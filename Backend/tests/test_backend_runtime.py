"""内置后台租约与有序退场的竞态边界，不调用模型或消费任何产物确认。"""

import json
import unittest
from unittest.mock import patch
from app.services.backend_runtime import BackendRuntime
from app.protocols.backend_runtime import build_backend_runtime_stream


class BackendRuntimeTests(unittest.TestCase):
    def setUp(self):
        """用单调假时钟隔离租约与真实任务收口。"""
        self.now = 100
        self.work = 0
        self.exits = []
        self.runtime = BackendRuntime("t" * 64, clock=lambda: self.now)
        self.runtime.busy = lambda: self.work
        self.runtime.shutdown = lambda: self.exits.append(self.now)

    def test_crashed_client_waits_for_real_work(self):
        """客户端失联只过期租约，生成完成后才允许后台退出。"""
        self.runtime.act("attach", "desktop")
        self.work = 1
        self.now += 100
        self.runtime.tick()
        self.now += 10
        self.runtime.tick()
        self.assertEqual(self.exits, [])
        self.assertEqual(self.runtime.phase, "open")
        self.work = 0
        self.runtime.tick()
        self.assertEqual(len(self.exits), 1)

    def test_prepare_blocks_new_work_and_can_rollback(self):
        """准备和入口计数原子互斥，清理预览仍放行，取消后恢复服务。"""
        self.runtime.act("attach", "a")
        self.assertTrue(self.runtime.enter_request())
        self.assertEqual(self.runtime.act("prepare_exit", "a")["state"], "busy")
        self.runtime.leave_request()
        prepared = self.runtime.act("prepare_exit", "a")
        self.assertFalse(self.runtime.enter_request())
        self.assertTrue(self.runtime.enter_request(preview_cleanup=True))
        self.assertEqual(self.runtime.act("commit_exit", "a", prepared["ticket"])["state"], "busy")
        self.runtime.leave_request()
        with self.assertRaises(ValueError):
            self.runtime.act("commit_exit", "b", prepared["ticket"])
        self.assertEqual(self.runtime.act("cancel_exit", "a", prepared["ticket"])["state"], "open")
        self.assertTrue(self.runtime.enter_request())

    def test_other_clients_and_workers_prevent_exit(self):
        """升级不能打断其它前端或已脱离网络连接的真实后台任务。"""
        self.runtime.act("attach", "a")
        self.runtime.act("attach", "b")
        self.assertEqual(self.runtime.act("prepare_exit", "a")["state"], "busy")
        self.runtime.act("detach", "b")
        self.work = 1
        self.assertEqual(self.runtime.act("prepare_exit", "a")["state"], "busy")
        self.assertEqual(self.runtime.phase, "open")

    def test_exit_waits_for_control_receipt_and_never_reopens(self):
        """提交退出后等待 AG-UI 回执发送完成，旧票据不可重新开放服务。"""
        self.runtime.act("attach", "a")
        prepared = self.runtime.act("prepare_exit", "a")
        self.runtime.control_requests = 1
        self.runtime.act("commit_exit", "a", prepared["ticket"])
        self.runtime.tick()
        self.assertEqual(self.exits, [])
        self.assertEqual(self.runtime.act("cancel_exit", "a", prepared["ticket"])["state"], "exiting")
        self.runtime.control_requests = 0
        self.runtime.tick()
        self.assertEqual(len(self.exits), 1)

    def test_startup_grace_and_unmanaged_source_server(self):
        """冻结实例留出首连接预算，开发共享后台始终不由租约退出。"""
        self.now += 59
        self.runtime.tick()
        self.assertEqual(self.exits, [])
        self.now += 1
        self.runtime.tick()
        self.assertEqual(len(self.exits), 1)
        unmanaged = BackendRuntime()
        with self.assertRaises(ValueError):
            unmanaged.authorize("t" * 64)
        unmanaged.tick()


class BackendRuntimeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_counts_entire_response_and_closes_new_work(self):
        """最后一个响应收口前不可准备退出，栅栏之后不进入业务应用。"""
        from app.middleware.backend_runtime import BackendRuntimeAdmission
        runtime = BackendRuntime("t" * 64)
        runtime.shutdown = lambda: None
        entered = []
        messages = []

        async def application(scope, receive, send):
            """在响应末尾模拟任务接受，验证退场无法穿过业务接收窗口。"""
            entered.append(scope["path"])
            self.assertEqual(runtime.act("prepare_exit", "a")["state"], "busy")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            """提供已完成的请求正文；响应流不额外创建业务。"""
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            """保存标准 ASGI 响应，断言被拒请求仍发送 AG-UI 完整生命周期。"""
            messages.append(message)

        with patch("app.middleware.backend_runtime.runtime", runtime):
            admission = BackendRuntimeAdmission(application)
            scope = {"type": "http", "path": "/workflow/run", "asgi": {"spec_version": "2.4"}, "method": "POST", "headers": []}
            await admission(scope, receive, send)
            self.assertEqual(runtime.requests, 0)
            self.assertEqual(runtime.act("prepare_exit", "a")["state"], "prepared")
            await admission(scope, receive, send)
        self.assertEqual(entered, ["/workflow/run"])
        self.assertIn(b"RUN_FINISHED", b"".join(message.get("body", b"") for message in messages))

    async def test_full_lifecycle_and_invalid_token(self):
        """正确凭据返回完整生命周期，错误凭据不改变运行状态且不返回凭据。"""
        from app.services.backend_instance import current_backend_instance
        runtime = BackendRuntime("t" * 64)
        runtime.shutdown = lambda: None
        payload = {"threadId": "runtime-test", "runId": "lease-test", "forwardedProps": {"backendRuntime": {
            "action": "attach", "clientId": "a", "backendInstanceId": current_backend_instance().instance_id,
            "controlToken": "t" * 64,
        }}}
        with patch("app.protocols.backend_runtime.runtime", runtime):
            frames = [frame async for frame in build_backend_runtime_stream(payload)]
            events = [json.loads(frame.removeprefix("data: ")) for frame in frames]
            self.assertEqual(events[0]["type"], "RUN_STARTED")
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            result = next(event["value"] for event in events if event["type"] == "CUSTOM")
            self.assertEqual(result["state"], "open")
            payload["forwardedProps"]["backendRuntime"].update(action="prepare_exit", controlToken="x" * 64)
            frames = [frame async for frame in build_backend_runtime_stream(payload)]
            self.assertIn('"status":"failed"', "".join(frames).replace(" ", ""))
            self.assertNotIn("x" * 64, "".join(frames))
            self.assertEqual(runtime.phase, "open")
