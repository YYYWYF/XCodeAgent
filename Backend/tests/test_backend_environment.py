"""多环境加载边界测试，所有配置均来自临时目录，不读取真实凭据。"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import environment


class BackendEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        """隔离配置目录、环境变量和进程内加载缓存。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.backend = self.root / "Backend"
        self.backend.mkdir()
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch.object(environment, "__file__", str(self.backend / "app/environment.py")))
        environment.load_backend_environment.cache_clear()
        self.addCleanup(environment.load_backend_environment.cache_clear)

    def write_env(self, app_env: str, content: str = "VALUE=from-file\n") -> Path:
        """创建当前用例使用的假环境文件。"""
        file = self.backend / (".env" if app_env == "dev" else f".env.{app_env}")
        file.write_text(content, encoding="utf-8")
        return file

    def test_all_environments_select_only_their_file_and_directory(self) -> None:
        """四环境不叠加 dev 配置，并绑定各自的数据目录。"""
        self.write_env("dev", "DEV_ONLY=must-not-leak\nVALUE=dev\n")
        for app_env, directory in environment.ENVIRONMENT_DIRECTORIES.items():
            with self.subTest(app_env=app_env), patch.dict(os.environ, {"APP_ENV": app_env}, clear=True):
                environment.load_backend_environment.cache_clear()
                file = self.write_env(app_env, f"VALUE={app_env}\n" + ("DEV_ONLY=dev\n" if app_env == "dev" else ""))
                selected = environment.load_backend_environment()
                self.assertEqual(selected.env_file, file)
                self.assertEqual(selected.app_env, app_env)
                self.assertEqual(os.environ["VALUE"], app_env)
                self.assertEqual(os.environ["DEVAGENTSTUDIO_WORKING_DIR"], directory)
                if app_env != "dev":
                    self.assertNotIn("DEV_ONLY", os.environ)

    def test_default_is_dev(self) -> None:
        """无选择变量时读取开发文件。"""
        self.write_env("dev")
        self.assertEqual(environment.load_backend_environment().app_env, "dev")

    def test_file_wins_and_unconfigured_process_values_remain(self) -> None:
        """业务值以文件为准，未声明值仍可由进程提供。"""
        self.write_env("dev", 'VALUE=file\nMULTILINE="first\\nsecond"\nREF=${VALUE}\n')
        os.environ.update(VALUE="process", OTHER="injected")
        environment.load_backend_environment()
        self.assertEqual(os.environ["VALUE"], "file")
        self.assertEqual(os.environ["OTHER"], "injected")
        self.assertEqual(os.environ["MULTILINE"], "first\nsecond")
        self.assertEqual(os.environ["REF"], "file")

    def test_startup_controls_cannot_be_replaced_by_file(self) -> None:
        """文件不能修改环境、配置来源、主机、端口、编码或工作目录。"""
        file = self.write_env("st", "\n".join(
            f"{name}=wrong"
            for key in environment.STARTUP_VARIABLES for name in (key, key.lower())
        ))
        os.environ.update(APP_ENV="st", DEVAGENTSTUDIO_BACKEND_HOST="127.0.0.1",
                          DEVAGENTSTUDIO_BACKEND_PORT="49152", PYTHONUTF8="1",
                          DEVAGENTSTUDIO_WORKING_DIR=".devagentstudio_dev")
        environment.load_backend_environment()
        self.assertEqual(os.environ["APP_ENV"], "st")
        self.assertEqual(os.environ["DEVAGENTSTUDIO_BACKEND_ENV_FILE"], str(file))
        self.assertEqual(os.environ["DEVAGENTSTUDIO_BACKEND_HOST"], "127.0.0.1")
        self.assertEqual(os.environ["DEVAGENTSTUDIO_BACKEND_PORT"], "49152")
        self.assertEqual(os.environ["DEVAGENTSTUDIO_WORKING_DIR"], ".devagentstudio_st")
        self.assertEqual(os.environ["PYTHONUTF8"], "1")
        self.assertNotIn("devagentstudio_backend_port", dict(os.environ))

    def test_explicit_path_is_the_only_file_loaded(self) -> None:
        """显式路径优先，目标标准文件和 dev 文件不参与加载。"""
        file = self.root / "custom.env"
        file.write_text("VALUE=explicit\n", encoding="utf-8")
        self.write_env("dev", "DEV_ONLY=wrong\n")
        self.write_env("prd", "PRD_ONLY=wrong\n")
        os.environ.update(APP_ENV="prd", DEVAGENTSTUDIO_BACKEND_ENV_FILE=str(file))
        self.assertEqual(environment.load_backend_environment().env_file, file)
        self.assertEqual(os.environ["VALUE"], "explicit")
        self.assertNotIn("DEV_ONLY", os.environ)
        self.assertNotIn("PRD_ONLY", os.environ)

    def test_missing_target_or_explicit_file_fails_without_fallback(self) -> None:
        """标准和显式目标缺失均失败，不读取存在的开发文件。"""
        self.write_env("dev")
        for variables in ({"APP_ENV": "st"}, {"DEVAGENTSTUDIO_BACKEND_ENV_FILE": str(self.root / "missing")}):
            with self.subTest(variables=variables), patch.dict(os.environ, variables, clear=True):
                with self.assertRaisesRegex(RuntimeError, "Missing backend configuration"):
                    environment.load_backend_environment()
                self.assertNotIn("VALUE", os.environ)

    def test_invalid_environments_fail(self) -> None:
        """不接受空环境、大小写别名或任意环境名。"""
        for value in ("", "ST", "production", "../prd"):
            with self.subTest(value=value), patch.dict(os.environ, {"APP_ENV": value}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "APP_ENV must"):
                    environment.load_backend_environment()

    def test_frozen_root_is_executable_directory(self) -> None:
        """冻结运行只从可执行文件旁读取目标环境文件。"""
        file = self.root / ".env.uat"
        file.write_text("VALUE=frozen\n", encoding="utf-8")
        os.environ["APP_ENV"] = "uat"
        with patch.object(environment.sys, "frozen", True, create=True), patch.object(
            environment.sys, "executable", str(self.root / "devagentstudio-backend")
        ):
            self.assertEqual(environment.load_backend_environment().env_file, file)
            self.assertEqual(os.environ["VALUE"], "frozen")

    def test_loading_is_independent_of_cwd_and_idempotent(self) -> None:
        """工作目录和文件后续变化不改变进程内已加载的身份与配置。"""
        file = self.write_env("dev")
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            selected = environment.load_backend_environment()
            file.write_text("VALUE=changed\n", encoding="utf-8")
            self.assertIs(environment.load_backend_environment(), selected)
            self.assertEqual(os.environ["VALUE"], "from-file")
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
