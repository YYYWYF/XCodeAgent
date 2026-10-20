"""验证内置数据库启动检查、一次取密、环境注入和凭据脱敏。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.services.backend_launch_support import _backend_runtime_environment
from app.services.database_credentials import MySQLConnectionConfig
from app.services.backend_project_launcher import _launch_backend_project_locked
from app.services.managed_database_runtime import (
    ManagedDatabaseStartupError, ManagedRuntimeClient, prepare_managed_startup, resolve_managed_runtime,
)
from app.services.preview_runtime_repair import prepare_repair
from app.services.runtime_log_redaction import attach_redacted_logs
from app.services.preview_runtime_state import begin_attempt, record_database_check, runtime_snapshot


class ManagedDatabaseRuntimeTests(unittest.TestCase):
    """使用临时当前合同和 HTTP 替身，不连接实际 SaaS、MySQL 或启动 Java。"""

    def setUp(self) -> None:
        """准备已注册应用和带前缀的已确认实体。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        folder = self.root / ".devagentstudio"
        (folder / "plans").mkdir(parents=True)
        self.application = {
            "schemaVersion": 6, "configRevision": 3, "appName": "订单应用",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"datasourceKey": "12ab34cd56ef", "datasourceId": 2, "status": "REGISTERED"},
        }
        self.application_path = folder / "application.json"
        self.application_path.write_text(json.dumps(self.application))
        self.plan = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "entities": [{
            "id": "Order", "name": "订单", "description": "记录订单信息。", "table_name": "12ab34cd56ef_order",
            "fields": [{"name": "amount", "label": "金额", "description": "订单金额。", "type": "decimal", "required": True}],
        }]}
        self.plan_path = folder / "plans/technical-plan.json"
        self.plan_path.write_text(json.dumps(self.plan))
        self.ready = {"ready": True, "datasourceId": 2, "reason": None, "operationId": None, "operationStatus": None,
                      "tables": [{"tableName": "12ab34cd56ef_order", "status": "SYNCED", "differences": []}]}
        self.connection = {"datasourceKey": "12ab34cd56ef", "datasourceId": 2, "host": "db.invalid", "port": 3306,
                           "database": "inner2", "username": "demo_user", "password": "test-runtime-password",
                           "sslMode": "VERIFY_IDENTITY"}
        self.calls: list[httpx.Request] = []
        self.status = 200
        settings = Settings(model_base_url="http://model.invalid", model_api_key="test", model_name="test",
                            managed_database_service_key="test-service-key")
        self.client = ManagedRuntimeClient(settings, transport=httpx.MockTransport(self.reply))

    def reply(self, request: httpx.Request) -> httpx.Response:
        """检查请求只读语义和服务鉴权，模拟资源响应。"""

        self.calls.append(request)
        self.assertEqual(request.headers["Authorization"], "Bearer test-service-key")
        if self.status != 200:
            return httpx.Response(self.status, json={"code": "RUNTIME_CONNECTION_NOT_CONFIGURED", "message": "sensitive-payload"})
        if request.url.path.endswith("/tables/check"):
            self.assertEqual(request.method, "POST")
            sqls = json.loads(request.content)["sqls"]
            self.assertEqual(len(sqls), len(self.plan["entities"]))
            self.assertIn("CREATE TABLE `12ab34cd56ef_order`", sqls[0])
            return httpx.Response(200, json=self.ready)
        self.assertTrue(request.url.path.endswith("/runtime-connection"))
        self.assertEqual(request.method, "GET")
        return httpx.Response(200, json=self.connection)

    def test_preflight_and_before_launch_check_then_fetch_once(self) -> None:
        """构建前后固定两次检查，连接只取一次且不改正式配置。"""

        before = self.application_path.read_bytes()
        snapshot = prepare_managed_startup(self.root, client=self.client)
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(self.calls), 1)
        connection = resolve_managed_runtime(self.root, snapshot, client=self.client)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(connection.database, "inner2")
        self.assertIn("sslMode=VERIFY_IDENTITY", connection.jdbc_url())
        self.assertNotIn("test-runtime-password", repr(connection))
        self.assertEqual(self.application_path.read_bytes(), before)

    def test_missing_mismatched_or_partial_tables_block_before_credentials(self) -> None:
        """缺表、结构变化、错误挂载、重复或不完整集合均不能取到连接。"""

        original = json.loads(json.dumps(self.ready))
        for case in ["missing", "changed", "datasource", "partial", "duplicate", "busy"]:
            with self.subTest(case=case):
                self.ready = json.loads(json.dumps(original))
                if case in {"missing", "changed"}:
                    self.ready["ready"] = False
                    self.ready["tables"][0]["status"] = "NOT_CREATED" if case == "missing" else "STRUCTURE_MISMATCH"
                elif case == "datasource":
                    self.ready["datasourceId"] = 1
                elif case == "partial":
                    self.ready["tables"] = []
                elif case == "duplicate":
                    self.ready["tables"] *= 2
                else:
                    self.ready.update(ready=False, reason="CHECK_CHANGED", tables=[])
                self.calls.clear()
                with self.assertRaises(ManagedDatabaseStartupError):
                    prepare_managed_startup(self.root, client=self.client)
                self.assertTrue(all(call.url.path.endswith("/tables/check") for call in self.calls))

    def test_build_time_design_change_prevents_further_requests(self) -> None:
        """构建期间实体变更不能沿用原预检，停止前不再取密。"""

        snapshot = prepare_managed_startup(self.root, client=self.client)
        self.plan["entities"][0]["description"] = "订单记录说明已变更。"
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ManagedDatabaseStartupError, "已变化"):
            resolve_managed_runtime(self.root, snapshot, client=self.client)
        self.assertEqual(len(self.calls), 1)

    def test_connection_identity_and_contract_are_validated_without_secret_leaks(self) -> None:
        """连接身份不符或合同错误只返回安全错误，不回显密码。"""

        snapshot = prepare_managed_startup(self.root, client=self.client)
        for key, value in [("datasourceKey", "ab1234cd56ef"), ("datasourceId", 1), ("host", "db.invalid/?password=secret"), ("sslMode", "arbitrary")]:
            with self.subTest(key=key):
                previous = self.connection[key]
                self.connection[key] = value
                with self.assertRaises(ManagedDatabaseStartupError) as caught:
                    resolve_managed_runtime(self.root, snapshot, client=self.client)
                self.assertNotIn("test-runtime-password", str(caught.exception))
                self.connection[key] = previous

    def test_environment_injects_runtime_only_and_strips_saas_key(self) -> None:
        """运行时只注入子进程，不把 SaaS 密钥传入应用或回写配置。"""

        snapshot = prepare_managed_startup(self.root, client=self.client)
        with patch("app.services.managed_database_runtime._client", return_value=self.client), patch.dict(os.environ, {"MANAGED_DATABASE_SERVICE_KEY": "private-platform-key"}):
            environment, error = _backend_runtime_environment(self.root, managed_snapshot=snapshot)
        self.assertIsNone(error)
        self.assertEqual(environment["SPRING_DATASOURCE_PASSWORD"], "test-runtime-password")
        self.assertEqual(environment["MYSQL_DATABASE"], "inner2")
        self.assertEqual(environment["SPRING_FLYWAY_ENABLED"], "false")
        self.assertNotIn("MANAGED_DATABASE_SERVICE_KEY", environment)

    def test_missing_runtime_configuration_returns_safe_message(self) -> None:
        """SaaS 缺调试配置时给出明确处理建议，不泄露原始响应。"""

        self.status = 503
        with self.assertRaisesRegex(ManagedDatabaseStartupError, "专用调试连接") as caught:
            prepare_managed_startup(self.root, client=self.client)
        self.assertNotIn("sensitive-payload", str(caught.exception))

    def test_preflight_failure_never_builds_or_spawns_java(self) -> None:
        """真实启动入口遇到资源失败不会构建或创建 Java 进程。"""

        with patch("app.services.backend_project_launcher.prepare_managed_startup", side_effect=ManagedDatabaseStartupError("缺表")), \
             patch("app.services.backend_project_launcher._run_backend_build") as build, \
             patch("app.services.backend_project_launcher._start_backend_server") as start:
            result = _launch_backend_project_locked(root=self.root, backend_root=self.root / "backend",
                pom_path=self.root / "backend/pom.xml", runtime_root=self.root / ".devagentstudio/runtime/launch",
                maven_command="mvn", java_command="java")
        self.assertEqual(result["failed_stage"], "backend_database_resource")
        build.assert_not_called()
        start.assert_not_called()

    def test_design_changed_during_build_never_fetches_credentials_or_starts(self) -> None:
        """实际启动器构建后复核指纹，变更期间不会取得连接或创建 Java。"""

        def build(**_kwargs):
            """模拟构建耗时期间用户重新确认了另一份实体说明。"""

            self.plan["entities"][0]["description"] = "构建期间修改的说明。"
            self.plan_path.write_text(json.dumps(self.plan))
            return {"returncode": 0}

        with patch("app.services.backend_project_launcher.prepare_managed_startup", side_effect=lambda root, **kwargs: prepare_managed_startup(root, client=self.client, **kwargs)), \
             patch("app.services.managed_database_runtime._client", return_value=self.client), \
             patch("app.services.backend_project_launcher.stop_previous_backend_process", return_value={"success": True}), \
             patch("app.services.backend_project_launcher._run_backend_build", side_effect=build), \
             patch("app.services.backend_project_launcher._find_backend_snapshot_jar", return_value=(self.root / "test.jar", [])), \
             patch("app.services.backend_project_launcher._jar_has_main_class", return_value=True), \
             patch("app.services.backend_project_launcher._start_backend_server") as start:
            result = _launch_backend_project_locked(root=self.root, backend_root=self.root / "backend",
                pom_path=self.root / "backend/pom.xml", runtime_root=self.root / ".devagentstudio/runtime/launch",
                maven_command="mvn", java_command="java")
        self.assertEqual(result["failed_stage"], "backend_database_resource")
        self.assertIn("已变化", result["message"])
        self.assertEqual(len(self.calls), 1)
        start.assert_not_called()

    def test_external_connection_keeps_existing_resolver_without_saas(self) -> None:
        """外部直连继续原解析路径，不受技术规划或 SaaS 门禁影响。"""

        self.application["datasource"]["db"] = {"useBuiltin": False, "plantMode": {}}
        self.application_path.write_text(json.dumps(self.application))
        config = MySQLConnectionConfig("external.invalid", 3306, "external_user", "external-password", "external_db")
        with patch("app.services.backend_launch_support.resolve_application_mysql_config", return_value=config) as resolver, \
             patch("app.services.managed_database_runtime._client") as client:
            environment, error = _backend_runtime_environment(self.root)
        self.assertIsNone(error)
        self.assertEqual(environment["MYSQL_DATABASE"], "external_db")
        resolver.assert_called_once_with(self.root)
        client.assert_not_called()

    def test_resource_failure_does_not_call_repair_model(self) -> None:
        """资源失败即使直接请求诊断，也不能派发模型修代码。"""

        with patch("app.services.preview_runtime_repair.runtime_snapshot", return_value={"failedStage": "backend_database_resource"}):
            result = prepare_repair(str(self.root), "thread", {})
        self.assertEqual(result["status"], "failed")
        self.assertIn("无需修改业务代码", result["message"])

    def test_runtime_logs_are_redacted_before_disk_write(self) -> None:
        """输出管道落盘前替换已知凭据，保留正常就绪日志。"""

        stdout, stderr = self.root / "stdout.log", self.root / "stderr.log"
        with subprocess.Popen([sys.executable, "-c", "print('Started Application; test-runtime-password')"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            threads = attach_redacted_logs(process, stdout, stderr, {"MYSQL_PWD": "test-runtime-password"})
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=2)
        self.assertIn("Started Application", stdout.read_text())
        self.assertNotIn("test-runtime-password", stdout.read_text())

    def test_readiness_reports_all_entities_with_stable_navigation_ids(self) -> None:
        """多实体检查保留中文名称、稳定 ID 和字段差异，不靠错误文本反解析导航。"""

        self.plan["entities"].append({**self.plan["entities"][0], "id": "OrderItem", "name": "订单明细",
                                      "table_name": "12ab34cd56ef_order_item"})
        self.plan_path.write_text(json.dumps(self.plan))
        self.ready.update(ready=False, tables=[
            {"tableName": "12ab34cd56ef_order_item", "status": "NOT_CREATED", "differences": []},
            {"tableName": "12ab34cd56ef_order", "status": "STRUCTURE_MISMATCH", "differences": [
                {"category": "COLUMN", "target": "is_deleted", "expected": "存在", "actual": "缺失"},
            ]},
        ])
        reports = []
        with self.assertRaises(ManagedDatabaseStartupError):
            prepare_managed_startup(self.root, client=self.client, on_readiness=reports.append)
        self.assertEqual([report["status"] for report in reports], ["checking", "blocked"])
        rows = reports[-1]["entities"]
        self.assertEqual([(row["entityId"], row["label"]) for row in rows], [("Order", "订单"), ("OrderItem", "订单明细")])
        self.assertEqual(rows[0]["differences"][0]["target"], "is_deleted")
        self.assertEqual(rows[1]["status"], "NOT_CREATED")
        self.assertIsNotNone(reports[-1]["checkedAt"])
        self.assertEqual(len(self.calls), 1)

    def test_untrusted_response_is_not_projected_as_entity_results(self) -> None:
        """错误挂载或不完整集合只能投影检查失败，不能展示看似通过的实体。"""

        self.ready["datasourceId"] = 99
        reports = []
        with self.assertRaises(ManagedDatabaseStartupError):
            prepare_managed_startup(self.root, client=self.client, on_readiness=reports.append)
        self.assertEqual(reports[-1]["status"], "error")
        self.assertEqual(reports[-1]["entities"][0]["status"], "NOT_CHECKED")
        self.assertEqual(reports[-1]["entities"][0]["differences"], [])

    def test_fixed_checks_publish_progress_and_ready_without_extra_requests(self) -> None:
        """构建前后复核各发布检查中和终态，不新增轮询或取密请求。"""

        reports = []
        snapshot = prepare_managed_startup(self.root, client=self.client, on_readiness=reports.append)
        resolve_managed_runtime(self.root, snapshot, client=self.client, on_readiness=reports.append)
        self.assertEqual([report["status"] for report in reports], ["checking", "ready", "checking", "ready"])
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("password", json.dumps(reports))

    def test_database_check_snapshot_redacts_text_and_resets_for_new_attempt(self) -> None:
        """AG-UI 原运行快照携带脱敏检查，新启动清除上一轮结果。"""

        begin_attempt(self.root)
        record_database_check(self.root, {"status": "blocked", "message": "password=not-public", "entities": []})
        current = runtime_snapshot(self.root, logs=False)
        self.assertEqual(current["databaseCheck"]["status"], "blocked")
        self.assertNotIn("not-public", current["databaseCheck"]["message"])
        begin_attempt(self.root)
        self.assertNotIn("databaseCheck", runtime_snapshot(self.root, logs=False))
