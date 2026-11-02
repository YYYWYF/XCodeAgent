from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.persistence.data_sources import write_sources
from app.graph.nodes.authorization_bootstrap import authorization_bootstrap
from app.services.authorization_bootstrap import _command_for_workspace, run_authorization_bootstrap


def _application_config(enabled: bool = True) -> dict:
    """构造 Bootstrap 判断权限能力的唯一应用配置。"""

    return {"authorization": {"enabled": enabled}}


def _technical_plan(fingerprint: str = "sha256:abc") -> dict:
    """构造满足平台节点最小前置的已确认权限技术规划。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "authorization_manifest": {"fingerprint": fingerprint},
    }


class AuthorizationBootstrapTests(unittest.TestCase):
    """验证权限 Bootstrap 的平台调用、缓存和失败边界。"""

    def setUp(self) -> None:
        """隔离平台解密服务，目录选择和运行环境仍走真实实现。"""

        decrypt = patch("app.services.database_credentials.decrypt_password", return_value="application-password")
        decrypt.start()
        self.addCleanup(decrypt.stop)

    def _workspace(self, directory: str) -> Path:
        """创建模板脚本执行器要求的最小生成项目目录。"""

        root = Path(directory).resolve()
        script = root / "backend" / "scripts" / "bootstrap-authorization.sh"
        ddl = root / "backend/sql/authorization/schema.sql"
        script.parent.mkdir(parents=True)
        ddl.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        script.with_suffix(".ps1").write_text("exit 0\n", encoding="utf-8")
        ddl.write_text("create table role (id int);", encoding="utf-8")
        application = root / ".devagentstudio/application.json"
        application.parent.mkdir(parents=True)
        application.write_text(json.dumps({"datasource": {"type": "database", "db": {"useBuiltin": False}}}), encoding="utf-8")
        write_sources(root, [{
            "id": "application-db", "name": "应用数据库", "type": "database", "mode": "direct",
            "domain": "application.mysql.local", "port": 3313, "schema": "application_schema",
            "userName": "application_user", "passwordCiphertext": "devagentstudio-secret:v1:rsa-oaep-256:key:cipher", "managedTables": [],
        }])
        return root

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_bootstrap_injects_current_connection_and_isolates_host(self, run_mock) -> None:
        """真实目录解析的连接必须进入脚本环境，宿主连接和平台密钥不得泄漏。"""

        run_mock.return_value = SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            with patch.dict("os.environ", {
                "DB_PASSWORD": "host-secret", "MYSQL_PWD": "host-secret",
                "SPRING_DATASOURCE_PASSWORD": "host-secret", "XCODEAGENT_DB_PASSWORD": "host-secret",
                "MANAGED_DATABASE_SERVICE_KEY": "platform-secret",
            }):
                result = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
            self.assertEqual(result["status"], "executed")
            environment = run_mock.call_args.kwargs["env"]
            self.assertEqual(environment["MYSQL_HOST"], "application.mysql.local")
            self.assertEqual(environment["MYSQL_PORT"], "3313")
            self.assertEqual(environment["MYSQL_DATABASE"], "application_schema")
            self.assertEqual(environment["SPRING_DATASOURCE_USERNAME"], "application_user")
            for key in ("MYSQL_PWD", "SPRING_DATASOURCE_PASSWORD", "XCODEAGENT_DB_PASSWORD"):
                self.assertEqual(environment[key], "application-password")
            self.assertIn("application.mysql.local:3313/application_schema", environment["SPRING_DATASOURCE_URL"])
            self.assertNotIn("DB_PASSWORD", environment)
            self.assertNotIn("MANAGED_DATABASE_SERVICE_KEY", environment)
            self.assertNotIn("application-password", json.dumps(result))

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_missing_connection_blocks_bootstrap(self, run_mock) -> None:
        """缺少数据库连接时不得让脚本使用 YAML 默认连接执行 SQL。"""

        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            write_sources(root, [])
            result = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
            self.assertEqual(result["failure_category"], "database_configuration_invalid")
            run_mock.assert_not_called()

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_success_is_cached_by_manifest_fingerprint(self, run_mock) -> None:
        """同一 manifest 指纹成功后不应重复调用数据库脚本。"""

        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = b"ok"
        run_mock.return_value.stderr = b""
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            first = run_authorization_bootstrap(
                root, _technical_plan(), application_config=_application_config()
            )
            second = run_authorization_bootstrap(
                root, _technical_plan(), application_config=_application_config()
            )
            self.assertEqual(first["status"], "executed")
            self.assertEqual(second["status"], "reused")
            self.assertEqual(run_mock.call_count, 1)
            self.assertEqual(run_mock.call_args.kwargs["timeout"], 600)
            self.assertEqual(run_mock.call_args.kwargs["cwd"], root.resolve())
            marker = root / ".devagentstudio/runtime/authorization-bootstrap/abc/result.json"
            self.assertEqual(
                json.loads(marker.read_text(encoding="utf-8"))["status"], "executed"
            )

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_failed_run_is_not_cached(self, run_mock) -> None:
        """脚本失败保留真实结果，且不能写入可复用成功标记。"""

        run_mock.return_value.returncode = 2
        run_mock.return_value.stdout = b""
        run_mock.return_value.stderr = b"invalid input"
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            result = run_authorization_bootstrap(
                root, _technical_plan(), application_config=_application_config()
            )
            self.assertEqual(result["status"], "failed")
            marker = root / ".devagentstudio/runtime/authorization-bootstrap/abc/result.json"
            self.assertFalse(marker.exists())

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_missing_schema_blocks_process_start(self, run_mock) -> None:
        """脚本存在但缺少新 SQL 时必须精确报错且不启动任何进程。"""

        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            (root / "backend/sql/authorization/schema.sql").unlink()
            result = run_authorization_bootstrap(
                root, _technical_plan(), application_config=_application_config()
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "template_contract_invalid")
            self.assertFalse(result["ddl_available"])
            run_mock.assert_not_called()

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_old_sql_paths_do_not_satisfy_contract(self, run_mock) -> None:
        """只有任一旧 SQL 或同时保留两个旧 SQL 都不能作为兼容来源。"""

        old_paths = ("backend/migrations/001-schema.sql", "backend/docs/auth/sql/ddl.sql")
        for paths in ((old_paths[0],), (old_paths[1],), old_paths):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as directory:
                root = self._workspace(directory)
                (root / "backend/sql/authorization/schema.sql").unlink()
                for relative in paths:
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("select 1;", encoding="utf-8")
                result = run_authorization_bootstrap(
                    root, _technical_plan(), application_config=_application_config()
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_category"], "template_contract_invalid")
                self.assertFalse(result["ddl_available"])
                run_mock.assert_not_called()

    def test_both_platforms_require_same_schema(self) -> None:
        """仅替换服务模块的平台视图，跨宿主验证脚本选择和唯一 SQL 契约。"""

        for platform, suffix in (("posix", ".sh"), ("nt", ".ps1")):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                root = self._workspace(directory)
                # 不修改全局 os.name，避免 pathlib 在 POSIX 上实例化 WindowsPath。
                with patch("app.services.authorization_bootstrap.os", SimpleNamespace(name=platform)):
                    command, error = _command_for_workspace(root)
                    self.assertIsNone(error)
                    script = root / f"backend/scripts/bootstrap-authorization{suffix}"
                    expected = (["/bin/bash", str(script)] if platform == "posix" else [
                        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-File", str(script),
                    ])
                    self.assertEqual(command, expected)
                    (root / "backend/sql/authorization/schema.sql").unlink()
                    self.assertEqual(_command_for_workspace(root), (
                        [], "权限 Bootstrap 缺少 backend/sql/authorization/schema.sql。"
                    ))

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_missing_or_linked_script_blocks_process_start(self, run_mock) -> None:
        """新 SQL 存在时，缺失脚本或指向工作区外的链接仍必须阻断。"""

        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            for suffix in (".sh", ".ps1"):
                (root / f"backend/scripts/bootstrap-authorization{suffix}").unlink()
            result = run_authorization_bootstrap(
                root, _technical_plan(), application_config=_application_config()
            )
            self.assertEqual(result["failure_category"], "template_contract_invalid")
            self.assertIn("模板脚本不存在", result["message"])
            outside = root / "outside.sh"
            outside.write_text("exit 0\n", encoding="utf-8")
            for suffix in (".sh", ".ps1"):
                (root / f"backend/scripts/bootstrap-authorization{suffix}").symlink_to(outside)
            result = run_authorization_bootstrap(
                root, _technical_plan(), application_config=_application_config()
            )
            self.assertEqual(result["failure_category"], "template_contract_invalid")
            self.assertIn("backend 目录内的普通文件", result["message"])
            run_mock.assert_not_called()

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_disabled_authorization_is_skipped(self, run_mock) -> None:
        """权限关闭时平台执行器不应要求模板脚本存在。"""

        with tempfile.TemporaryDirectory() as directory:
            result = run_authorization_bootstrap(
                directory,
                _technical_plan(),
                application_config=_application_config(False),
            )
            self.assertEqual(result["status"], "skipped")
            run_mock.assert_not_called()

    def test_graph_node_rejects_missing_confirmed_build_plan(self) -> None:
        """Graph 节点必须在数据库副作用前拒绝缺失的 Build DAG。"""

        with tempfile.TemporaryDirectory() as directory:
            result = authorization_bootstrap(
                {"workspace": directory, "technical_plan": _technical_plan()}
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                result["authorization_bootstrap_result"]["failure_category"],
                "build_plan_invalid",
            )

    @patch("app.services.authorization_bootstrap._bootstrap_environment")
    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_manual_modes_never_execute(self, run, environment) -> None:
        """人工模式在脚本校验、取密及进程执行之前返回完整 DDL。"""

        for mode in ("dbid", "static", "external_api", "unknown", "inconsistent"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = self._workspace(directory)
                application = root / ".devagentstudio/application.json"
                if mode in {"static", "external_api", "unknown"}:
                    application.write_text(json.dumps({"datasource": {"type": mode}}))
                elif mode == "dbid":
                    write_sources(root, [{"id": "db", "name": "DB", "type": "database", "mode": "dbid",
                                         "domain": "db.local", "port": 3306, "schema": "app", "userName": "app",
                                         "dbid": "private-token", "managedTables": []}])
                else:
                    application.write_text(json.dumps({"datasource": {"type": "database", "db": {"useBuiltin": True}}}))
                for script in (root / "backend/scripts").iterdir():
                    script.unlink()
                result = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
                self.assertEqual(result["status"], "manual_required")
                self.assertEqual(result["ddl"], "create table role (id int);")
                self.assertFalse(result["execution_attempted"])
                self.assertNotIn("private-token", json.dumps(result))
        run.assert_not_called()
        environment.assert_not_called()

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_switching_database_invalidates_success(self, run) -> None:
        """同一 Manifest 切库必须重新执行，切换 DBID 后不得复用旧成功。"""

        from app.persistence.data_sources import read_sources
        run.return_value = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            first = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
            sources = read_sources(root)
            sources[0]["schema"] = "another_database"
            write_sources(root, sources)
            second = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
            self.assertEqual(second["status"], "executed")
            self.assertNotEqual(first["datasource_identity_fingerprint"], second["datasource_identity_fingerprint"])
            sources[0].pop("passwordCiphertext")
            sources[0].update(mode="dbid", dbid="private-token")
            write_sources(root, sources)
            self.assertEqual(run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())["status"], "manual_required")
            self.assertEqual(run.call_count, 2)

    @patch("app.services.authorization_bootstrap._bootstrap_environment")
    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_builtin_keeps_runtime_gate(self, run, environment) -> None:
        """内置模式调用现有运行时连接流程，失败时禁止换库重试。"""

        from app.services.database_credentials import DatabaseCredentialError
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            (root / ".devagentstudio/application.json").write_text(json.dumps({
                "datasource": {"type": "database", "db": {"useBuiltin": True}},
                "managedDatabase": {"status": "REGISTERED", "datasourceKey": "012345abcdef", "datasourceId": 1},
            }))
            write_sources(root, [{"id": "builtin", "name": "内置", "type": "database", "mode": "builtin", "managedTables": []}])
            run.return_value = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            environment.return_value = {"SPRING_DATASOURCE_URL": "jdbc:mysql://managed/app"}
            self.assertEqual(run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())["status"], "executed")
            environment.assert_called_once()
            environment.side_effect = DatabaseCredentialError("secret")
            result = run_authorization_bootstrap(root, _technical_plan("sha256:new"), application_config=_application_config())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["ddl_available"])
            self.assertEqual(run.call_count, 1)
            self.assertNotIn("secret", json.dumps(result))

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_process_failures_continue_build(self, run) -> None:
        """脚本非零、超时及启动异常保留真实子状态，Graph 完成后路由 Build。"""

        import subprocess
        from app.graph.workflow import route_authorization_bootstrap
        outcomes = (("script_failed", SimpleNamespace(returncode=1, stdout=b"private", stderr=b"secret")),
                    ("timeout", subprocess.TimeoutExpired("script", 600, stderr=b"secret")),
                    ("process_start_failed", OSError("private")))
        for category, outcome in outcomes:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                root = self._workspace(directory)
                run.side_effect = outcome if isinstance(outcome, Exception) else None
                run.return_value = outcome
                result = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_category"], category)
                self.assertTrue(result["execution_attempted"])
                self.assertIn("ddl", result)
                self.assertNotIn("secret", json.dumps(result))
                self.assertFalse((root / ".devagentstudio/runtime/authorization-bootstrap/abc/result.json").exists())
                with patch("app.graph.nodes.authorization_bootstrap._build_plan_gate_error", return_value=""), patch(
                    "app.graph.nodes.authorization_bootstrap.read_application_config", return_value=_application_config()
                ), patch("app.graph.nodes.authorization_bootstrap.run_authorization_bootstrap", return_value=result):
                    node = authorization_bootstrap({"workspace": str(root)})
                self.assertEqual(node["status"], "completed")
                self.assertEqual(node["error"], "")
                self.assertEqual(node["authorization_bootstrap_result"]["status"], "failed")
                self.assertEqual(route_authorization_bootstrap(node), "build")

    def test_ddl_safety_limits(self) -> None:
        """拒绝超限、非法文本、外部符号链接及目录，不返回伪造 SQL。"""

        from app.services.authorization_bootstrap_policy import read_authorization_ddl
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = self._workspace(directory)
            ddl = root / "backend/sql/authorization/schema.sql"
            for content in (b"x" * (256 * 1024 + 1), b"\xff", b"", b"\x00"):
                ddl.write_bytes(content)
                self.assertFalse(read_authorization_ddl(root)["ddl_available"])
            ddl.unlink()
            target = Path(outside) / "secret.sql"
            target.write_text("outside secret")
            ddl.symlink_to(target)
            self.assertFalse(read_authorization_ddl(root)["ddl_available"])
            ddl.unlink()
            ddl.mkdir()
            self.assertFalse(read_authorization_ddl(root)["ddl_available"])

    def test_agui_projection_and_process_frame_share_safe_detail(self) -> None:
        """完成事件与实时步骤携带同一 DDL，状态摘要不复制 SQL 或原始日志。"""

        from ag_ui.encoder import EventEncoder
        from app.protocols.workflow.projection import _public_workflow_state, _workflow_node_detail
        from app.protocols.workflow.stream_events import _process_frame
        raw = {"status": "manual_required", "datasource_mode": "dbid", "execution_attempted": False,
               "requires_manual_action": True, "ddl_available": True, "ddl": "create table roles(id int);",
               "ddl_path": "backend/sql/authorization/schema.sql", "message": "需人工执行 DDL，Build 已继续。",
               "stderr": "private-secret"}
        update = {"status": "completed", "authorization_bootstrap_result": raw}
        detail = _workflow_node_detail("authorization_bootstrap", update)["data"]["authorizationBootstrap"]
        self.assertEqual(detail["ddl"], raw["ddl"])
        frame = _process_frame(EventEncoder(), id="bootstrap", kind="workflow", status="completed",
                               title="权限初始化", sequence=1, authorization_bootstrap=detail)
        payload = json.loads(frame.removeprefix("data: ").strip())
        self.assertEqual(payload["name"], "agent-process")
        self.assertEqual(payload["value"]["authorizationBootstrap"], detail)
        summary = _public_workflow_state(update)
        self.assertNotIn("ddl", summary["authorizationBootstrap"])
        self.assertNotIn("private-secret", json.dumps([summary, detail, payload]))

    @patch("app.graph.nodes.authorization_bootstrap.run_authorization_bootstrap")
    def test_invalid_dag_never_attempts_bootstrap(self, run) -> None:
        """DAG 未确认、版本错误、校验失败及范围不符均保持强阻断。"""

        from app.graph.workflow import route_authorization_bootstrap
        base = {"schema_version": "build-dag.v4", "status": "ready", "confirmation_status": "confirmed",
                "task_graph": {"validation": {"is_valid": True}},
                "build_execution_scope": {"type": "page", "targetId": "orders"}}
        for delta in ({"schema_version": "bad"}, {"confirmation_status": "draft"},
                      {"task_graph": {"validation": {"is_valid": False}}},
                      {"build_execution_scope": {"type": "page", "targetId": "other"}}):
            with self.subTest(delta=delta), patch("app.graph.nodes.authorization_bootstrap.load_build_task_plan_json", return_value={**base, **delta}):
                node = authorization_bootstrap({"workspace": "/workspace", "build_execution_scope": base["build_execution_scope"]})
                self.assertEqual(node["authorization_bootstrap_result"]["failure_category"], "build_plan_invalid")
                self.assertEqual(route_authorization_bootstrap(node), "handle_failure")
        run.assert_not_called()

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_direct_index_cannot_authorize_dbid_detail(self, run) -> None:
        """索引与详情模式矛盾时禁止以 direct 索引启动实际 DBID 连接。"""

        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            source_path = root / ".devagentstudio/datasource/databases/application-db.json"
            source = json.loads(source_path.read_text())
            source.pop("passwordCiphertext")
            source.update(mode="dbid", dbid="private-token")
            source_path.write_text(json.dumps(source))
            result = run_authorization_bootstrap(root, _technical_plan(), application_config=_application_config())
            self.assertEqual(result["status"], "manual_required")
            self.assertEqual(result["datasource_mode"], "unknown")
            run.assert_not_called()

    def test_completed_node_title_uses_real_bootstrap_result(self) -> None:
        """AG-UI 步骤标题按真实初始化子状态展示，不把节点放行误称为初始化成功。"""

        from app.protocols.workflow.runtime import _runtime_terminal_process_title
        for status, expected in (("executed", "权限数据库初始化成功"),
                                 ("reused", "权限数据库初始化成功（复用已有结果）"),
                                 ("manual_required", "权限数据库待手动初始化"),
                                 ("failed", "权限数据库初始化未完成"),
                                 ("skipped", "权限数据库初始化已跳过")):
            with self.subTest(status=status):
                self.assertEqual(_runtime_terminal_process_title("authorization_bootstrap", "completed", {
                    "authorization_bootstrap_result": {"status": status}
                }), expected)
        self.assertIn("执行失败", _runtime_terminal_process_title("authorization_bootstrap", "failed", {
            "authorization_bootstrap_result": {"status": "failed", "failure_category": "build_plan_invalid"}
        }))
