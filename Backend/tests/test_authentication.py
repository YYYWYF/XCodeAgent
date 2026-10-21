"""云效登录、文件凭据与取消栅栏的独立回归。"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.services.auth_credentials import CredentialStore, AuthCredentials
from app.services.auth_protection import protect
from app.services.yunxiao_auth import AuthService
from app.protocols import authentication
from app.protocols.authentication import build_authentication_stream

SESSION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TOKEN = "private-access-token-never-in-events"
REFRESH = "private-refresh-token-never-in-events"


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    """用 HTTP 模拟服务及真实系统加密验证认证边界。"""
    def setUp(self):
        """每个测试隔离用户目录和环境，不访问真实云效。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, {"YUNXIAO_ENDPOINT": "http://127.0.0.1:9999", "YUNXIAO_POLL_INTERVAL_SECONDS": "0.1", "YUNXIAO_LOGIN_TIMEOUT_SECONDS": "2", "YUNXIAO_PENDING_CODES": "", "DEVAGENTSTUDIO_WORKING_DIR": ".devagentstudio_dev"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = CredentialStore(Path(self.temporary.name)/"yst-login")
        self.store.initialize(SESSION)
        self.calls = []
        self.pending_count = 0
        self.service = AuthService(self.store, httpx.MockTransport(self.handler))

    def handler(self, request):
        """按 demo 返回 state、token 和用户，并记录认证请求头。"""
        self.calls.append(request)
        if request.url.path.endswith("/auth/state"):
            assert request.headers["X-User-Agent"] == "Cli"
            return httpx.Response(200, json={"code": 0, "data": {"state": "state with & characters"}})
        if request.url.path.endswith("/auth/token"):
            assert request.url.params["state"] == "state with & characters"
            if self.pending_count:
                self.pending_count -= 1
                return httpx.Response(200, json={"code": 1001, "data": None})
            return httpx.Response(200, json={"code": 0, "data": {"token": TOKEN, "expiresAt": int(time.time())+3600, "refreshToken": REFRESH, "refreshExpiresAt": int(time.time())+7200, "tokenType": "Bearer"}})
        if request.url.path.endswith("/auth/userinfo"):
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return httpx.Response(200, json={"code": 0, "data": {"id": "u1", "nickname": "测试用户"}})
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"ok": True})

    async def progress(self, *args, **kwargs):
        """保存公开阶段供测试检查。"""
        pass

    async def test_login_encrypts_file_and_reads_without_memory_cache(self):
        """验证真正落盘、密文及新存储实例可读取。"""
        self.pending_count = 1
        result = await self.service.login(SESSION, self.progress)
        self.assertTrue(result["authenticated"])
        self.assertNotIn(TOKEN, self.store.path.read_text())
        self.assertNotIn(REFRESH, self.store.path.read_text())
        reloaded = CredentialStore(self.store.root).read(SESSION)
        self.assertEqual(reloaded.access_token, TOKEN)
        self.assertGreater(reloaded.expires_at_ms, time.time()*1000)
        self.assertFalse(hasattr(self.service, "access_token"))
        self.store.path.unlink()
        self.assertFalse(self.service.status(SESSION)["authenticated"])

    async def test_configured_http_and_https_services_follow_demo(self):
        """非本机 HTTP 和 HTTPS 均可完成认证，业务代理也沿用配置协议。"""
        for endpoint in ("http://yunxiao.internal:8080", "https://yunxiao.example"):
            with self.subTest(endpoint=endpoint):
                with patch.dict(os.environ, {"YUNXIAO_ENDPOINT": endpoint, "YUNXIAO_API_BASE_URL": ""}):
                    self.calls.clear()
                    result = await self.service.login(SESSION, self.progress)
                    self.assertTrue(result["authenticated"])
                    self.assertEqual([str(call.url).split("/auth/")[0] for call in self.calls], [endpoint] * 3)
                    response = await self.service.cloud_request(SESSION, {"url": endpoint + "/api", "method": "GET"})
                    self.assertEqual(response["httpStatus"], 200)
                    self.assertEqual(self.calls[-1].headers["Authorization"], f"Bearer {TOKEN}")
                    with patch.dict(os.environ, {"YUNXIAO_API_BASE_URL": "http://business.internal:8081"}):
                        response = await self.service.cloud_request(SESSION, {"url": "http://business.internal:8081/api", "method": "GET"})
                        self.assertEqual(response["body"], {"ok": True})
                        self.assertEqual(str(self.calls[-1].url), "http://business.internal:8081/api")

    async def test_corrupt_or_expired_file_is_removed(self):
        """损坏或即将到期的文件不能继续表示有效登录。"""
        await self.service.login(SESSION, self.progress)
        self.store.path.write_text('{"ciphertext":"invalid"}')
        self.assertFalse(self.service.status(SESSION)["authenticated"])
        self.assertFalse(self.store.path.exists())
        credentials = AuthCredentials(access_token=TOKEN, expires_at_ms=int(time.time()*1000)-1, user_id="u", user_name="n")
        self.store.save(SESSION, credentials)
        self.assertIsNone(self.store.read(SESSION))
        self.assertFalse(self.store.path.exists())

    async def test_cancel_and_shutdown_prevent_late_write(self):
        """取消和退出后到达的 token 不能重新创建凭据。"""
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            """暂停 token 请求，模拟用户在网络请求中退出。"""
            if request.url.path.endswith("/auth/token"):
                started.set()
                await release.wait()
            return self.handler(request)
        self.service.transport = httpx.MockTransport(delayed)
        task = asyncio.create_task(self.service.login(SESSION, self.progress))
        await started.wait()
        await self.service.reset(SESSION, shutdown=True)
        release.set()
        with self.assertRaisesRegex(RuntimeError, "取消|结束"):
            await task
        self.assertFalse(self.store.path.exists())
        with self.assertRaises(RuntimeError):
            self.store.require_session(SESSION)

    async def test_duplicate_login_and_stale_session_are_rejected(self):
        """同一会话只允许一个轮询，旧会话不能读取新凭据。"""
        started, release = asyncio.Event(), asyncio.Event()
        async def report(*args, **kwargs):
            """等待测试发出继续信号。"""
            started.set()
            await release.wait()
        task = asyncio.create_task(self.service.login(SESSION, report))
        await started.wait()
        with self.assertRaisesRegex(RuntimeError, "正在进行"):
            await self.service.login(SESSION, self.progress)
        await self.service.reset(OTHER, initialize=True)
        release.set()
        with self.assertRaises(RuntimeError):
            await task
        with self.assertRaises(RuntimeError):
            self.service.status(SESSION)
        self.assertFalse(self.store.path.exists())

    async def test_complete_ag_ui_lifecycle_does_not_expose_credentials(self):
        """失败和成功都有终帧，事件不包含 token。"""
        with patch.object(authentication, "_service", self.service):
            chunks = [chunk async for chunk in build_authentication_stream({"threadId":"t", "runId":"r", "forwardedProps":{"authentication":{"action":"login", "sessionId":SESSION, "workingDir":".devagentstudio_dev"}}})]
        raw = "".join(chunks)
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertTrue(events[-1]["result"]["authentication"]["authenticated"])
        self.assertIn("STATE_SNAPSHOT", [event["type"] for event in events])
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn(REFRESH, raw)

    async def test_environment_mismatch_has_handled_failure(self):
        """开发模式两端目录不一致时不能写入错误环境。"""
        with patch.object(authentication, "_service", self.service):
            raw = "".join([chunk async for chunk in build_authentication_stream({"forwardedProps":{"authentication":{"action":"status", "sessionId":SESSION, "workingDir":".devagentstudio_st"}}})])
        self.assertIn('"status":"failed"', raw)
        self.assertIn("RUN_FINISHED", raw)
        self.assertFalse(self.store.path.exists())

    async def test_missing_endpoint_and_network_failure_are_safe(self):
        """配置和网络错误不会回显 token 或上游 URL 参数。"""
        with patch.dict(os.environ, {"YUNXIAO_ENDPOINT":""}):
            with self.assertRaisesRegex(RuntimeError, "YUNXIAO_ENDPOINT"):
                await self.service.login(SESSION, self.progress)
        def failed(request):
            """模拟服务网络中断。"""
            raise httpx.ConnectError("sensitive URL", request=request)
        self.service.transport = httpx.MockTransport(failed)
        with self.assertRaisesRegex(RuntimeError, "连接失败"):
            await self.service.login(SESSION, self.progress)
        self.assertFalse(self.store.path.exists())

    async def test_timeout_and_fatal_http_response_do_not_save(self):
        """超时和服务端 HTTP 拒绝都终止登录。"""
        def pending(request):
            """持续返回等待授权状态。"""
            if request.url.path.endswith("/auth/token"):
                return httpx.Response(202)
            return self.handler(request)
        self.service.transport = httpx.MockTransport(pending)
        with patch.dict(os.environ, {"YUNXIAO_LOGIN_TIMEOUT_SECONDS":"1"}):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                await self.service.login(SESSION, self.progress)
        self.service.transport = httpx.MockTransport(lambda request: httpx.Response(401))
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            await self.service.login(SESSION, self.progress)
        self.assertFalse(self.store.path.exists())

    async def test_trusted_cloud_proxy_uses_file_and_blocks_foreign_origin(self):
        """业务 token 只由 Python 附加，不能发到任意域名。"""
        await self.service.login(SESSION, self.progress)
        result = await self.service.cloud_request(SESSION, {"url":"http://127.0.0.1:9999/api", "method":"GET", "headers":{"Authorization":"Bearer wrong"}})
        self.assertEqual(result["body"], {"ok":True})
        with self.assertRaisesRegex(RuntimeError, "可信服务"):
            await self.service.cloud_request(SESSION, {"url":"https://other.example/api", "method":"GET"})
        with self.assertRaisesRegex(RuntimeError, "认证接口"):
            await self.service.cloud_request(SESSION, {"url":"http://127.0.0.1:9999/auth/token", "method":"GET"})

    async def test_startup_cleans_previous_session_and_temporary_files(self):
        """新桌面启动删除上次退出残留，并隔离旧会话。"""
        await self.service.login(SESSION, self.progress)
        (self.store.root/".auth-crash.tmp").write_text("partial")
        await self.service.reset(OTHER, initialize=True)
        self.assertFalse(self.store.path.exists())
        self.assertFalse(list(self.store.root.glob(".auth-*.tmp")))
        self.assertFalse(self.service.status(OTHER)["authenticated"])


if __name__ == "__main__":
    unittest.main()
