"""验证统计输出异步投递、冻结快照、容量限制及统一开关。"""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.model_call_logging import deliver_record
from app.observation_output import ObservationOutput
from app.upstream import Upstream
from tests.helpers import completion, envelope, read_events, settings


def record(index=1):
    """创建独立统计快照，含北京时间、英文操作和固定模型标签。"""
    return {"identity": {"run_id": f"run-{index}", "current_operation": "generate_technical_plan"},
            "data": {"span_kind": "model", "run_name": "model-inference",
                     "started_at": "2026-10-27T09:20:01.602528+08:00",
                     "ended_at": "2026-10-27T09:20:21.687423+08:00", "duration_ms": 20084,
                     "model": {"input_tokens": 4732, "output_tokens": 3299, "total_tokens": 8031}}}


class ObservationOutputTests(unittest.TestCase):
    """使用事件控制线程边界，避免靠睡眠和实际网络推断异步行为。"""

    def test_disabled_output_does_not_serialize_start_pool_or_deliver(self):
        """关闭统一开关时不序列化、不创建线程池，也不执行任何输出目标。"""
        delivered = []
        output = ObservationOutput(enabled=False, writer=delivered.append)
        self.addCleanup(output.close)
        with patch("app.observation_output.ThreadPoolExecutor") as executor:
            output.start()
            self.assertFalse(output.submit({"not_serializable": object()}))
            executor.assert_not_called()
        self.assertEqual(delivered, [])
        self.assertEqual(output.stats()["pending"], 0)
        self.assertEqual(output.stats()["submitted"], 0)

    def test_blocked_writer_does_not_hold_submission_and_snapshot_is_frozen(self):
        """提交不等待慢输出，提交后的字典修改不改变后台接收的时间或用量。"""
        entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
        delivered, results = [], []

        def writer(payload):
            """等测试释放后再读取已经冻结的 JSON。"""
            entered.set()
            release.wait(5)
            delivered.append(json.loads(payload))

        output = ObservationOutput(writer=writer)
        snapshot = record()

        def submit():
            """从辅助调用线程提交，确保错误的同步实现也能被有界检测。"""
            results.append(output.submit(snapshot))
            submitted.set()

        caller = threading.Thread(target=submit)
        try:
            caller.start()
            self.assertTrue(entered.wait(3), "后台输出没有启动")
            self.assertTrue(submitted.wait(1), "提交仍在等待输出完成")
            self.assertEqual(results, [True])
            snapshot["identity"]["current_operation"] = "changed_after_submission"
            snapshot["data"]["ended_at"] = "changed"
            snapshot["data"]["duration_ms"] = 999999
            snapshot["data"]["model"]["total_tokens"] = 0
            self.assertFalse(output.drain(timeout=0.01))
        finally:
            release.set()
            if caller.ident is not None:
                caller.join(5)
            output.close()
        self.assertFalse(caller.is_alive())
        self.assertEqual(delivered, [record()])

    def test_full_capacity_is_rejected_without_waiting_for_running_writer(self):
        """容量包含执行中的任务，满额拒绝不能等待或回退为同步打印。"""
        entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
        results = []

        def writer(payload):
            """阻塞首个任务，稳定占满唯一可用容量。"""
            entered.set()
            release.wait(5)

        output = ObservationOutput(max_pending=1, writer=writer)

        def submit_second():
            """在另一调用线程检测满额投递是否立即结束。"""
            results.append(output.submit(record(2)))
            submitted.set()

        caller = threading.Thread(target=submit_second)
        try:
            self.assertTrue(output.submit(record()))
            self.assertTrue(entered.wait(3))
            caller.start()
            self.assertTrue(submitted.wait(1), "队列满时投递发生等待")
            self.assertEqual(results, [False])
            self.assertEqual(output.stats()["dropped_full"], 1)
            self.assertEqual(output.stats()["pending"], 1)
        finally:
            release.set()
            if caller.ident is not None:
                caller.join(5)
            output.close()
        self.assertEqual(output.stats()["pending"], 0)

    def test_writer_failure_releases_capacity_and_next_record_can_succeed(self):
        """后台目标抛异常只记失败，不耗尽容量或阻止后续正常输出。"""
        delivered = []

        def writer(payload):
            """仅让第一条记录失败，随后允许正常输出。"""
            decoded = json.loads(payload)
            if decoded["identity"]["run_id"] == "run-1":
                raise OSError("console unavailable")
            delivered.append(decoded)

        output = ObservationOutput(max_pending=1, writer=writer)
        self.addCleanup(output.close)
        self.assertTrue(output.submit(record()))
        self.assertTrue(output.drain(timeout=3))
        self.assertEqual(output.stats()["failed"], 1)
        self.assertEqual(output.stats()["pending"], 0)
        self.assertTrue(output.submit(record(2)))
        self.assertTrue(output.drain(timeout=3))
        self.assertEqual(delivered, [record(2)])

    def test_serialization_failure_does_not_deliver_or_exhaust_capacity(self):
        """无法冻结的输入只记序列化失败，后续合法任务仍可提交。"""
        delivered = []
        output = ObservationOutput(max_pending=1, writer=delivered.append)
        self.addCleanup(output.close)
        self.assertFalse(output.submit({"invalid": object()}))
        self.assertEqual(output.stats()["serialization_failed"], 1)
        self.assertEqual(output.stats()["pending"], 0)
        self.assertTrue(output.submit(record()))
        self.assertTrue(output.drain(timeout=3))
        self.assertEqual([json.loads(payload) for payload in delivered], [record()])

    def test_single_worker_orders_records_and_close_drains_then_refuses_new_work(self):
        """单工作线程按投递顺序输出，关闭排空已接受任务后拒绝新任务。"""
        delivered = []
        output = ObservationOutput(writer=delivered.append)
        for index in range(8):
            self.assertTrue(output.submit(record(index)))
        output.close()
        self.assertEqual([json.loads(value)["identity"]["run_id"] for value in delivered],
                         [f"run-{index}" for index in range(8)])
        self.assertEqual(output.stats()["pending"], 0)
        self.assertFalse(output.submit(record(9)))
        self.assertEqual(output.stats()["dropped_closed"], 1)
        output.close()

    def test_delivery_entry_only_prints_console_and_does_not_connect_to_es(self):
        """当前统一入口只调用控制台函数，任何网络连接都应保持未调用。"""
        payload = json.dumps(record(), ensure_ascii=False)
        with patch("app.model_call_logging.print_record_json") as console, \
                patch("socket.create_connection", side_effect=AssertionError("unexpected ES network")) as network:
            deliver_record(payload)
        console.assert_called_once_with(payload)
        network.assert_not_called()


class ObservationOutputIntegrationTests(unittest.TestCase):
    """验证共享线程池与真实 AG-UI/历史收尾的行为边界。"""

    def test_model_finishes_and_history_is_saved_while_output_writer_is_blocked(self):
        """打印未完成时，模型请求仍返回 RUN_FINISHED，且 SQLite 已保存同份快照。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory))
            entered, release = threading.Event(), threading.Event()
            delivered = []

            def writer(payload):
                """阻塞输出目标，以检查请求路径没有等待此任务。"""
                entered.set()
                release.wait(5)
                delivered.append(json.loads(payload))

            def handler(request):
                """让真实 SDK 仅访问内存上游，不发起真实模型请求。"""
                return httpx.Response(200, json=completion())

            output = ObservationOutput(writer=writer)
            app = create_app(config, Upstream(config, httpx.MockTransport(handler)), observation_output=output)
            try:
                with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as callers:
                    request = callers.submit(read_events, client, envelope(streaming=False))
                    try:
                        self.assertTrue(entered.wait(3), "后台输出没有启动")
                        events = request.result(timeout=1)
                        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                        saved = app.state.history.get("run-1")
                        self.assertEqual(saved["status"], "completed")
                        self.assertEqual(saved["analysis"]["execution_trace"]["data"]["model"]["total_tokens"], 5)
                        self.assertEqual(delivered, [])
                    finally:
                        release.set()
            finally:
                release.set()
                output.close()
            self.assertEqual(delivered, [app.state.history.get("run-1")["analysis"]["execution_trace"]])

    def test_disabled_service_keeps_model_trace_and_history_without_output(self):
        """关闭统一输出不改变推理结果、本地追踪、北京时间或数据库快照。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory), model_observation_output_enabled=False)

            def handler(request):
                """提供完整离线模型响应，避免任何真实上游连接。"""
                return httpx.Response(200, json=completion())

            app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
            console = StringIO()
            with patch("app.model_call_logging.print_record_json") as printer, \
                    redirect_stdout(console), TestClient(app) as client:
                events = read_events(client, envelope(streaming=False))
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            printer.assert_not_called()
            self.assertEqual(console.getvalue(), "")
            snapshot = app.state.history.get("run-1")["analysis"]["execution_trace"]
            self.assertEqual(snapshot["data"]["model"]["total_tokens"], 5)
            self.assertTrue(snapshot["data"]["started_at"].endswith("+08:00"))
            self.assertIsInstance(snapshot["data"]["span_id"], str)
            self.assertEqual(app.state.observation_output.stats()["submitted"], 0)


if __name__ == "__main__":
    unittest.main()
