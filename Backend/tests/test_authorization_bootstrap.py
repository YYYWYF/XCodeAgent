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

    def _workspace(self, directory: str) -> Path:
        """创建模板脚本执行器要求的最小生成项目目录。"""

        root = Path(directory)
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
            "id": "application-db", "name": "应用数据库", "type": "database", "mode": "dbid",
            "domain": "application.mysql.local", "port": 3313, "schema": "application_schema",
            "userName": "application_user", "dbid": "application-password", "managedTables": [],
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
        """脚本失败必须阻断 Build，且不能写入可复用成功标记。"""

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
            self.assertEqual(result["message"], "权限 Bootstrap 缺少 backend/sql/authorization/schema.sql。")
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
                self.assertEqual(result["message"], "权限 Bootstrap 缺少 backend/sql/authorization/schema.sql。")
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
