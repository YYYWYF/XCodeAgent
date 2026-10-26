"""验证真实注册适配合同、工作区持久化与 AG-UI 动作的安全边界。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import ApplicationConfigError
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle
from app.services.managed_database_client import ManagedDatabaseClient, ManagedDatabaseRegistrationError
from app.services.managed_database_registration import ensure_managed_database_registration


class ManagedDatabaseRegistrationTests(unittest.TestCase):
    """不访问真实 SaaS 或数据库，使用 HTTP 传输替身核对一次创建与中断保护。"""

    def setUp(self) -> None:
        """准备符合当前配置合同的独立工作区。"""

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        folder = self.workspace / ".devagentstudio"
        folder.mkdir()
        self.path = folder / "application.json"
        self.path.write_text(json.dumps({
            "schemaVersion": 6, "configRevision": 1, "appName": "注册测试",
            "auth": {"enable": False, "yht": {
                "clientId": "", "privateKey": "", "publicKey": "", "centerPublicKey": "",
                "centerUri": "", "authorizeUri": "", "redirectUri": "", "endUrl": "", "logoutShareJs": "",
            }},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "theme": {"primaryColor": "#6b3cf0"},
        }), encoding="utf-8")
        self.settings = Settings(
            model_base_url="http://model.invalid", model_api_key="test-model-key", model_name="test",
            managed_database_service_key="test-service-key",
        )
        self.calls: list[httpx.Request] = []

    def binding(self) -> dict[str, object]:
        """返回 SaaS 当前 HTTP 201 合同，包含可能以数字开头的真实 datasourceKey。"""

        return {"datasourceKey": "12ab34cd56ef", "datasourceId": 2, "status": "REGISTERED", "isDeleted": False}

    def client(self, handler=None) -> ManagedDatabaseClient:
        """创建不重试、不重定向的真实 HTTP 适配器，传输交由测试处理。"""

        def handle(request: httpx.Request) -> httpx.Response:
            """保存每次 POST，便于判断是否出现重复创建。"""

            self.calls.append(request)
            if handler is not None:
                return handler(request)
            return httpx.Response(201, json=self.binding())

        return ManagedDatabaseClient(self.settings, transport=httpx.MockTransport(handle))

    def test_success_saves_canonical_binding_and_reuses_without_post(self) -> None:
        """只提交权威应用名称，重入与改名后仍复用同一资源，不用名称冒充 Key。"""

        client = self.client()
        saved = ensure_managed_database_registration(self.workspace, client=client)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(str(self.calls[0].url), "http://127.0.0.1:18081/managed-database/datasources")
        self.assertEqual(self.calls[0].headers["Authorization"], "Bearer test-service-key")
        self.assertEqual(json.loads(self.calls[0].content), {"appName": "注册测试"})
        self.assertEqual(saved["managedDatabase"], {
            "status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2,
        })
        self.assertEqual(saved["configRevision"], 3)
        saved["appName"] = "重命名"
        ApplicationConfigRepository(self.workspace).save(saved)
        reused = ensure_managed_database_registration(self.workspace, client=client)
        self.assertEqual(reused["managedDatabase"], saved["managedDatabase"])
        self.assertEqual(reused["appName"], "重命名")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("test-service-key", self.path.read_text())
        self.assertNotIn("test-service-key", repr(self.settings))

    def test_invalid_application_name_never_sends_registration(self) -> None:
        """缺失、空白、超长名称在发送请求前拒绝，不发出无关联信息的创建。"""

        client = self.client()
        for name in [None, "", "   ", " 名称", "名" * 256, 123]:
            with self.subTest(name=name), self.assertRaises(ManagedDatabaseRegistrationError):
                client.register(name)
        self.assertEqual(self.calls, [])

    def test_binding_only_accepts_current_datasource_identity(self) -> None:
        """资源配置仅接受 datasourceKey，不把应用标识当作兼容别名。"""

        from app.domain.managed_database import ManagedDatabaseRegistration
        from pydantic import ValidationError

        binding = ManagedDatabaseRegistration.model_validate({
            "status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2,
        })
        self.assertEqual(binding.model_dump(by_alias=True)["datasourceKey"], "12ab34cd56ef")
        with self.assertRaises(ValidationError):
            ManagedDatabaseRegistration.model_validate({
                "status": "REGISTERED", "appKey": "12ab34cd56ef", "datasourceId": 2,
            })

    def test_read_timeout_is_unknown_and_second_call_never_posts(self) -> None:
        """响应丢失可能已创建记录，持久化 UNKNOWN 后再次调用必须拒绝。"""

        def timeout(request: httpx.Request) -> httpx.Response:
            """模拟请求发出后等待响应超时，不宣称远端未创建。"""

            raise httpx.ReadTimeout("sensitive-driver-message", request=request)

        client = self.client(timeout)
        for _ in range(2):
            with self.assertRaises(ManagedDatabaseRegistrationError) as captured:
                ensure_managed_database_registration(self.workspace, client=client)
            self.assertTrue(captured.exception.uncertain)
            self.assertNotIn("sensitive-driver-message", str(captured.exception))
        self.assertEqual(json.loads(self.path.read_text())["managedDatabase"]["status"], "UNKNOWN")
        self.assertEqual(len(self.calls), 1)

    def test_connect_failure_can_only_retry_on_another_explicit_call(self) -> None:
        """连接建立失败不自动重试，后续明确调用才能再次发起。"""

        def unavailable(request: httpx.Request) -> httpx.Response:
            """模拟尚未提交业务请求时连接失败。"""

            raise httpx.ConnectError("unavailable", request=request)

        with self.assertRaises(ManagedDatabaseRegistrationError) as captured:
            ensure_managed_database_registration(self.workspace, client=self.client(unavailable))
        self.assertFalse(captured.exception.uncertain)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(self.path.read_text())["managedDatabase"]["status"], "FAILED")
        saved = ensure_managed_database_registration(self.workspace, client=self.client())
        self.assertEqual(saved["managedDatabase"]["status"], "REGISTERED")
        self.assertEqual(len(self.calls), 2)

    def test_invalid_success_and_server_error_preserve_unknown(self) -> None:
        """成功体无效或服务端失败均不能当成确定未创建，也不透传响应秘密。"""

        for code in [201, 500]:
            with self.subTest(status=code):
                self.setUp()
                client = self.client(lambda _request: httpx.Response(code, json={"message": "private-secret"}))
                with self.assertRaises(ManagedDatabaseRegistrationError) as captured:
                    ensure_managed_database_registration(self.workspace, client=client)
                self.assertTrue(captured.exception.uncertain)
                self.assertNotIn("private-secret", str(captured.exception))
                self.assertEqual(json.loads(self.path.read_text())["managedDatabase"]["status"], "UNKNOWN")

    def test_inflight_marker_survives_failed_success_persistence(self) -> None:
        """收到 datasourceKey 后本地保存失败仍保留在途标记，阻止再次创建。"""

        save = ApplicationConfigRepository.save
        count = 0

        def fail_second(repository, application) -> None:
            """第一遍保存 REGISTERING，第二遍模拟磁盘写入失败。"""

            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk unavailable")
            save(repository, application)

        client = self.client()
        with patch.object(ApplicationConfigRepository, "save", new=fail_second):
            with self.assertRaises(ManagedDatabaseRegistrationError) as captured:
                ensure_managed_database_registration(self.workspace, client=client)
        self.assertIn("12ab34cd56ef", str(captured.exception))
        self.assertEqual(json.loads(self.path.read_text())["managedDatabase"]["status"], "REGISTERING")
        with self.assertRaises(ManagedDatabaseRegistrationError):
            ensure_managed_database_registration(self.workspace, client=client)
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_calls_submit_once(self) -> None:
        """同一工作区并发注册串行化，两个调用都取得同一个 datasourceKey。"""

        entered = threading.Event()
        release = threading.Event()

        def blocked(_request: httpx.Request) -> httpx.Response:
            """在首个请求发出后让第二个调用进入等待。"""

            entered.set()
            if not release.wait(3):
                raise AssertionError("测试同步超时")
            return httpx.Response(201, json=self.binding())

        client = self.client(blocked)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(ensure_managed_database_registration, self.workspace, client=client)
            self.assertTrue(entered.wait(3))
            second = pool.submit(ensure_managed_database_registration, self.workspace, client=client)
            release.set()
            self.assertEqual(first.result()["managedDatabase"], second.result()["managedDatabase"])
        self.assertEqual(len(self.calls), 1)

    def test_external_database_and_missing_key_do_not_register(self) -> None:
        """外部数据库与未配置密钥的请求都不写在途标记、不访问 SaaS。"""

        application = json.loads(self.path.read_text())
        application["datasource"]["db"]["useBuiltin"] = False
        ApplicationConfigRepository(self.workspace).save(application)
        with self.assertRaises(ManagedDatabaseRegistrationError):
            ensure_managed_database_registration(self.workspace, client=self.client())
        application["datasource"]["db"]["useBuiltin"] = True
        ApplicationConfigRepository(self.workspace).save(application)
        self.settings = Settings(model_base_url="unused", model_api_key="unused", model_name="unused")
        with self.assertRaises(ManagedDatabaseRegistrationError):
            ensure_managed_database_registration(self.workspace, settings=self.settings)
        self.assertNotIn("managedDatabase", json.loads(self.path.read_text()))
        self.assertEqual(self.calls, [])

    def test_invalid_binding_is_rejected_by_config_repository(self) -> None:
        """不完整绑定与密钥夹带均不能进入 canonical 配置。"""

        application = json.loads(self.path.read_text())
        application["managedDatabase"] = {"status": "REGISTERED", "datasourceKey": "12ab34cd56ef"}
        with self.assertRaises(ApplicationConfigError):
            ApplicationConfigRepository(self.workspace).save(application)

    def test_ag_ui_success_and_failure_complete_lifecycle(self) -> None:
        """注册动作成功与业务失败都完成消息、结果、快照和 RUN_FINISHED。"""

        write_application_lifecycle(self.workspace, create_application_lifecycle(
            application_id="local-app", application_name="注册测试", initialization_thread_id="thread",
        ))

        async def collect() -> list[dict]:
            """按现有测试习惯消费标准 AG-UI 测试事件，不访问网络。"""

            stream = build_application_lifecycle_ag_ui_stream(payload={
                "threadId": "thread", "runId": "registration-run", "forwardedProps": {
                    "applicationLifecycle": {
                        "action": "register_managed_database", "workspaceRoot": str(self.workspace),
                        "application": {"id": "local-app", "appName": "注册测试"},
                    },
                },
            })
            return [json.loads(line[6:]) async for frame in stream
                    for line in frame.splitlines() if line.startswith("data: ")]

        result = json.loads(self.path.read_text())
        result["managedDatabase"] = {"status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2}
        with patch("app.protocols.managed_database_registration.ensure_managed_database_registration", return_value=result):
            events = asyncio.run(collect())
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertIn("STATE_SNAPSHOT", [event["type"] for event in events])
        payload = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(payload["managedDatabase"]["datasourceKey"], "12ab34cd56ef")
        with patch("app.protocols.managed_database_registration.ensure_managed_database_registration",
                   side_effect=ManagedDatabaseRegistrationError("测试失败", uncertain=True)):
            failed = asyncio.run(collect())
        self.assertEqual(failed[-1]["type"], "RUN_FINISHED")
        failure = next(event["value"] for event in failed if event["type"] == "CUSTOM")
        self.assertEqual(failure["status"], "failed")


if __name__ == "__main__":
    unittest.main()
