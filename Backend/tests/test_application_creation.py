"""创建事务只在成功时占用目标目录，失败保留用户数据。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.services.application_creation import initialize_application
from app.services.managed_database_client import ManagedDatabaseClient, ManagedDatabaseRegistrationError


class ApplicationCreationTests(unittest.TestCase):
    """覆盖创建落盘边界、外部注册失败和 AG-UI 生命周期。"""

    def setUp(self):
        """使用独立目标和暂存根目录，避免触及真实应用。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "project"
        self.configuration = {
            "schemaVersion": 6, "configRevision": 1, "appName": "创建测试",
            "auth": {"enable": False, "yht": {
                "clientId": "", "privateKey": "", "publicKey": "", "centerPublicKey": "",
                "centerUri": "", "authorizeUri": "", "redirectUri": "", "endUrl": "", "logoutShareJs": "",
            }},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "none"},
        }
        self.patcher = patch("app.services.application_creation.tempfile.gettempdir", return_value=str(self.root))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def create(self):
        """调用生产创建事务，不替换真实配置和生命周期持久化。"""
        return initialize_application(
            str(self.target), application_id="application-test", application_name="创建测试",
            configuration=self.configuration, thread_id="thread-test",
        )

    def test_success_and_duplicate_receipt(self):
        """成功一次提交完整元数据，同身份重复请求复用成功结果。"""
        state, saved = self.create()
        self.assertEqual(state.application.id, "application-test")
        self.assertEqual(saved, self.configuration)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), [".devagentstudio"])
        self.assertEqual(self.create()[0], state)
        self.assertEqual(list(self.root.glob("devagentstudio-create-*")), [])

    def test_existing_empty_target(self):
        """用户预先选择的空目录也可提交创建。"""
        self.target.mkdir()
        self.create()
        self.assertTrue((self.target / ".devagentstudio/application.json").is_file())

    def test_failure_keeps_target_absent_and_retry_succeeds(self):
        """明确注册失败不占用目标、不保留失败事务，原目录可以直接重试。"""
        self.configuration["datasource"] = {"type": "database", "db": {"useBuiltin": True}}
        with patch("app.services.application_creation.ensure_managed_database_registration", side_effect=ValueError("注册失败")):
            with self.assertRaisesRegex(ValueError, "注册失败"):
                self.create()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.iterdir()), [])
        self.configuration["datasource"] = {"type": "none"}
        self.create()

    def test_commit_failure_restores_empty_target(self):
        """提交失败撤销本次创建的空目录，后续重试不被归属检查拦截。"""
        with patch.object(Path, "rename", side_effect=OSError("提交失败")):
            with self.assertRaisesRegex(OSError, "提交失败"):
                self.create()
        self.assertFalse(self.target.exists())
        self.create()

    def test_existing_user_files_are_preserved(self):
        """已有文件和已有应用仍受保护，不因失败重试规则而被覆盖。"""
        self.target.mkdir()
        user_file = self.target / "notes.txt"
        user_file.write_text("保留", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(user_file.read_text(encoding="utf-8"), "保留")

    def test_symlink_target_is_rejected(self):
        """目标符号链接不能把创建事务导向用户的其他目录。"""
        real = self.root / "real"
        real.mkdir()
        self.target.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(list(real.iterdir()), [])

    def test_client_cannot_supply_registration_result(self):
        """远端注册事实只能由后端取得，创建请求不能伪造绑定。"""
        self.configuration["managedDatabase"] = {
            "status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2,
        }
        with self.assertRaisesRegex(ValueError, "不能提供"):
            self.create()
        self.assertFalse(self.target.exists())

    def test_uncertain_registration_never_posts_twice(self):
        """未知远端结果只留隔离注册记录，不占用目标，后续禁止重复注册。"""
        self.configuration["datasource"] = {"type": "database", "db": {"useBuiltin": True}}
        with patch("app.services.managed_database_registration.ManagedDatabaseClient") as client:
            client.return_value.register.side_effect = ManagedDatabaseRegistrationError("结果未知", uncertain=True)
            with self.assertRaises(ManagedDatabaseRegistrationError):
                self.create()
            with self.assertRaises(ManagedDatabaseRegistrationError):
                self.create()
            self.assertEqual(client.return_value.register.call_count, 1)
        self.assertFalse(self.target.exists())

    def test_registered_binding_survives_commit_failure(self):
        """已注册但提交失败时复用原绑定和线程，重新打开窗口也不再次注册。"""
        self.configuration["datasource"] = {"type": "database", "db": {"useBuiltin": True}}
        with patch("app.services.managed_database_registration.ManagedDatabaseClient") as client:
            client.return_value.register.return_value = {
                "status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2,
            }
            with patch.object(Path, "rename", side_effect=OSError("提交失败")):
                with self.assertRaises(OSError):
                    self.create()
            self.assertFalse(self.target.exists())
            state, saved = initialize_application(
                str(self.target), application_id="new-window-id", application_name="创建测试",
                configuration=self.configuration, thread_id="new-window-thread",
            )
            self.assertEqual(client.return_value.register.call_count, 1)
            self.assertEqual(state.application.id, "application-test")
            self.assertEqual(state.initialization.thread_id, "thread-test")
            self.assertEqual(saved["managedDatabase"]["datasourceKey"], "12ab34cd56ef")

    def test_disabling_database_bypasses_retained_registration(self):
        """关闭模拟库后原目录可创建，未知或已成功的远端注册证据仍原样保留。"""
        for status in ("UNKNOWN", "REGISTERING", "REGISTERED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir=self.root) as case:
                self.target = Path(case) / "project"
                self.target.mkdir()
                self.configuration["datasource"] = {"type": "database", "db": {"useBuiltin": True}}
                with patch("app.services.managed_database_registration.ManagedDatabaseClient") as client:
                    if status == "REGISTERED":
                        client.return_value.register.return_value = {
                            "status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2,
                        }
                        with patch.object(Path, "rename", side_effect=OSError("提交失败")):
                            with self.assertRaises(OSError):
                                self.create()
                    else:
                        # 进程中断只留下 REGISTERING；请求结果未知则保存 UNKNOWN。
                        error = RuntimeError("注册中断") if status == "REGISTERING" else ManagedDatabaseRegistrationError("结果未知", uncertain=True)
                        client.return_value.register.side_effect = error
                        with self.assertRaises((RuntimeError, ManagedDatabaseRegistrationError)):
                            self.create()
                    self.assertEqual(list(self.target.iterdir()), [])
                    retained = next(
                        item for item in self.root.glob("devagentstudio-create-*")
                        if json.loads((item / "request.json").read_text())["configuration"] == self.configuration
                        and json.loads((item / ".devagentstudio/application.json").read_text())["managedDatabase"]["status"] == status
                    )
                    evidence = {path: path.read_bytes() for path in retained.rglob("*") if path.is_file()}
                    # 使用与新建表单相同的不带 db 配置，重新打开窗口也不受旧身份影响。
                    self.configuration["datasource"] = {"type": "database"}
                    state, saved = initialize_application(
                        str(self.target), application_id="without-database", application_name="创建测试",
                        configuration=self.configuration, thread_id="without-database-thread",
                    )
                    self.assertEqual(state.application.id, "without-database")
                    self.assertEqual(state.initialization.thread_id, "without-database-thread")
                    self.assertEqual(saved, self.configuration)
                    self.assertNotIn("managedDatabase", saved)
                    self.assertEqual(client.return_value.register.call_count, 1)
                    self.assertEqual({path: path.read_bytes() for path in evidence}, evidence)

    def test_ag_ui_saas_not_found_then_create_without_database(self):
        """真实注册适配器收到 404 后，AG-UI 切换无模拟库能成功且不再发送注册。"""
        requests = []

        def not_found(request):
            """模拟 SaaS 注册接口不存在，并记录实际发送的请求。"""
            requests.append(request)
            return httpx.Response(404)

        async def collect(run_id):
            """沿生产 AG-UI 协议提交当前表单配置并收集完整生命周期。"""
            return "".join([chunk async for chunk in build_application_lifecycle_ag_ui_stream(payload={
                "threadId": "thread-test", "runId": run_id,
                "forwardedProps": {"applicationLifecycle": {
                    "action": "initialize_application", "workspaceRoot": str(self.target),
                    "application": {"id": "application-test", "appName": "创建测试"},
                    "configuration": self.configuration,
                }},
            })])

        client = ManagedDatabaseClient(
            Settings(
                model_base_url="http://model.invalid", model_api_key="test-model-key", model_name="test",
                managed_database_service_key="test-key",
            ), transport=httpx.MockTransport(not_found),
        )
        with patch("app.services.managed_database_registration.ManagedDatabaseClient", return_value=client):
            self.configuration["datasource"] = {"type": "database", "db": {"useBuiltin": True}}
            failed = asyncio.run(collect("with-database"))
            self.assertIn('"failed"', failed)
            self.assertIn("SaaS", failed)
            self.assertFalse(self.target.exists())
            self.configuration["datasource"] = {"type": "database"}
            success = asyncio.run(collect("without-database"))
        self.assertEqual(len(requests), 1)
        self.assertIn('"completed"', success)
        self.assertNotIn('"failed"', success)
        self.assertNotIn("managedDatabase", success)
        for stream in (failed, success):
            for event in ("RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
                self.assertIn(event, stream)
        saved = json.loads((self.target / ".devagentstudio/application.json").read_text())
        self.assertEqual(saved["datasource"], {"type": "database"})
        self.assertNotIn("managedDatabase", saved)

    def test_ag_ui_failure_is_complete(self):
        """创建失败仍投射完整 AG-UI 终态，而不写入目标目录。"""
        async def collect():
            """收集生产协议流进行事件边界断言。"""
            return "".join([chunk async for chunk in build_application_lifecycle_ag_ui_stream(payload={
                "threadId": "thread-test", "runId": "run-test",
                "forwardedProps": {"applicationLifecycle": {
                    "action": "initialize_application", "workspaceRoot": str(self.target),
                    "application": {"id": "application-test", "appName": "创建测试"},
                    "configuration": self.configuration,
                }},
            })])
        with patch("app.services.application_creation.ensure_application_lifecycle", side_effect=ValueError("初始化失败")):
            stream = asyncio.run(collect())
        for event in ("RUN_STARTED", "TEXT_MESSAGE_START", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
            self.assertIn(event, stream)
        self.assertIn('"failed"', stream)
        self.assertFalse(self.target.exists())
