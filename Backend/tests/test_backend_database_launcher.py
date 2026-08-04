from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import backend_process_registry
from app.services.backend_project_launcher import (
    _backend_runtime_environment,
    launch_backend_project,
)


def _write_application(workspace: Path, *, use_builtin: bool = False) -> None:
    """写入启动器测试所需的应用数据库配置。"""

    application_file = workspace / ".xcodeagent" / "application.json"
    application_file.parent.mkdir(parents=True)
    application_file.write_text(
        json.dumps(
            {
                "datasource": {
                    "type": "DataBase",
                    "db": (
                        {"useBuiltin": True}
                        if use_builtin
                        else {
                            "useBuiltin": False,
                            "plantMode": {
                                "domain": "application.mysql.local",
                                "port": 3313,
                                "userName": "application_user",
                                "pwd": "application-password",
                                "schema": "application_schema",
                            },
                        }
                    ),
                }
            }
        ),
        encoding="utf-8",
    )


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

    def test_launch_rejects_invalid_application_database_config_before_maven(self) -> None:
        """应用数据库配置不可解析时，启动器必须在 Maven 前失败。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            backend = workspace / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_application(workspace, use_builtin=True)
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
        self.assertIn("平台内置数据库", result["message"])
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
                    "app.services.backend_project_launcher.subprocess.run",
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
