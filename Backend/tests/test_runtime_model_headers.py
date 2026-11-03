"""独立 Runtime 的模型请求头配置传递与配置失效回归。"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.config import Settings
from app.services.agent_runtime_environment import build_runtime_environment
from app.services.python_runtime_checks import runtime_source_fingerprint


class RuntimeModelHeadersTests(TestCase):
    """验证 Runtime 使用 Studio 显式开关，不继承宿主残留配置。"""

    def test_launch_forwards_enabled_and_disabled_switch(self) -> None:
        """预览、调试和探针共用的启动环境必须显式传递开关的两种状态。"""
        for enabled in (False, True):
            settings = Settings(model_base_url="https://example.com/v1", model_api_key="test-key",
                                model_name="test-model", model_opencode_go_headers_enabled=enabled)
            with self.subTest(enabled=enabled), patch(
                "app.services.agent_runtime_environment.load_application_json",
                return_value={"auth": {"enable": False}, "authorization": {"enabled": False}},
            ), patch("app.services.agent_runtime_environment.runtime_database_environment", return_value={}), patch(
                "app.services.python_runtime_checks.runtime_check_plan", return_value={},
            ), patch("app.services.runtime_execution_policy.verify_execution_policy", return_value="policy"), patch.dict(
                "os.environ", {"MODEL_OPENCODE_GO_HEADERS_ENABLED": str(not enabled).lower()},
            ):
                environment = build_runtime_environment(Path("/tmp/runtime-headers"), port=12345,
                                                        launch_id="test", snapshot=None, settings=settings)
            self.assertEqual(environment["MODEL_OPENCODE_GO_HEADERS_ENABLED"], str(enabled).lower())

    def test_switch_change_invalidates_runtime_fingerprint(self) -> None:
        """只改变请求头开关也必须使启动校验缓存及调试源码摘要失效。"""
        settings = Settings(model_base_url="https://example.com/v1", model_api_key="test-key", model_name="test-model")
        with TemporaryDirectory() as directory, patch("app.config.Settings.from_env", return_value=settings) as current:
            root = Path(directory)
            disabled = runtime_source_fingerprint(root)
            current.return_value = replace(settings, model_opencode_go_headers_enabled=True)
            enabled = runtime_source_fingerprint(root)
            self.assertNotEqual(disabled, enabled)
            current.return_value = settings
            self.assertEqual(disabled, runtime_source_fingerprint(root))
