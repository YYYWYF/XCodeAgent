"""T3.1 严格 Raw Unit Candidate Parser 回归。"""

from __future__ import annotations

import json
import unittest

from app.services.unit_candidate_parser import (
    RawUnitCandidateParseError,
    parse_raw_unit_candidate,
)


UNIT_ID = "page:orders"


class UnitCandidateParserTests(unittest.TestCase):
    def _parse_error(self, raw_text: object) -> RawUnitCandidateParseError:
        """解析失败时返回结构化异常，并断言问题均归因到当前 Unit。"""

        with self.assertRaises(RawUnitCandidateParseError) as caught:
            parse_raw_unit_candidate(raw_text, unit_id=UNIT_ID)  # type: ignore[arg-type]
        error = caught.exception
        self.assertTrue(error.issues)
        for issue in error.issues:
            self.assertEqual(issue.level, "unit")
            self.assertEqual(issue.category, "generation")
            self.assertEqual(issue.unit_ids, (UNIT_ID,))
            self.assertEqual(issue.retry_unit_ids, (UNIT_ID,))
            self.assertTrue(issue.retryable)
        return error

    def test_malformed_json_becomes_structured_issue(self) -> None:
        """截断 JSON 必须转为结构化问题，不能回退提取嵌套 object。"""

        error = self._parse_error('{"tasks": [{"id": "task-1"}')
        self.assertEqual(error.issues[0].code, "RAW_CANDIDATE_JSON_MALFORMED")

    def test_missing_tasks_is_rejected(self) -> None:
        """顶层缺少 tasks 时必须拒绝，不能当作空 Candidate。"""

        error = self._parse_error("{}")
        self.assertEqual(error.issues[0].code, "RAW_CANDIDATE_TASKS_MISSING")

    def test_non_object_task_is_not_dropped(self) -> None:
        """任一 task 非 object 时整个 Candidate 失败，不能静默删除坏项。"""

        raw = '{"tasks":[{"id":"task-1"},"malformed",{"id":"task-2"}]}'
        error = self._parse_error(raw)
        self.assertEqual(error.issues[0].code, "RAW_CANDIDATE_TASK_TYPE_INVALID")
        self.assertEqual(error.issues[0].details["index"], 1)

    def test_missing_task_id_is_rejected_without_synthesis(self) -> None:
        """缺失 ID 必须报错，Parser 不得按序号补 Task ID。"""

        error = self._parse_error('{"tasks":[{"owner":"frontend"}]}')
        self.assertEqual(error.issues[0].code, "RAW_CANDIDATE_TASK_ID_MISSING")
        self.assertEqual(error.issues[0].task_ids, ())

    def test_duplicate_task_id_is_rejected_without_rename_or_merge(self) -> None:
        """重复 ID 必须报错，Parser 不得 rename 或合并任务。"""

        error = self._parse_error(
            '{"tasks":[{"id":"task-1"},{"id":"task-1"}]}'
        )
        self.assertEqual(error.issues[0].code, "RAW_CANDIDATE_TASK_ID_DUPLICATE")
        self.assertEqual(error.issues[0].task_ids, ("task-1",))

    def test_extra_unsupported_envelope_is_rejected(self) -> None:
        """新 Candidate 顶层只接受 tasks，不兼容旧 workspace_analysis 或 dag。"""

        error = self._parse_error(
            '{"workspace_analysis":{},"tasks":[{"id":"task-1"}]}'
        )
        self.assertEqual(error.issues[0].code, "RAW_CANDIDATE_ENVELOPE_UNSUPPORTED")
        self.assertEqual(
            error.issues[0].details["unsupported_keys"],
            ("workspace_analysis",),
        )

    def test_wrong_types_are_rejected(self) -> None:
        """原始值、envelope、tasks 与 Task ID 的错误类型均不得隐式转换。"""

        cases = (
            (None, "RAW_CANDIDATE_TEXT_TYPE_INVALID"),
            ("[]", "RAW_CANDIDATE_ENVELOPE_TYPE_INVALID"),
            ('{"tasks":{}}', "RAW_CANDIDATE_TASKS_TYPE_INVALID"),
            ('{"tasks":[{"id":1}]}', "RAW_CANDIDATE_TASK_ID_TYPE_INVALID"),
        )
        for raw_text, code in cases:
            with self.subTest(code=code):
                self.assertEqual(self._parse_error(raw_text).issues[0].code, code)

    def test_valid_candidate_preserves_raw_tasks_exactly(self) -> None:
        """合法 Candidate 返回原始任务顺序和字段，不改 owner 或补默认字段。"""

        expected = [
            {
                "id": "task-api",
                "owner": "api",
                "dependencies": [],
                "custom_field": {"items": [1, True, None]},
            },
            {"id": "task-page", "owner": "frontend"},
        ]
        raw_text = json.dumps({"tasks": expected}, ensure_ascii=False)

        parsed = parse_raw_unit_candidate(raw_text, unit_id=UNIT_ID)

        self.assertEqual(parsed, expected)
        self.assertEqual([task["id"] for task in parsed], ["task-api", "task-page"])
        self.assertEqual(parsed[0]["owner"], "api")
        self.assertNotIn("unit_id", parsed[0])


if __name__ == "__main__":
    unittest.main()
