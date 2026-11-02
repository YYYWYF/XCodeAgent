"""桌面控制连接恢复时的注册等待，不执行模型或创建验收任务。"""
from threading import Thread, Event
from unittest import TestCase
from unittest.mock import patch

from app.services import runtime_acceptance_bridge as bridge


class RuntimeBridgeReadinessTests(TestCase):
    """验证等待注册、可选读取及超时不会消费任务预算。"""

    def test_reader_waits_for_desktop_registration(self):
        """启动顺序不同时等待当前桌面的注册通知。"""
        entered = Event()
        result = []

        def read():
            """在独立线程模拟业务节点读取桌面注册。"""
            entered.set()
            result.append(bridge.desktop_verifier_sha256())

        with patch.object(bridge, "_DESKTOP_VERIFIER", ""):
            worker = Thread(target=read)
            worker.start()
            self.assertTrue(entered.wait(1))
            with bridge._CONDITION:
                bridge._DESKTOP_VERIFIER = "a" * 64
                bridge._CONDITION.notify_all()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, ["a" * 64])

    def test_unavailable_bridge_does_not_create_acceptance_jobs(self):
        """无桌面时给出明确动作说明，不将等待失败伪装为供应商调用失败。"""
        with patch.object(bridge, "_DESKTOP_VERIFIER", ""), patch.object(
            bridge._CONDITION, "wait_for", return_value=False
        ):
            self.assertEqual(bridge.desktop_verifier_sha256(required=False), "")
            before = dict(bridge._JOBS)
            with self.assertRaisesRegex(ValueError, "本次尚未发送验收请求"):
                bridge.desktop_verifier_sha256()
            self.assertEqual(bridge._JOBS, before)
