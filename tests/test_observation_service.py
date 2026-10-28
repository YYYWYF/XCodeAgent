"""通过真实 FastAPI、OpenAI SDK 和模拟 HTTP 验证模型埋点全流程。"""

import asyncio
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from app.contracts import InferenceInput
from app.main import create_app
from app.model_observation import ModelObservation
from app.tracing import TraceRecorder
from app.upstream import Upstream
from tests.helpers import chunk, completion, envelope, read_events, settings, sse_response


class ObservationServiceTests(unittest.TestCase):
    """验证响应合同不变、打印唯一性以及真实 SDK 的统计边界。"""

    def setUp(self):
        """使用临时数据库，所有 SDK HTTP 请求由离线传输拦截。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)

    def app(self, handler, **overrides):
        """创建注入 MockTransport 的完整服务。"""
        config = settings(self.path, **overrides)
        return create_app(config, Upstream(config, httpx.MockTransport(handler)))

    def summaries(self, output):
        """解析每次调用唯一的控制台 JSON，动作说明已包含在 identity。"""
        return [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{"identity":')]

    def test_tail_usage_printed_once_saved_and_not_added_to_agui(self):
        """等待独立用量尾帧，日志不重复、不改变模型协议，并保存同份摘要。"""
        captured = []

        def handler(request):
            """提供内容结束之后才到达的 usage，确认统计开关实际发出。"""
            captured.append(json.loads(request.content))
            tail = chunk(usage={"prompt_tokens": 284, "completion_tokens": 45, "total_tokens": 329})
            tail["choices"] = []
            return sse_response([chunk({"role": "assistant"}), chunk({"content": "你"}),
                                 chunk({"content": "好"}), chunk(finish_reason="stop"), tail])

        app = self.app(handler)
        raw = envelope("incoming-run", metadata={"sessionId": "different-session", "requestId": "different-request"})
        raw["threadId"] = "incoming-thread"
        raw["forwardedProps"]["modelInference"]["request"]["stream_options"] = {"include_usage": False}
        raw["forwardedProps"]["modelInference"]["request"]["messages"] = [
            {"role": "system", "content": "You are the technical-planning model for an app-generation workflow."},
            {"role": "user", "content": "PROMPT_MUST_NOT_BE_LOGGED"},
        ]
        ticks = iter([0.0, 0.25, 1.0, 1.5])

        def observation_factory(*args):
            """将网络调用的真实采集位置绑定到确定性时钟。"""
            return ModelObservation(*args, clock=lambda: next(ticks))

        output = StringIO()
        with patch("app.inference.ModelObservation", side_effect=observation_factory), redirect_stdout(output), TestClient(app) as client:
            events = read_events(client, raw)
        summaries = self.summaries(output)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        record = summaries[0]
        self.assertEqual(record["identity"]["run_id"], "incoming-run")
        self.assertEqual(record["identity"]["thread_id"], "incoming-thread")
        self.assertEqual(record["data"]["model"]["total_tokens"], 329)
        self.assertEqual(record["data"]["model"]["ttft_ms"], 250)
        self.assertEqual(record["data"]["model"]["output_duration_ms"], 750)
        self.assertEqual(record["data"]["duration_ms"], 1500)
        self.assertTrue(captured[0]["stream_options"]["include_usage"])
        self.assertEqual(captured[0]["messages"], raw["forwardedProps"]["modelInference"]["request"]["messages"])
        self.assertEqual(record["identity"]["current_operation"], "generate_technical_plan")
        self.assertNotIn("PROMPT_MUST_NOT_BE_LOGGED", output.getvalue())
        self.assertNotIn("current_operation", record)
        self.assertNotIn("current_operation", record["data"])
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertFalse(any(event.get("name") == "execution_trace" for event in events))
        self.assertEqual(app.state.history.get("incoming-run")["analysis"]["execution_trace"], record)
        self.assertTrue(record["data"]["started_at"].endswith("+08:00"))
        self.assertTrue(record["data"]["ended_at"].endswith("+08:00"))
        self.assertEqual(app.state.history.get("incoming-run")["input_context"], captured[0])

    def test_nonstream_uses_requested_model_and_null_stream_timing(self):
        """模型名取实际发送的配置，而非上游可能返回的别名。"""
        def handler(request):
            """返回与配置模型名不同的上游响应别名。"""
            return httpx.Response(200, json=completion())

        app, output = self.app(handler), StringIO()
        with redirect_stdout(output), TestClient(app) as client:
            read_events(client, envelope(streaming=False))
        record = self.summaries(output)[0]
        model = record["data"]["model"]
        self.assertEqual(model["name"], app.state.settings.active_model)
        self.assertEqual(model["provider"], "direct")
        self.assertIsNone(model["ttft_ms"])
        self.assertIsNone(model["output_duration_ms"])
        self.assertIsNone(model["usage_source"])
        self.assertIsNone(model["usage_status"])

    def test_tool_payloads_are_forwarded_without_separate_observation(self):
        """工具定义、历史结果和新指令照常透传，每次推理只打印一组固定模型记录。"""
        captured = []

        def handler(request):
            """保留上游实际收到的工具上下文，并返回新的工具调用指令。"""
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=completion())

        app, output = self.app(handler), StringIO()
        raw = envelope(streaming=False, metadata={"extensions": {"current_operation": "处理工具结果"}})
        request = raw["forwardedProps"]["modelInference"]["request"]
        request["tools"] = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
        request["messages"] = [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "previous-call", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "previous-call", "content": "TOOL_RESULT_PRIVATE"},
            {"role": "user", "content": "继续"}]
        with redirect_stdout(output), TestClient(app) as client:
            events = read_events(client, raw)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(len(self.summaries(output)), 1)
        data = self.summaries(output)[0]["data"]
        self.assertEqual((data["span_kind"], data["run_name"]), ("model", "model-inference"))
        self.assertEqual(captured[0]["messages"], request["messages"])
        self.assertEqual(captured[0]["tools"], request["tools"])
        result = next(event["value"] for event in events if event.get("name") == "model-inference.result")
        self.assertEqual(result["choices"][0]["message"]["tool_calls"], completion()["choices"][0]["message"]["tool_calls"])
        self.assertNotIn("tool_observations", app.state.history.get("run-1")["analysis"])
        self.assertNotIn("TOOL_RESULT_PRIVATE", output.getvalue())

    def test_rejections_and_information_calls_do_not_print_summaries(self):
        """健康、目录、鉴权及输入拒绝不产生模型统计。"""
        def handler(request):
            """被拒绝请求绝不能到达上游。"""
            raise AssertionError("unexpected upstream")

        app, output = self.app(handler), StringIO()
        with redirect_stdout(output), TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["status"], "ok")
            self.assertEqual(client.post("/model-info/run", json=envelope()).status_code, 401)
            self.assertEqual(client.post("/model-info/run", json=envelope(), headers={"Authorization": "Bearer local-model-transmit-dev-token"}).status_code, 200)
            raw = envelope()
            raw["forwardedProps"]["modelInference"]["request"]["messages"] = []
            self.assertEqual(client.post("/model-inference/run", json=raw, headers={"Authorization": "Bearer local-model-transmit-dev-token"}).status_code, 422)
        self.assertEqual(output.getvalue(), "")

    def test_sdk_retry_prints_one_summary_and_duplicate_does_not_print(self):
        """SDK 连接重试归属同次推理，重复 runId 被拒绝后不重复打印。"""
        attempts = []

        def handler(request):
            """第一次连接失败，SDK 第二次尝试成功。"""
            attempts.append(request)
            if len(attempts) == 1:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, json=completion())

        app, output = self.app(handler), StringIO()
        raw = envelope(streaming=False)
        raw["forwardedProps"]["modelInference"]["maxRetries"] = 1
        with redirect_stdout(output), TestClient(app) as client:
            read_events(client, raw)
            duplicate = client.post("/model-inference/run", json=raw, headers={"Authorization": "Bearer local-model-transmit-dev-token"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.summaries(output)), 1)
        self.assertEqual(self.summaries(output)[0]["data"]["model"]["total_tokens"], 5)

    def test_http_timeout_and_incomplete_response_all_print_failure(self):
        """认证失败、超时和缺失结束原因均输出失败，且不泄露错误原文。"""
        for scenario, expected in (("auth", "UPSTREAM_AUTH_ERROR"), ("timeout", "UPSTREAM_TIMEOUT"), ("incomplete", "UPSTREAM_PROTOCOL_ERROR")):
            with self.subTest(scenario=scenario):
                def handler(request):
                    """模拟不同上游失败，避免真实网络和真实密钥。"""
                    if scenario == "timeout":
                        raise httpx.ReadTimeout("SECRET_PROMPT", request=request)
                    if scenario == "auth":
                        return httpx.Response(401, json={"error": {"message": "SECRET_PROMPT test-direct-secret"}})
                    return sse_response([chunk({"content": "partial"})])

                app, output = self.app(handler), StringIO()
                with redirect_stdout(output), TestClient(app) as client:
                    read_events(client, envelope(f"r-{scenario}"))
                record = self.summaries(output)[0]
                self.assertEqual(record["data"]["status"], "failed")
                self.assertEqual(record["data"]["error"]["code"], expected)
                self.assertNotIn("SECRET_PROMPT", output.getvalue())
                self.assertNotIn("test-direct-secret", output.getvalue())

    def test_print_failure_does_not_change_result_or_local_history(self):
        """日志模块故障时推理与既有 SQLite 历史继续完成。"""
        def handler(request):
            """提供正常模型结果。"""
            return httpx.Response(200, json=completion())

        app = self.app(handler)
        with patch("app.model_call_logging.print_record_json", side_effect=OSError("closed console")), TestClient(app) as client:
            events = read_events(client, envelope(streaming=False))
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(app.state.observation_output.stats()["failed"], 1)
        self.assertEqual(app.state.history.get("run-1")["status"], "completed")
        self.assertEqual(app.state.history.get("run-1")["analysis"]["execution_trace"]["data"]["status"], "completed")

    def test_langsmith_sdk_ids_exist_without_upload_or_credentials(self):
        """真实 SDK 无凭证本地生成身份，不上传也保留根关系和模型成功结果。"""
        config = settings(self.path)

        def handler(request):
            """模型请求仍然由离线 HTTP 正常完成。"""
            return httpx.Response(200, json=completion())

        app = create_app(config, Upstream(config, httpx.MockTransport(handler)), TraceRecorder(config))
        output = StringIO()
        with patch("langsmith.run_trees.get_cached_client", side_effect=AssertionError("unexpected client")), \
             patch("langsmith.RunTree.post", side_effect=AssertionError("unexpected upload")), \
             patch("langsmith.RunTree.patch", side_effect=AssertionError("unexpected upload")), \
             redirect_stdout(output), TestClient(app) as client:
            read_events(client, envelope(streaming=False))
        data = self.summaries(output)[0]["data"]
        self.assertIsInstance(data["span_id"], str)
        self.assertNotEqual(data["span_id"], "run-1")
        self.assertEqual(data["trace_id"], data["span_id"])
        self.assertIsNone(data["parent_span_id"])
        self.assertEqual(data["status"], "completed")
        self.assertEqual(app.state.history.get("run-1")["langsmith_run_id"], data["span_id"])


class AsyncObservationServiceTests(unittest.IsolatedAsyncioTestCase):
    """直接控制上游异步流，验证取消和真实并发调用隔离。"""

    async def test_cancelled_stream_closes_and_prints_partial_observation(self):
        """关闭消费者时关闭实际 SDK 流，并保存已取得的时间和未知用量。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory))
            closed = []

            class Stream(httpx.AsyncByteStream):
                """阻塞尾部输出，允许测试显式关闭消费方。"""

                async def __aiter__(self):
                    """只产生一个有效文本分片。"""
                    yield ("data: " + json.dumps(chunk({"content": "partial"})) + "\n\n").encode()
                    await asyncio.Event().wait()

                async def aclose(self):
                    """记录真实网络流释放。"""
                    closed.append(True)

            def handler(request):
                """将官方 SDK 连接到测试流。"""
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

            app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
            self.addCleanup(app.state.observation_output.close)
            raw = envelope()
            run = RunAgentInput.model_validate(raw)
            call = InferenceInput.model_validate(raw["forwardedProps"]["modelInference"])
            events = app.state.service.events(run, call, app.state.service.reserve(run, call))
            output = StringIO()
            with redirect_stdout(output):
                async for event in events:
                    if getattr(event, "name", None) == "model-inference.chunk":
                        break
                await events.aclose()
                await asyncio.to_thread(app.state.observation_output.close)
            record = json.loads(output.getvalue().splitlines()[0])
            self.assertTrue(closed)
            self.assertEqual(record["data"]["status"], "cancelled")
            self.assertIsNotNone(record["data"]["ended_at"])
            self.assertIsNotNone(record["data"]["model"]["ttft_ms"])
            self.assertIsNone(record["data"]["model"]["total_tokens"])
            self.assertIsNone(record["data"]["error"])
            self.assertEqual(app.state.history.get("run-1")["status"], "cancelled")

    async def test_parallel_calls_do_not_share_identity_operation_or_usage(self):
        """多个真实异步模型请求交错时，各自用量、身份、动作仍准确关联。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory))

            async def handler(request):
                """按本次请求返回不同用量，模拟并行响应。"""
                body = json.loads(request.content)
                index = int(body["messages"][0]["content"])
                await asyncio.sleep(0.001)
                response = completion()
                response["usage"] = {"prompt_tokens": index, "completion_tokens": 1, "total_tokens": index + 1}
                return httpx.Response(200, json=response)

            app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
            self.addCleanup(app.state.observation_output.close)

            async def invoke(index):
                """每个任务构造独立请求，不设置进程级当前会话。"""
                raw = envelope(f"run-{index}", False, {"extensions": {"current_operation": f"operation_{index}"}})
                raw["threadId"] = f"thread-{index}"
                raw["forwardedProps"]["modelInference"]["request"]["messages"] = [{"role": "user", "content": str(index)}]
                run = RunAgentInput.model_validate(raw)
                call = InferenceInput.model_validate(raw["forwardedProps"]["modelInference"])
                async for _ in app.state.service.events(run, call, app.state.service.reserve(run, call)):
                    pass

            output = StringIO()
            with redirect_stdout(output):
                await asyncio.gather(*(invoke(index) for index in range(1, 9)))
                await asyncio.to_thread(app.state.observation_output.close)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 8)
            for line in lines:
                record = json.loads(line)
                index = int(record["identity"]["run_id"].split("-")[1])
                self.assertEqual(record["identity"]["thread_id"], f"thread-{index}")
                self.assertEqual(record["data"]["model"]["total_tokens"], index + 1)
                self.assertEqual((record["data"]["span_kind"], record["data"]["run_name"]), ("model", "model-inference"))
                self.assertEqual(record["identity"]["current_operation"], f"operation_{index}")
