"""验证任务回复的受控提取，不允许截断、嵌套或多候选绕过原 Parser。"""

import json
import unittest

from app.services.unit_candidate_parser import (
    RawUnitCandidateParseError,
    parse_raw_unit_candidate,
)


class UnitCandidateEnvelopeTests(unittest.TestCase):
    """通过真实 Parser 检查文本包装与任务数据之间的边界。"""

    def _error(self, text: str) -> RawUnitCandidateParseError:
        """返回严格解析错误，确保失败时不会返回部分任务。"""

        with self.assertRaises(RawUnitCandidateParseError) as caught:
            parse_raw_unit_candidate(text, unit_id="page:orders")
        return caught.exception

    def test_commentary_keeps_task_values_and_records_wrapper_size(self) -> None:
        """中文说明、字符串内括号与转义不能改变候选数据或边界。"""

        tasks = [{"id": "task-orders", "description": '路径 {todoId}，说明 "原样保留"；数组 [x]。'}]
        body = json.dumps({"tasks": tasks}, ensure_ascii=False)
        prefix = "已读取合同 {todoId}，响应字段为 {id: string}。\n"
        suffix = "\n以上是最终任务。"
        metadata: dict[str, object] = {}
        raw = prefix + body + suffix
        result = parse_raw_unit_candidate(raw, unit_id="page:orders", parse_metadata=metadata)
        self.assertEqual(result, tasks)
        self.assertEqual(raw, prefix + body + suffix)
        self.assertEqual(metadata["candidate_response_format"], "extracted_json")
        self.assertEqual(metadata["candidate_wrapper_chars"], len(prefix) + len(suffix))

    def test_fenced_json_with_explanation_is_accepted(self) -> None:
        """围栏外说明不会触发整轮重试，任务内容仍按原 Parser 校验。"""

        fence = chr(96) * 3
        raw = f'以下是任务。\n{fence}json\n{{"tasks":[{{"id":"task-1"}}]}}\n{fence}\n完成。'
        self.assertEqual(parse_raw_unit_candidate(raw, unit_id="page:orders")[0]["id"], "task-1")

    def test_long_analysis_and_other_complete_objects_are_not_task_data(self) -> None:
        """长分析、路径占位及非任务示意对象不加入任务数组。"""

        prefix = '分析合同。\n{"endpoint":"/api/orders/{id}"}\n' + "规划分析。\n" * 5_000
        tasks = [{"id": "task-final"}]
        raw = prefix + json.dumps({"tasks": tasks})
        self.assertEqual(parse_raw_unit_candidate(raw, unit_id="page:orders"), tasks)

    def test_multiple_candidates_are_rejected_even_when_identical(self) -> None:
        """多个合法 tasks 根不能挑选首个、末个或按内容去重后冒充唯一结果。"""

        first = '{"tasks":[{"id":"task-1"}]}'
        for second in (first, '{"tasks":[{"id":"task-2"}]}'):
            with self.subTest(second=second):
                self.assertEqual(self._error("说明\n" + first + "\n" + second).issues[0].code,
                                 "RAW_CANDIDATE_JSON_MALFORMED")

    def test_truncated_or_nested_containers_are_not_salvaged(self) -> None:
        """外层容器损坏时，不能扫描其中合法的 tasks 子对象作为完整候选。"""

        cases = (
            '说明\n{"outer":{"tasks":[{"id":"task-1"}]}',
            '说明\n[{"tasks":[{"id":"task-1"}]}',
            '说明\n{"outer":[{"tasks":[{"id":"task-1"}]}}',
            '说明\n{"outer":{"tasks":[{"id":"task-1"}]}}',
            '说明\n[{"tasks":[{"id":"task-1"}]}]',
            '说明\n{"tasks":[{"id":"task-1"}',
            '说明\n{"tasks":[{"id":"task-1"}]}\n{"unfinished":',
            '说明\n{"tasks":[{"id":"broken",}]}\n{"tasks":[{"id":"task-1"}]}',
            '说明\n{"\\u0074asks":[{"id":"broken"},]}\n{"tasks":[{"id":"task-1"}]}',
            '说明\n{tasks:[{"id":"broken"},]}\n{"tasks":[{"id":"task-1"}]}',
            '说明\n{\'tasks\':[{"id":"broken"},]}\n{"tasks":[{"id":"task-1"}]}',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self._error(raw).issues[0].code, "RAW_CANDIDATE_JSON_MALFORMED")

    def test_nested_example_prevents_choosing_another_candidate(self) -> None:
        """出现包含任务对象的示例包裹时，不能忽略其存在再挑选另一个结果。"""

        raw = '示例\n{"example":{"tasks":[]}}\n最终\n{"tasks":[{"id":"task-1"}]}'
        self.assertEqual(self._error(raw).issues[0].code, "RAW_CANDIDATE_JSON_MALFORMED")

    def test_wrapping_does_not_hide_duplicate_keys_or_invalid_constants(self) -> None:
        """提取后依旧由严格解码器拒绝重复字段及非标准数值。"""

        cases = (
            ('{"tasks":[],"tasks":[{"id":"task-1"}]}', "RAW_CANDIDATE_JSON_KEY_DUPLICATE"),
            ('{"tasks":[{"id":"a","id":"b"}]}', "RAW_CANDIDATE_JSON_KEY_DUPLICATE"),
            ('{"tasks":[{"id":"task-1","value":NaN}]}', "RAW_CANDIDATE_JSON_CONSTANT_INVALID"),
            ('{"tasks":[{"id":"task-1","value":Infinity}]}', "RAW_CANDIDATE_JSON_CONSTANT_INVALID"),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(self._error("说明\n" + body + "\n结束").issues[0].code, expected)

    def test_wrapping_does_not_remove_unsupported_fields_or_invalid_tasks(self) -> None:
        """对象内字段、错误任务类型及任务 ID 均保持原有错误，不能被包装处理吞掉。"""

        cases = (
            ('{"tasks":[],"analysis":"不允许"}', "RAW_CANDIDATE_ENVELOPE_UNSUPPORTED"),
            ('{"tasks":{}}', "RAW_CANDIDATE_TASKS_TYPE_INVALID"),
            ('{"tasks":[{"id":"a"},"bad"]}', "RAW_CANDIDATE_TASK_TYPE_INVALID"),
            ('{"tasks":[{"id":1}]}', "RAW_CANDIDATE_TASK_ID_TYPE_INVALID"),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(self._error("说明\n" + body).issues[0].code, expected)

    def test_complete_wrong_root_is_not_replaced_by_nested_tasks(self) -> None:
        """原响应本身是完整 JSON 时，保留原顶层类型和额外字段错误。"""

        cases = (
            ('[{"tasks":[{"id":"task-1"}]}]', "RAW_CANDIDATE_ENVELOPE_TYPE_INVALID"),
            ('{"wrapper":{"tasks":[]}}', "RAW_CANDIDATE_TASKS_MISSING"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self._error(raw).issues[0].code, expected)

    def test_direct_json_and_existing_complete_root_repair_are_retained(self) -> None:
        """原有直接 JSON 路径及完整根语法修复不因新增包装处理而改变。"""

        metadata: dict[str, object] = {}
        self.assertEqual(parse_raw_unit_candidate(' {"tasks":[{"id":"task-1"}]} ',
                         unit_id="page:orders", parse_metadata=metadata)[0]["id"], "task-1")
        self.assertEqual(metadata["candidate_response_format"], "direct_json")
        self.assertEqual(metadata["candidate_wrapper_chars"], 0)
        tasks = parse_raw_unit_candidate('{"tasks":[{"id":"task-1","description":"显示 "标题"。"}]}',
                                         unit_id="page:orders", parse_metadata=metadata)
        self.assertEqual(tasks[0]["description"], '显示 "标题"。')
        self.assertEqual(metadata["candidate_response_format"], "repaired_json")
