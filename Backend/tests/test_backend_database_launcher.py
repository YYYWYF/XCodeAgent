from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.persistence.data_sources import write_sources

from app.services import backend_process_registry
from app.services.backend_project_launcher import (
    _backend_runtime_environment,
    launch_backend_project,
)


def _write_application(workspace: Path, *, use_builtin: bool = False) -> None:
    """写入模拟开关及应用内数据库目录，启动器不读取应用级外部连接。"""

    application_file = workspace / ".devagentstudio" / "application.json"
    application_file.parent.mkdir(parents=True)
    application_file.write_text(
        json.dumps(
            {
                "datasource": {
                    "type": "database",
                    "db": (
                        {"useBuiltin": True}
                        if use_builtin
                        else {"useBuiltin": False}
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    if not use_builtin:
        write_sources(workspace, [{
            "id": "application-db", "name": "应用数据库", "type": "database", "mode": "dbid",
            "domain": "application.mysql.local", "port": 3313, "userName": "application_user",
            "dbid": "application-password", "schema": "application_schema", "managedTables": [],
        }])


class BackendDatabaseLauncherTests(unittest.TestCase):
    """验证 Java 启动器的应用级数据库环境绑定。"""

    def setUp(self) -> None:
        """清空测试之间共享的 Java 进程登记。"""

        backend_process_registry._BACKEND_PROCESSES.clear()
        backend_process_registry._BACKEND_LAUNCH_LOCKS.clear()

    def tearDown(self) -> None:
        """清空测试结束后残留的模拟 Java 进程登记。"""

        backend_process_registry._BACKEND_PROCESSES.clear()
        backend_process_registry._BACKEND_LAUNCH_LOCKS.clear()

    def test_runtime_environment_binds_application_and_overrides_global_values(self) -> None:
        """当前应用配置必须覆盖后端服务继承的全局数据库变量。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _write_application(workspace)
            with patch.dict(
                "app.services.backend_project_launcher.os.environ",
                {
                    "MYSQL_HOST": "global.mysql.local",
                    "MYSQL_PORT": "3306",
                    "MYSQL_USER": "global_user",
                    "MYSQL_PWD": "global-password",
                    "MYSQL_DATABASE": "global_schema",
                    "SPRING_DATASOURCE_URL": "jdbc:mysql://global.mysql.local/global_schema",
                },
            ):
                environment, error = _backend_runtime_environment(workspace)

        self.assertIsNone(error)
        self.assertEqual(environment["MYSQL_HOST"], "application.mysql.local")
        self.assertEqual(environment["MYSQL_PORT"], "3313")
        self.assertEqual(environment["MYSQL_USER"], "application_user")
        self.assertEqual(environment["MYSQL_PWD"], "application-password")
        self.assertEqual(environment["MYSQL_DATABASE"], "application_schema")
        self.assertIn(
            "application.mysql.local:3313/application_schema",
            environment["SPRING_DATASOURCE_URL"],
        )

    def test_runtime_environment_without_application_drops_global_database_values(self) -> None:
        """没有应用配置时也不能把后端服务的全局数据库变量传给 Java。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            with patch.dict(
                "app.services.backend_project_launcher.os.environ",
                {
                    "MYSQL_HOST": "global.mysql.local",
                    "MYSQL_PORT": "3306",
                    "MYSQL_USER": "global_user",
                    "MYSQL_PWD": "global-password",
                    "MYSQL_DATABASE": "global_schema",
                    "SPRING_DATASOURCE_URL": "jdbc:mysql://global.mysql.local/global_schema",
                },
            ):
                environment, error = _backend_runtime_environment(Path(temporary_root))

        self.assertIsNone(error)
        self.assertNotIn("MYSQL_HOST", environment)
        self.assertNotIn("MYSQL_PORT", environment)
        self.assertNotIn("MYSQL_USER", environment)
        self.assertNotIn("MYSQL_PWD", environment)
        self.assertNotIn("MYSQL_DATABASE", environment)
        self.assertNotIn("SPRING_DATASOURCE_URL", environment)

    def test_runtime_environment_without_connection_does_not_require_database(self) -> None:
        """未使用模拟数据库且未填写连接时允许启动，并隔离 Studio 的数据库环境。"""

        for datasource in ({"type": "database"}, {"type": "database", "db": {"useBuiltin": False}}):
            with self.subTest(datasource=datasource), tempfile.TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                application_file = workspace / ".devagentstudio" / "application.json"
                application_file.parent.mkdir()
                application_file.write_text(json.dumps({"datasource": datasource}), encoding="utf-8")
                with (
                    patch.dict("os.environ", {"MYSQL_HOST": "studio-only.local", "MYSQL_PWD": "studio-only"}),
                    patch("app.services.backend_launch_support.resolve_runtime_mysql_config") as resolve,
                ):
                    environment, error = _backend_runtime_environment(workspace)

                self.assertIsNone(error)
                self.assertNotIn("MYSQL_HOST", environment)
                self.assertNotIn("MYSQL_PWD", environment)
                resolve.assert_not_called()

    def test_launch_rejects_invalid_application_database_config_before_maven(self) -> None:
        """应用数据库配置不可解析时，启动器必须在 Maven 前失败。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            backend = workspace / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_application(workspace)
            source_file = workspace / ".devagentstudio/datasource/databases/application-db.json"
            source = json.loads(source_file.read_text(encoding="utf-8"))
            source["schema"] = ""
            source_file.write_text(json.dumps(source), encoding="utf-8")
            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    side_effect=["/usr/bin/mvn", "/usr/bin/java"],
                ),
                patch("app.services.backend_project_launcher.subprocess.run") as run,
                patch("app.services.backend_project_launcher.subprocess.Popen") as popen,
            ):
                result = launch_backend_project(workspace)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], "backend_database_config")
        self.assertIn("数据库配置", result["message"])
        run.assert_not_called()
        popen.assert_not_called()

    def test_unreadable_application_is_config_failure_before_maven(self) -> None:
        """应用配置损坏时不能在尚未确定模式前归类为 SaaS 资源错误。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            backend = workspace / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_application(workspace)
            (workspace / ".devagentstudio/application.json").write_text("{invalid-json", encoding="utf-8")
            with patch("app.services.backend_project_launcher.shutil.which", side_effect=["/usr/bin/mvn", "/usr/bin/java"]), \
                 patch("app.services.backend_project_launcher.subprocess.run") as run, \
                 patch("app.services.backend_project_launcher.subprocess.Popen") as popen:
                result = launch_backend_project(workspace)
        self.assertEqual(result["failed_stage"], "backend_database_config")
        run.assert_not_called()
        popen.assert_not_called()

    def test_launch_passes_application_database_environment_to_java(self) -> None:
        """Java 进程启动时必须收到当前应用的数据库环境。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            backend = workspace / "backend"
            target = backend / "target"
            target.mkdir(parents=True)
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            jar_path = target / "testApp-1.0.0-SNAPSHOT.jar"
            jar_path.write_bytes(b"jar")
            fake_process = SimpleNamespace(pid=24681, poll=lambda: None)
            with (
                patch.dict(
                    "app.services.backend_project_launcher.os.environ",
                    {
                        "MYSQL_HOST": "global.mysql.local",
                        "MYSQL_PORT": "3306",
                        "MYSQL_USER": "global_user",
                        "MYSQL_PWD": "global-password",
                        "MYSQL_DATABASE": "global_schema",
                    },
                ),
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    side_effect=["/usr/bin/mvn", "/usr/bin/java"],
                ),
                patch(
                    "app.services.backend_project_launcher.workspace_process_registry.run",
                    return_value=SimpleNamespace(returncode=0, stdout="built", stderr=""),
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ) as popen,
                patch(
                    "app.services.backend_project_launcher._wait_for_backend_ready",
                    return_value=True,
                ),
            ):
                _write_application(workspace)
                result = launch_backend_project(workspace)

        self.assertEqual(result["status"], "running")
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["MYSQL_HOST"], "application.mysql.local")
        self.assertEqual(environment["MYSQL_PORT"], "3313")
        self.assertEqual(environment["MYSQL_USER"], "application_user")
        self.assertEqual(environment["MYSQL_PWD"], "application-password")
        self.assertEqual(environment["MYSQL_DATABASE"], "application_schema")
        self.assertNotEqual(environment["MYSQL_HOST"], "global.mysql.local")


if __name__ == "__main__":
    unittest.main()
