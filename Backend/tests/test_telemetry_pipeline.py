"""验证统一管道的输出、快照和故障隔离。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import httpx

from app.observability import telemetry
from app.observability.telemetry_pipeline import TelemetryPipeline
from app.observability.telemetry_sinks import HttpSink, LocalSink


class TelemetryPipelineTests(unittest.TestCase):
    """覆盖 OFF、LOCAL、HTTP 和异常输出边界。"""

    def test_off_and_legacy_precedence(self):
        """OFF 不触碰输入或创建管道，旧 URL 仅在 MODE 缺失时生效。"""
        with patch.object(telemetry, "_pipeline", None), patch.dict(
            "os.environ", {"DEVAGENTSTUDIO_TELEMETRY_URL": "https://example.test"}, clear=True,
        ):
            self.assertEqual(telemetry.mode(), "http")
            with patch.dict("os.environ", {"DEVAGENTSTUDIO_OBSERVABILITY_MODE": "off"}):
                telemetry.publish(object())
                self.assertIsNone(telemetry._pipeline)

    def test_local_and_invalid_record(self):
        """LOCAL 写入冻结后的嵌套记录，坏记录独立计数。"""
        with TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"DEVAGENTSTUDIO_OBSERVABILITY_EXPERIMENT": "baseline"},
        ):
            root = Path(tmp)
            with patch("app.observability.telemetry_sinks.local_root", return_value=root):
                pipeline = TelemetryPipeline(LocalSink())
                record = {"identity": {"project_id": "a"}, "data": {"duration_ms": None}}
                pipeline.submit(record, "execution_trace")
                record["identity"]["project_id"] = "changed"
                pipeline.submit({"data": {"unexpected": "invalid"}}, "execution_trace")
                pipeline.submit({"data": {"run_name": "\ud800"}}, "execution_trace")
                pipeline.close(2)
            saved = json.loads(next((root / "baseline").glob("*.jsonl")).read_text())
            self.assertEqual(saved["identity"]["project_id"], "a")
            self.assertEqual(saved["data"], {"duration_ms": None})
            self.assertEqual(pipeline.diagnostics()["written"], 1)
            self.assertEqual(pipeline.diagnostics()["validation_failed"], 2)

            (root / "file").touch()
            with patch("app.observability.telemetry_sinks.local_root", return_value=root / "file"):
                pipeline = TelemetryPipeline(LocalSink())
                pipeline.submit({}, "telemetry_event")
                pipeline.close(2)
            self.assertEqual(pipeline.diagnostics()["local_write_failed"], 1)

    def test_http_mock(self):
        """HTTP 发送既有批次协议并分类非 2xx 状态。"""
        client = httpx.Client
        for status in (200, 400, 503):
            with self.subTest(status=status):
                requests = []

                def respond(request):
                    """读取真实 HTTP 客户端构造的请求并返回模拟状态。"""
                    self.assertEqual(request.method, "POST")
                    requests.append(json.loads(request.content))
                    return httpx.Response(status)

                with patch.dict("os.environ", {
                    "DEVAGENTSTUDIO_OBSERVABILITY_MODE": "http",
                    "DEVAGENTSTUDIO_OBSERVABILITY_URL": "https://example.test/batches",
                }), patch.object(httpx, "Client", side_effect=lambda **kw: client(
                    transport=httpx.MockTransport(respond), **kw,
                )):
                    pipeline = TelemetryPipeline(HttpSink())
                    pipeline.submit({"data": {"duration_ms": None}}, "execution_trace")
                    pipeline.close(2)
                self.assertEqual(requests[0]["schemaVersion"], 1)
                self.assertEqual(requests[0]["records"][0]["data"], {"durationMs": None})
                self.assertEqual(pipeline.diagnostics()["sent" if status == 200 else "http_failed"], 1)

    def test_queue_full_and_bounded_shutdown(self):
        """阻塞的 Sink 无法阻塞业务提交或停机。"""
        started = threading.Event()
        release = threading.Event()

        class BlockingSink:
            success = "written"

            def write(self, records):
                """模拟不可及时返回的 I/O。"""
                started.set()
                release.wait(3)

        pipeline = TelemetryPipeline(BlockingSink(), capacity=1)
        try:
            pipeline.submit({}, "execution_trace")
            self.assertTrue(started.wait(1))
            pipeline.submit({}, "execution_trace")
            pipeline.submit({}, "execution_trace")
            pipeline.close(0)
            self.assertTrue(pipeline.worker.is_alive())
            self.assertEqual(pipeline.diagnostics()["dropped"], 1)
        finally:
            release.set()
            pipeline.close(2)
        self.assertEqual(pipeline.diagnostics()["pending"], 0)


class TelemetryProtocolTests(unittest.TestCase):
    """验证前端已有的 AG-UI 提交入口。"""

    def test_ag_ui_lifecycle(self):
        """有效与无效记录均有完整生命周期，坏字段或原文不回显。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routes.telemetry import telemetry_router

        app = FastAPI()
        app.include_router(telemetry_router)
        with TestClient(app) as client, patch.object(telemetry, "publish") as publish:
            for record in ({}, {"unsupported": "private-content"}):
                response = client.post("/telemetry/run", headers={"accept": "text/event-stream"}, json={
                    "runId": "run", "threadId": "thread",
                    "forwardedProps": {"telemetry": {"action": "emit_trace", "record": record}},
                })
                self.assertEqual(response.status_code, 200)
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                self.assertEqual(events[0]["type"], "RUN_STARTED")
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                self.assertTrue(any(event["type"] == "TEXT_MESSAGE_CONTENT" for event in events))
                self.assertTrue(any(event["type"] == "STATE_SNAPSHOT" for event in events))
                custom = next(event for event in events if event["type"] == "CUSTOM")
                self.assertEqual(custom["value"]["status"], "failed" if record else "completed")
                self.assertNotIn("private-content", response.text)
            publish.assert_called_once()
