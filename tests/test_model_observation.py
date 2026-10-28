"""使用确定性时钟验证埋点合同和操作识别，不访问外部服务。"""

import json
import tempfile
import unittest
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ag_ui.core import RunAgentInput
from pydantic import ValidationError

from app.contracts import InferenceInput
from app.model_call_logging import print_record_json
from app.model_observation import ModelObservation
from app.observation_models import ExecutionRecord, ModelUsage
from app.operation_detection import detect_operation
from tests.helpers import chunk, envelope, settings


class ObservationTests(unittest.TestCase):
    """验证 Token 与时间取值，不以实现细节替代合同断言。"""

    def setUp(self):
        """创建隔离配置与可独立推进的本地观测时钟。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = settings(Path(self.temporary.name))
        self.tick = 0.0

    def clock(self):
        """返回测试控制的单调时间。"""
        return self.tick

    def observation(self, streaming=True, metadata=None):
        """构造真实输入观测，不发送网络请求。"""
        raw = envelope(streaming=streaming, metadata=metadata)
        return ModelObservation(RunAgentInput.model_validate(raw),
                                InferenceInput.model_validate(raw["forwardedProps"]["modelInference"]),
                                self.config, clock=self.clock)

    def model(self, observation):
        """读取正式输出合同中的模型字段。"""
        return observation.snapshot("completed", None)["data"]["model"]

    def test_all_contract_fields_optional_and_source_status_only_null(self):
        """空合同有效，显式用量来源状态只接受 null。"""
        self.assertEqual(ExecutionRecord().model_dump(exclude_unset=True), {})
        self.assertEqual(ExecutionRecord.model_validate({"identity": {}, "data": {}}).model_dump(exclude_unset=True),
                         {"identity": {}, "data": {}})
        for field in ("usage_source", "usage_status"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ModelUsage.model_validate({field: "provider"})

    def test_identity_is_request_envelope_not_metadata_session_or_request(self):
        """不将元数据 sessionId/requestId 冒充请求中的 threadId/runId。"""
        observation = self.observation(metadata={
            "userId": "u1", "projectId": "app1", "sessionId": "other-session", "requestId": "other-request",
            "extensions": {"conversation_id": "c1", "operation_id": "o1", "workflow_scope": "conversation"},
        })
        identity = observation.snapshot("completed", None)["identity"]
        self.assertEqual(identity, {"user_id": "u1", "project_id": "app1", "conversation_id": "c1",
                                    "operation_id": "o1", "workflow_scope": "conversation",
                                    "thread_id": "thread-1", "run_id": "run-1", "current_operation": "unknown"})
        empty = self.observation().snapshot("completed", None)
        self.assertIsNone(empty["identity"]["conversation_id"])
        self.assertIsNone(empty["data"]["trace_id"])
        self.assertNotIn("active_intervals", empty["data"])

    def test_operation_metadata_does_not_expose_service_credentials(self):
        """明确操作说明误填凭证时不复制或变形输出该凭证，改用安全识别结果。"""
        for secret in (self.config.service_token.get_secret_value(), self.config.direct_api_key.get_secret_value()):
            with self.subTest(secret_kind="configured credential"):
                observation = self.observation(metadata={"extensions": {"current_operation": secret}})
                record = observation.snapshot("completed", None)
                self.assertEqual(record["identity"]["current_operation"], "unknown")
                self.assertNotIn(secret, json.dumps(record))

    def test_stream_timing_excludes_role_finish_usage_and_late_delivery(self):
        """独立尾部用量帧不拉长首末生成间隔，结束后再次 end 不延长耗时。"""
        observation = self.observation()
        observation.begin()
        self.tick = 0.125
        observation.observe(chunk({"role": "assistant"}))
        self.tick = 0.25
        observation.observe(chunk({"content": " "}))
        self.tick = 1.0
        observation.observe(chunk({"content": "完成"}))
        self.tick = 1.25
        observation.observe(chunk(finish_reason="stop"))
        self.tick = 1.5
        observation.observe({"choices": [], "usage": {"prompt_tokens": 284, "completion_tokens": 45, "total_tokens": 329}})
        observation.end()
        self.tick = 99.0
        observation.end()
        model = self.model(observation)
        self.assertEqual((model["ttft_ms"], model["output_duration_ms"]), (250, 750))
        self.assertEqual(observation.snapshot("completed", None)["data"]["duration_ms"], 1500)
        self.assertEqual((model["input_tokens"], model["output_tokens"], model["total_tokens"]), (284, 45, 329))

    def test_beijing_fallback_crosses_day_without_changing_durations_or_unknown_times(self):
        """SDK 对象缺失也显示东八区；跨日保留微秒，耗时仍来自单调时钟。"""
        wall_times = iter(["2026-10-27T23:59:59.123456+00:00", "2026-10-28T00:00:00.123456+00:00"])
        raw = envelope()
        observation = ModelObservation(
            RunAgentInput.model_validate(raw),
            InferenceInput.model_validate(raw["forwardedProps"]["modelInference"]),
            self.config, clock=self.clock, wall_clock=lambda: next(wall_times),
        )
        before = observation.snapshot("failed", None)["data"]
        self.assertIsNone(before["started_at"])
        self.assertIsNone(before["ended_at"])
        observation.begin()
        self.tick = 0.25
        observation.observe(chunk({"content": "first"}))
        self.tick = 0.75
        observation.observe(chunk({"content": "last"}))
        self.tick = 1.0
        observation.end()
        data = observation.snapshot("completed", None)["data"]
        self.assertEqual(data["started_at"], "2026-10-28T07:59:59.123456+08:00")
        self.assertEqual(data["ended_at"], "2026-10-28T08:00:00.123456+08:00")
        self.assertEqual(datetime.fromisoformat(data["started_at"]), datetime.fromisoformat(observation.started_at))
        self.assertEqual((data["duration_ms"], data["model"]["ttft_ms"], data["model"]["output_duration_ms"]), (1000, 250, 500))

    def test_tool_and_reasoning_output_count_but_tool_id_does_not(self):
        """模型生成工具名称、参数和推理都算输出，工具 ID 不算生成内容。"""
        observation = self.observation()
        observation.begin()
        self.tick = 0.125
        observation.observe(chunk({"tool_calls": [{"index": 0, "id": "call-1", "type": "function"}]}))
        self.tick = 0.25
        observation.observe(chunk({"tool_calls": [{"index": 0, "function": {"name": "read_file"}}]}))
        self.tick = 0.5
        observation.observe(chunk({"reasoning_content": "推理"}))
        self.tick = 1.0
        observation.observe(chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}))
        observation.end()
        self.assertEqual((self.model(observation)["ttft_ms"], self.model(observation)["output_duration_ms"]), (250, 750))

    def test_nonstream_single_chunk_and_no_output_have_correct_null_times(self):
        """非流式、单片和纯用量三类调用不能虚构流式输出持续时间。"""
        for streaming, response, expected_ttft in (
            (False, chunk({"content": "正文"}), None),
            (True, chunk({"content": "正文"}), 250),
            (True, {"choices": [], "usage": {"total_tokens": 1}}, None),
        ):
            with self.subTest(streaming=streaming, response=response):
                self.tick = 0.0
                observation = self.observation(streaming)
                observation.begin()
                self.tick = 0.25
                observation.observe(response)
                observation.end()
                model = self.model(observation)
                self.assertEqual(model["ttft_ms"], expected_ttft)
                self.assertIsNone(model["output_duration_ms"])

    def test_latest_partial_snapshot_not_sum_or_cross_snapshot_merge(self):
        """累计统计不相加，后续部分统计不与旧快照拼造完整用量。"""
        observation = self.observation()
        observation.observe({"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}})
        observation.observe({"usage": {"prompt_tokens": 40}})
        observation.observe({"choices": []})
        model = self.model(observation)
        self.assertEqual(model["input_tokens"], 40)
        self.assertIsNone(model["output_tokens"])
        self.assertIsNone(model["total_tokens"])
        self.assertIsNone(model["usage_source"])
        self.assertIsNone(model["usage_status"])

    def test_unknown_invalid_and_zero_usage_are_not_confused(self):
        """负数、布尔及字符串无效，上游真实零值有效。"""
        for usage, expected in (
            ({}, (None, None, None)),
            ({"prompt_tokens": -1, "completion_tokens": True, "total_tokens": "3"}, (None, None, None)),
            ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, (0, 0, 0)),
            ({"prompt_tokens": 1, "completion_tokens": 2}, (1, 2, None)),
        ):
            with self.subTest(usage=usage):
                observation = self.observation()
                observation.observe({"usage": usage})
                model = self.model(observation)
                self.assertEqual(tuple(model[key] for key in ("input_tokens", "output_tokens", "total_tokens")), expected)

    def test_inconsistent_total_becomes_unknown_without_changing_raw_usage(self):
        """总数冲突只影响摘要总数，不修改原始用量。"""
        observation = self.observation()
        usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 99}
        observation.observe({"usage": usage})
        with self.assertLogs("app.model_observation", level="WARNING"):
            self.assertIsNone(self.model(observation)["total_tokens"])
        self.assertEqual(observation.usage, usage)

    def test_error_summary_never_echoes_provider_prompt_or_source(self):
        """埋点仅使用安全摘要，不回显模型错误原文和源码。"""
        observation = self.observation()
        record = observation.snapshot("failed", {"code": "UPSTREAM_AUTH_ERROR", "message": "SECRET_PROMPT and source"})
        self.assertEqual(record["data"]["error"]["layer"], "model")
        self.assertNotIn("SECRET_PROMPT", json.dumps(record))
        self.assertIsNone(observation.snapshot("cancelled", {"code": "REQUEST_CANCELLED"})["data"]["error"])


class OperationAndConsoleTests(unittest.TestCase):
    """验证英文操作标识位于 identity，并发输出保持每次一行。"""

    def test_operation_uses_current_role_or_explicit_string(self):
        """优先明确扩展，否则使用固定角色开头，不猜测普通业务关键词。"""
        request = {"messages": [{"role": "system", "content": "You are the technical-planning model for an app-generation workflow.\n规则"}]}
        self.assertEqual(detect_operation(request, {}), "generate_technical_plan")
        self.assertEqual(detect_operation(request, {"current_operation": "校验后修复\n代码"}), "modify_code")
        self.assertEqual(detect_operation({"messages": [{"role": "user", "content": "修复技术规划"}]}, {}), "unknown")

    def test_operation_ignores_history_quotes_tool_results_and_conflicts(self):
        """历史引用和工具内容不得成为当前动作，冲突的系统角色返回未知。"""
        signature = "You are the Frontend Coding Agent."
        for messages in (
            [{"role": "user", "content": "历史说明：" + signature}],
            [{"role": "tool", "content": signature}],
            [{"role": "user", "content": "你好"}, {"role": "assistant", "content": signature}],
            [{"role": "system", "content": signature}, {"role": "system", "content": "You are the Java Backend Coding Agent."}],
        ):
            with self.subTest(messages=messages):
                self.assertEqual(detect_operation({"messages": messages}, {}), "unknown")

    def test_console_keeps_concurrent_records_single_line_and_escapes_newlines(self):
        """多线程每条只输出一行 JSON，身份中的换行不能破坏记录边界。"""
        output = StringIO()

        def write_record(index):
            """模拟并发请求的单行控制台输出。"""
            print_record_json(json.dumps({"identity": {"thread_id": "t\nquoted", "run_id": f"r{index}",
                                                       "current_operation": f"operation_{index}"},
                                          "data": {"span_id": f"s{index}"}}, ensure_ascii=False))

        with redirect_stdout(output), ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_record, range(40)))
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 40)
        for line in lines:
            record = json.loads(line)
            suffix = record["identity"]["run_id"][1:]
            self.assertEqual(record["identity"]["current_operation"], f"operation_{suffix}")
            self.assertEqual(record["data"]["span_id"], f"s{suffix}")

    def test_console_write_failure_reaches_output_manager(self):
        """底层控制台错误保留给共享输出组件隔离并计数，不在此吞掉失败。"""
        with patch("app.model_call_logging.sys.stdout") as output:
            output.write.side_effect = OSError("closed console")
            with self.assertRaises(OSError):
                print_record_json('{"identity":{},"data":{}}')
