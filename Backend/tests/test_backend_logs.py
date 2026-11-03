"""平台日志采集、流式隔离、滚动文件和 AG-UI 生命周期回归。"""

from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.observability.backend_log_store import BackendLogStore, MAX_WINDOW_BYTES
from app.observability.backend_logs import BackendLogHandler, CapturedStream, model_log_record
from app.protocols.backend_logs import backend_logs_capabilities, build_backend_logs_stream
from app.services.model_output_logger import ModelOutputLogHandler, log_model_output


class BackendLogStoreTests(unittest.TestCase):
    """使用临时目录验证真实持久化，不改变用户日志或全局标准流。"""

    def setUp(self) -> None:
        """创建隔离文件和代表真实敏感环境值的测试凭据。"""
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict("os.environ", {"MODEL_API_KEY": "test-cross-boundary-credential"})
        self.environment.start()
        self.path = Path(self.directory.name) / "logs" / "backend.log"
        self.store = BackendLogStore(self.path)

    def tearDown(self) -> None:
        """关闭真实文件句柄并恢复测试环境。"""
        self.store.close()
        self.environment.stop()
        self.directory.cleanup()

    def test_partial_tokens_are_visible_and_concurrent_models_do_not_interleave(self) -> None:
        """无换行 token 实时可见，相同调用合并且不同调用隔离。"""
        output = io.StringIO()
        stream = CapturedStream(output, "stdout", self.store)
        with model_log_record("model:a"):
            stream.write("第一个")
        with model_log_record("model:b"):
            stream.write("第二个")
        with model_log_record("model:a"):
            stream.write("模型\n")
        entries = self.store.snapshot()["entries"]
        self.assertEqual({entry["id"]: entry["text"] for entry in entries},
                         {"model:a": "第一个模型\n", "model:b": "第二个"})
        self.assertEqual(output.getvalue(), "第一个第二个模型\n")
        self.store.close()
        restored = BackendLogStore(self.path)
        try:
            self.assertEqual({e["id"]: e["text"] for e in restored.snapshot()["entries"]},
                             {"model:a": "第一个模型\n", "model:b": "第二个"})
        finally:
            restored.close()

    def test_stream_boundaries_cannot_leak_known_credentials(self) -> None:
        """跨 token 与定期刷新边界的凭据在快照和磁盘中都隐藏。"""
        secret = "test-cross-boundary-credential"
        for position in range(1, len(secret)):
            key = f"split:{position}"
            self.store.write("x" * 300 + secret[:position], source="stdout", key=key)
            self.store.flush()
            snapshot = self.store.snapshot()
            current = snapshot["entries"][-1]["text"]
            self.assertFalse(current.endswith(secret[:position]))
            self.store.write(secret[position:] + "\n", source="stdout", key=key)
        disk = self.path.read_text()
        self.assertNotIn(secret, disk)
        self.assertNotIn(secret, json.dumps(self.store.snapshot()))
        self.assertIn("[REDACTED]", disk)

    def test_redacts_fields_headers_urls_and_preserves_tracebacks(self) -> None:
        """完整异常保留多行，JSON 凭据字段、认证头和连接串均清理。"""
        self.store.record('password=private\n{"api_key":"private-json"}\nAuthorization: Bearer private-header\nhttps://name:private-url@host', source="test")
        text = self.store.snapshot()["entries"][0]["text"]
        for secret in ["private", "private-json", "private-header", "private-url"]:
            self.assertNotIn(secret, text)
        logger = BackendLogHandler(self.store)
        try:
            raise ValueError("真实错误")
        except ValueError:
            import sys
            event = logging.LogRecord("business", logging.ERROR, __file__, 1, "执行失败", (), sys.exc_info())
        logger.emit(event)
        logger.emit(event)
        entries = self.store.snapshot()["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[-1]["level"], "ERROR")
        self.assertIn("Traceback", entries[-1]["text"])
        self.assertIn("ValueError: 真实错误", entries[-1]["text"])

    def test_access_capture_excludes_only_debug_requests(self) -> None:
        """查看日志不自增噪音，其他请求仍保留。"""
        handler = BackendLogHandler(self.store)
        for message in ['POST /backend-logs/run HTTP/1.1 200', 'GET /health HTTP/1.1 200']:
            handler.emit(logging.LogRecord("uvicorn.access", logging.INFO, "", 0, message, (), None))
        entries = self.store.snapshot()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertIn("/health", entries[0]["text"])

    def test_rotation_and_bounded_window(self) -> None:
        """滚动归档最多五份，重开后仍可读，超大窗口有截断提示。"""
        self.store.close()
        self.store = BackendLogStore(self.path, max_bytes=300)
        for index in range(30):
            self.store.record(f"中文日志 {index}" * 5, source="test")
        self.assertTrue(self.path.with_name("backend.log.1").is_file())
        self.assertFalse(self.path.with_name("backend.log.6").exists())
        self.store.record("大" * MAX_WINDOW_BYTES, source="test")
        snapshot = self.store.snapshot()
        self.assertTrue(snapshot["truncated"])
        self.assertLessEqual(sum(len(e["text"].encode()) for e in snapshot["entries"]), MAX_WINDOW_BYTES)

    def test_write_error_keeps_memory_and_reports_storage_failure(self) -> None:
        """目录不可用时业务继续，日志窗口明确报告文件失败。"""
        with patch.object(self.store._handler, "shouldRollover", side_effect=OSError("blocked")):
            self.store.record("业务仍在执行", source="stdout")
        snapshot = self.store.snapshot()
        self.assertIn("业务仍在执行", snapshot["entries"][-1]["text"])
        self.assertIn("日志写入失败", snapshot["storageError"])

    def test_oversized_tool_record_restores_its_tail_after_restart(self) -> None:
        """工具参数超过窗口时仍能从片段恢复尾部，不因跳过巨行而全部消失。"""
        self.store.record("工具参数" * 200000 + "工具结尾标记", source="model", record_id="large-tool")
        self.store.close()
        restored = BackendLogStore(self.path)
        try:
            snapshot = restored.snapshot()
            self.assertTrue(snapshot["truncated"])
            self.assertTrue(snapshot["entries"][-1]["text"].endswith("工具结尾标记"))
            self.assertEqual(snapshot["entries"][-1]["id"], "large-tool")
        finally:
            restored.close()

    def test_compact_tools_in_streamed_and_non_streamed_callbacks(self) -> None:
        """工具参数保持完整含转义换行，两条模型路径都输出单行 JSON。"""
        calls = [{"name": "read_file", "args": {"path": "中文.ts", "code": "a\n  b"}, "id": "call-1", "type": "tool_call"}]
        for streamed in (False, True):
            output = io.StringIO()
            with patch("sys.stdout", output):
                if streamed:
                    handler = ModelOutputLogHandler()
                    run_id = uuid4()
                    handler.on_chat_model_start({}, [], run_id=run_id)
                    handler.on_llm_new_token("正文", run_id=run_id)
                    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="正文", tool_calls=calls))]]), run_id=run_id)
                else:
                    log_model_output(content="正文", tool_calls=calls)
            tool_lines = [line for line in output.getvalue().splitlines() if line.startswith("[model-tool-calls] ")]
            self.assertEqual(len(tool_lines), 1)
            parsed = json.loads(tool_lines[0].split(" ", 1)[1])
            self.assertEqual(parsed[0]["args"], calls[0]["args"])
            self.assertNotIn('\n  ', tool_lines[0])


class BackendLogsProtocolTests(unittest.IsolatedAsyncioTestCase):
    """验证真实 AG-UI 编码帧及取消，不启动业务工作流。"""

    async def test_get_watch_and_invalid_inputs_have_complete_lifecycle(self) -> None:
        """成功、订阅和非法动作均完整收口，且不登记工作区执行。"""
        with tempfile.TemporaryDirectory() as directory:
            store = BackendLogStore(Path(directory) / "backend.log")
            store.record("日志内容", source="test")
            try:
                for raw in [{"action": "get"}, {"action": "watch"}, {"action": "delete"}, {"action": "get", "path": "/private"}]:
                    with patch("app.protocols.backend_logs.get_backend_log_store", return_value=store), \
                            patch("app.protocols.backend_logs.WATCH_TICKS", 2), \
                            patch("app.protocols.backend_logs.WATCH_INTERVAL_SECONDS", 0), \
                            patch("app.protocols.ag_ui_action_stream.workflow_run_registry.register") as register:
                        stream = build_backend_logs_stream(payload={"threadId": "debug-thread", "runId": "debug-run", "forwardedProps": {"backendLogs": raw}})
                        events = [json.loads(line[6:]) async for chunk in stream for line in chunk.splitlines() if line.startswith("data: ")]
                    self.assertEqual(events[0]["type"], "RUN_STARTED")
                    self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                    self.assertTrue(any(e["type"] == "STATE_SNAPSHOT" for e in events))
                    failed = raw["action"] == "delete" or "path" in raw
                    result = events[-1]["result"]["backendLogs"]
                    self.assertEqual(result["status"], "failed" if failed else "completed")
                    if not failed:
                        self.assertIn("日志内容", result["logs"]["entries"][0]["text"])
                    register.assert_not_called()
            finally:
                store.close()
        self.assertEqual(backend_logs_capabilities()["endpoint"], "/backend-logs/run")

    async def test_closing_watch_cancels_operation(self) -> None:
        """关闭订阅后不保留轮询任务，后端采集对象仍可写。"""
        with tempfile.TemporaryDirectory() as directory:
            store = BackendLogStore(Path(directory) / "backend.log")
            try:
                with patch("app.protocols.backend_logs.get_backend_log_store", return_value=store):
                    stream = build_backend_logs_stream(payload={"forwardedProps": {"backendLogs": {"action": "watch"}}})
                    for _ in range(4):
                        await anext(stream)
                    await stream.aclose()
                store.record("关闭后继续采集", source="test")
                self.assertIn("关闭后继续采集", store.snapshot()["entries"][-1]["text"])
            finally:
                store.close()
