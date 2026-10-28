"""回归线程池入队后抛错、取消结算和退役池排空，不连接外部服务。"""

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.observation_output import ObservationOutput


class OutputSubmitFailureTests(unittest.TestCase):
    """真实线程池配合故障注入，确认失败任务不会偷偷输出或破坏待处理计数。"""

    def test_worker_creation_failure_discards_queued_item_and_recovers(self):
        """首次创建线程失败后旧工作项被清除，新任务不会重复结算旧容量。"""
        delivered = []
        output = ObservationOutput(writer=delivered.append)
        output.start()
        failed_pool = output._executor
        try:
            with patch.object(failed_pool, "_adjust_thread_count", side_effect=RuntimeError("cannot start new thread")):
                self.assertFalse(output.submit({"id": 1}))
            self.assertEqual(output.stats()["pending"], 0)
            self.assertIsNone(output._executor)
            self.assertTrue(output.submit({"id": 2}))
            self.assertTrue(output.drain(timeout=3))
            self.assertEqual([json.loads(value) for value in delivered], [{"id": 2}])
            self.assertEqual(output.stats()["completed"], 1)
            self.assertEqual(output.stats()["failed"], 1)
            self.assertEqual(output.stats()["pending"], 0)
        finally:
            output.close()

    def test_worker_that_already_dequeued_rejected_item_checks_cancel_ticket_before_io(self):
        """提交抛错时工作线程即使已拿到任务，也必须在执行 I/O 前检查撤销票据。"""
        delivered = []
        output = ObservationOutput(writer=delivered.append)
        output.start()
        failed_pool = output._executor
        start_worker = failed_pool._adjust_thread_count

        def start_then_fail():
            """先真实启动工作线程，再模拟提交入口异常。"""
            start_worker()
            raise RuntimeError("failure after queue insertion")

        try:
            with patch.object(failed_pool, "_adjust_thread_count", side_effect=start_then_fail):
                self.assertFalse(output.submit({"id": 1}))
            failed_pool.shutdown(wait=True)
            self.assertEqual(delivered, [])
            self.assertEqual(output.stats()["pending"], 0)
            self.assertEqual(output.stats()["failed"], 1)
            self.assertTrue(output.submit({"id": 2}))
            self.assertTrue(output.drain(timeout=3))
            self.assertEqual([json.loads(value) for value in delivered], [{"id": 2}])
        finally:
            output.close()

    def test_retiring_pool_cancels_accepted_queue_and_close_waits_for_its_active_writer(self):
        """故障池取消待执行任务时单次释放容量，并保留仍运行的旧池用于正常排空。"""
        entered, release, fourth_done, closed = (threading.Event() for _ in range(4))
        delivered = []

        def writer(payload):
            """第一条保持阻塞，新池第四条正常完成，以检测投递是否错误等待旧池。"""
            value = json.loads(payload)
            if value["id"] == 1:
                entered.set()
                release.wait(5)
            delivered.append(value)
            if value["id"] == 4:
                fourth_done.set()

        output = ObservationOutput(max_pending=4, writer=writer)

        def close_output():
            """在辅助线程等待所有新旧池结束，不影响测试线程释放阻塞任务。"""
            output.close()
            closed.set()

        closer = threading.Thread(target=close_output)
        try:
            self.assertTrue(output.submit({"id": 1}))
            self.assertTrue(entered.wait(3))
            self.assertTrue(output.submit({"id": 2}))
            failed_pool = output._executor
            with patch.object(failed_pool, "_adjust_thread_count", side_effect=RuntimeError("submit failure")):
                self.assertFalse(output.submit({"id": 3}))
            self.assertEqual(output.stats()["pending"], 1)
            self.assertEqual(output.stats()["failed"], 2)
            self.assertEqual(len(output._retired), 1)
            self.assertTrue(output.submit({"id": 4}))
            self.assertTrue(fourth_done.wait(3), "新提交错误地等待了旧池的阻塞输出")
            closer.start()
            self.assertFalse(closed.wait(0.05), "关闭没有等待退役池中的已接受输出")
        finally:
            release.set()
            if closer.ident is not None:
                closer.join(5)
            output.close()
        self.assertFalse(closer.is_alive())
        self.assertEqual(sorted(value["id"] for value in delivered), [1, 4])
        self.assertEqual(output.stats()["pending"], 0)
        self.assertEqual(output.stats()["completed"], 2)
        self.assertEqual(output.stats()["failed"], 2)
        self.assertEqual(len(output._inflight), 0)
        self.assertEqual(len(output._retired), 0)

    def test_repeated_creation_failure_does_not_accumulate_retired_pools_or_pending_items(self):
        """连续资源故障也要释放失败池和票据，恢复后保持可排空。"""
        delivered = []
        output = ObservationOutput(max_pending=1, writer=delivered.append)
        try:
            with patch.object(ThreadPoolExecutor, "_adjust_thread_count", side_effect=RuntimeError("cannot start thread")):
                for index in range(16):
                    self.assertFalse(output.submit({"id": index}))
                    self.assertEqual(output.stats()["pending"], 0)
                    self.assertEqual(len(output._inflight), 0)
                    self.assertEqual(len(output._retired), 0)
                    self.assertIsNone(output._executor)
            self.assertTrue(output.submit({"id": 16}))
            self.assertTrue(output.drain(timeout=3))
            self.assertEqual([json.loads(value) for value in delivered], [{"id": 16}])
            self.assertEqual(output.stats()["failed"], 16)
            self.assertEqual(output.stats()["pending"], 0)
        finally:
            output.close()


if __name__ == "__main__":
    unittest.main()
