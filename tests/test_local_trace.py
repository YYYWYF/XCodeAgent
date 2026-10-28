"""验证本地 LangSmith 对象优先取值且不会触发远程上报。"""

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient
from langsmith import RunTree, tracing_context

from app.contracts import InferenceInput
from app.main import create_app
from app.model_observation import ModelObservation
from app.tracing import TraceRecorder
from app.upstream import Upstream
from tests.helpers import chunk, completion, envelope, read_events, settings


class LocalTraceTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 SDK 对象与离线 HTTP 验证追踪来源和无上传边界。"""

    def setUp(self):
        """准备隔离配置和可控制的本地时钟。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = settings(Path(self.temporary.name))
        self.tick = 0.0

    def clock(self):
        """返回用例控制的单调时钟。"""
        return self.tick

    def observation(self):
        """创建真实请求对应的观测实例，不连接任何外部服务。"""
        raw = envelope()
        return ModelObservation(
            RunAgentInput.model_validate(raw),
            InferenceInput.model_validate(raw["forwardedProps"]["modelInference"]),
            self.config,
            clock=self.clock,
        )

    def prohibit_uploads(self):
        """禁止所有 LangSmith 客户端创建与显式上传入口。"""
        stack = ExitStack()
        for target in (
            "langsmith.Client", "langsmith.run_trees.get_cached_client",
            "langsmith.RunTree.post", "langsmith.RunTree.patch",
        ):
            stack.enter_context(patch(target, side_effect=AssertionError("禁止远程追踪请求")))
        return stack

    def summary(self, output):
        """提取控制台中唯一的模型 JSON 摘要。"""
        records = [json.loads(line) for line in output.getvalue().splitlines()
                   if line.startswith('{"identity":')]
        self.assertEqual(len(records), 1)
        return records[0]

    async def test_default_recorder_builds_local_ids_without_key_or_upload(self):
        """默认配置即有本地身份，外部启用追踪的环境不触发 Client 或上传。"""
        with self.prohibit_uploads(), patch.dict(os.environ, {
            "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true",
            "LANGSMITH_API_KEY": "fake-key-must-not-be-used",
        }):
            recorder = TraceRecorder(self.config)
            span_id, tree = await recorder.start(
                {"model": self.config.active_model, "messages": []},
                {"upstream": "direct", "model": self.config.active_model},
            )
            self.assertIsInstance(tree, RunTree)
            self.assertEqual(span_id, str(tree.id))
            self.assertEqual(tree.trace_id, tree.id)
            self.assertIsNone(tree.parent_run_id)
            await recorder.finish(tree, completion(), "completed", None)
            self.assertIsNone(tree.ls_client)
            self.assertEqual(tree.outputs["usage"]["total_tokens"], 5)
            self.assertEqual(tree.metadata["status"], "completed")

    async def test_local_root_does_not_inherit_ambient_parent_or_remote_switch(self):
        """外层追踪已开启也保持独立本地根，旧开关开启无需凭证。"""
        config = settings(Path(self.temporary.name), langsmith_enabled=True)
        with self.prohibit_uploads():
            parent = RunTree(name="external-parent", run_type="chain", replicas=[])
            with tracing_context(parent=parent, enabled=True):
                span_id, tree = await TraceRecorder(config).start({}, {})
            self.assertEqual(span_id, str(tree.trace_id))
            self.assertNotEqual(tree.trace_id, parent.trace_id)
            self.assertIsNone(tree.parent_run_id)
            self.assertEqual(tree.replicas, [])
            app = create_app(config)
            with TestClient(app) as client:
                health = client.get("/health").json()
            self.assertEqual(health["status"], "ok")
            self.assertFalse(health["langsmithEnabled"])
            self.assertEqual(health["langsmithMode"], "local")

    def test_snapshot_prefers_trace_facts_except_fixed_model_labels(self):
        """时间、用量、状态读取最新对象，类型与职责名称按模型服务合同固定。"""
        observation = self.observation()
        observation.begin()
        self.tick = 0.1
        observation.observe(chunk({"content": "local"}, usage={
            "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
        }))
        self.tick = 0.2
        observation.end()
        root = RunTree(name="root", run_type="chain", replicas=[])
        tree = RunTree(name="old-name", run_type="llm", parent_run=root, replicas=[])
        observation.set_trace(tree)
        started = datetime(2026, 10, 27, 1, 2, 3, tzinfo=timezone.utc)
        tree.start_time = started
        tree.end_time = started + timedelta(milliseconds=640)
        tree.name = "需求权限判断"
        tree.add_metadata({
            "ls_provider": "gateway", "ls_model_name": "traced-request-model",
            "duration_ms": 610, "ttft_ms": 90, "output_duration_ms": 400,
            "status": "failed",
            "execution_error": {"code": "UPSTREAM_TIMEOUT", "message": "SECRET_PROMPT"},
        })
        tree.set(usage_metadata={"input_tokens": 7, "output_tokens": 4, "total_tokens": 11})
        data = observation.snapshot("completed", None)["data"]
        self.assertEqual(data["span_id"], str(tree.id))
        self.assertEqual(data["trace_id"], str(root.id))
        self.assertEqual(data["parent_span_id"], str(root.id))
        self.assertEqual((data["span_kind"], data["run_name"]), ("model", "model-inference"))
        self.assertEqual(data["started_at"], "2026-10-27T09:02:03+08:00")
        self.assertEqual(data["ended_at"], "2026-10-27T09:02:03.640000+08:00")
        self.assertEqual(datetime.fromisoformat(data["started_at"]), started)
        self.assertEqual(datetime.fromisoformat(data["ended_at"]), tree.end_time)
        self.assertEqual(data["duration_ms"], 610)
        self.assertEqual(data["status"], "failed")
        model = data["model"]
        self.assertEqual((model["provider"], model["name"]), ("gateway", "traced-request-model"))
        self.assertEqual((model["input_tokens"], model["output_tokens"], model["total_tokens"]), (7, 4, 11))
        self.assertEqual((model["ttft_ms"], model["output_duration_ms"]), (90, 400))
        self.assertIsNone(model["usage_source"])
        self.assertIsNone(model["usage_status"])
        self.assertEqual(data["error"]["code"], "UPSTREAM_TIMEOUT")
        self.assertNotIn("SECRET_PROMPT", json.dumps(data))

    def test_partial_trace_usage_does_not_merge_with_older_local_snapshot(self):
        """追踪对象含部分有效用量时保留其整体快照，真实零值不被本地值覆盖。"""
        observation = self.observation()
        observation.observe({"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
        tree = RunTree(name="partial", run_type="llm", replicas=[])
        observation.set_trace(tree)
        tree.set(usage_metadata={"input_tokens": 0})
        model = observation.snapshot("completed", None)["data"]["model"]
        self.assertEqual(model["input_tokens"], 0)
        self.assertIsNone(model["output_tokens"])
        self.assertIsNone(model["total_tokens"])

    def test_model_labels_and_usage_remain_available_without_trace(self):
        """SDK 对象缺失时，固定模型标签和已经收到的真实用量仍然保留。"""
        observation = self.observation()
        observation.observe({"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
        data = observation.snapshot("completed", None)["data"]
        self.assertEqual((data["span_kind"], data["run_name"]), ("model", "model-inference"))
        self.assertEqual(data["model"]["total_tokens"], 5)

    async def test_finish_updates_trace_before_console_snapshot(self):
        """本地收尾后才打印，日志与 SQLite 使用收尾对象的最新内容。"""
        class FinishedRecorder(TraceRecorder):
            """在正常本地收尾后添加可识别的最终追踪事实。"""

            async def finish(self, tree, output, status, error):
                """改变最终对象字段，以验证打印不是提前生成的旧快照。"""
                await super().finish(tree, output, status, error)
                tree.name = "已完成的需求模型"
                tree.set(usage_metadata={"input_tokens": 7, "output_tokens": 4, "total_tokens": 11})

        def handler(request):
            """返回正常模型响应，网络仅经过内存传输。"""
            return httpx.Response(200, json=completion())

        app = create_app(self.config, Upstream(self.config, httpx.MockTransport(handler)),
                         FinishedRecorder(self.config))
        output = StringIO()
        with self.prohibit_uploads(), redirect_stdout(output), TestClient(app) as client:
            events = read_events(client, envelope(streaming=False))
        record = self.summary(output)
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(record["data"]["run_name"], "model-inference")
        self.assertEqual(record["data"]["model"]["total_tokens"], 11)
        self.assertEqual(app.state.history.get("run-1")["analysis"]["execution_trace"], record)

    async def test_failed_call_retains_local_ids_and_sanitized_error(self):
        """上游认证失败也能打印真实本地追踪身份，错误不泄露响应原文。"""
        def handler(request):
            """模拟认证拒绝，确保不会发生真实网络请求。"""
            return httpx.Response(401, json={"error": {"message": "SECRET_PROMPT", "type": "authentication_error"}})

        app = create_app(self.config, Upstream(self.config, httpx.MockTransport(handler)))
        output = StringIO()
        with self.prohibit_uploads(), redirect_stdout(output), TestClient(app) as client:
            read_events(client, envelope(streaming=False))
        data = self.summary(output)["data"]
        self.assertIsInstance(data["span_id"], str)
        self.assertEqual(data["trace_id"], data["span_id"])
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["error"]["code"], "UPSTREAM_AUTH_ERROR")
        self.assertNotIn("SECRET_PROMPT", json.dumps(data))

    async def test_local_finish_failure_does_not_turn_failure_or_cancel_into_success(self):
        """追踪收尾故障时保留真实失败或取消，不能因已有结束时间而判断成功。"""
        for status, error in (
            ("failed", {"code": "UPSTREAM_TIMEOUT"}),
            ("cancelled", {"code": "REQUEST_CANCELLED"}),
        ):
            with self.subTest(status=status), self.prohibit_uploads():
                observation = self.observation()
                _, tree = await TraceRecorder(self.config).start({}, {})
                observation.set_trace(tree)
                observation.begin()
                self.tick += 0.1
                observation.end()
                with patch("langsmith.RunTree.end", side_effect=RuntimeError("local end failure")), \
                        self.assertLogs("app.tracing", level="WARNING"):
                    await TraceRecorder(self.config).finish(tree, {}, status, error)
                data = observation.snapshot(status, error)["data"]
                self.assertEqual(data["status"], status)
                self.assertIsNotNone(data["ended_at"])
                if status == "failed":
                    self.assertEqual(data["error"]["code"], "UPSTREAM_TIMEOUT")
                else:
                    self.assertIsNone(data["error"])

    async def test_trace_creation_failure_keeps_model_response_and_local_fallback(self):
        """SDK 对象创建失败时只失去追踪身份，模型调用与本地用量保持有效。"""
        def handler(request):
            """返回正常模型响应以验证观测故障不影响业务。"""
            return httpx.Response(200, json=completion())

        app = create_app(self.config, Upstream(self.config, httpx.MockTransport(handler)))
        output = StringIO()
        with self.prohibit_uploads(), patch("app.tracing.RunTree", side_effect=RuntimeError("local SDK failure")), \
                redirect_stdout(output), TestClient(app) as client:
            events = read_events(client, envelope(streaming=False))
        data = self.summary(output)["data"]
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(data["status"], "completed")
        self.assertIsNone(data["span_id"])
        self.assertIsNone(data["trace_id"])
        self.assertEqual((data["span_kind"], data["run_name"]), ("model", "model-inference"))
        self.assertEqual(data["model"]["total_tokens"], 5)

    async def test_cancelled_stream_retains_local_trace_and_partial_facts(self):
        """取消未完成模型流时关闭上游并保留本地 Span 与已观测的时间。"""
        closed = []

        class Stream(httpx.AsyncByteStream):
            """仅提供一个模型分片，然后等待消费者主动关闭。"""

            async def __aiter__(self):
                """发出有效内容，后续等待不会访问任何网络。"""
                yield ("data: " + json.dumps(chunk({"content": "partial"})) + "\n\n").encode()
                await asyncio.Event().wait()

            async def aclose(self):
                """记录上游流资源已经释放。"""
                closed.append(True)

        def handler(request):
            """将 OpenAI SDK 请求交给可控制的内存流。"""
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

        app = create_app(self.config, Upstream(self.config, httpx.MockTransport(handler)))
        raw = envelope()
        self.addCleanup(app.state.observation_output.close)
        run = RunAgentInput.model_validate(raw)
        call = InferenceInput.model_validate(raw["forwardedProps"]["modelInference"])
        events = app.state.service.events(run, call, app.state.service.reserve(run, call))
        output = StringIO()
        with self.prohibit_uploads(), redirect_stdout(output):
            async for event in events:
                if getattr(event, "name", None) == "model-inference.chunk":
                    break
            await events.aclose()
            await asyncio.to_thread(app.state.observation_output.close)
        data = self.summary(output)["data"]
        self.assertTrue(closed)
        self.assertIsInstance(data["span_id"], str)
        self.assertEqual(data["status"], "cancelled")
        self.assertIsNone(data["error"])
        self.assertIsNotNone(data["model"]["ttft_ms"])
        self.assertIsNone(data["model"]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
