from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services.ui_design_generator import generate_page_react_code


class UiDesignSettingsTests(unittest.TestCase):
    def test_settings_expose_ui_design_defaults(self) -> None:
        """UI 设计生成配置缺省时应提供足够的输出上限与两次修复机会。"""

        environment = {
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_API_KEY": "test-key",
            "MODEL_NAME": "test-model",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.ui_design_max_tokens, 8192)
        self.assertEqual(settings.ui_design_max_retries, 2)

    def test_settings_read_ui_design_environment_overrides(self) -> None:
        """UI 设计生成配置应允许通过独立环境变量覆盖默认值。"""

        environment = {
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_API_KEY": "test-key",
            "MODEL_NAME": "test-model",
            "UI_DESIGN_MAX_TOKENS": "12288",
            "UI_DESIGN_MAX_RETRIES": "3",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.ui_design_max_tokens, 12288)
        self.assertEqual(settings.ui_design_max_retries, 3)


class UiDesignGeneratorTests(unittest.TestCase):
    def test_generation_binds_dedicated_ui_design_token_limit(self) -> None:
        """单页设计稿生成必须绑定 UI 设计专用输出上限。"""

        model = MagicMock()
        model.bind.return_value.invoke.return_value = SimpleNamespace(
            content="const MoviePage = () => <div>电影</div>; export default MoviePage;"
        )
        settings = SimpleNamespace(
            ui_design_max_tokens=12288,
            ui_design_max_retries=2,
        )
        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=settings,
        ), patch(
            "app.services.ui_design_generator.create_chat_model",
            return_value=model,
        ), patch(
            "app.services.ui_design_generator.validate_page_code",
            return_value=(True, ""),
        ):
            code = generate_page_react_code(
                {"pageId": "movie_page", "name": "电影页"},
                "MoviePage",
            )

        model.bind.assert_called_once_with(max_tokens=12288)
        self.assertIn("export default MoviePage", code)


if __name__ == "__main__":
    unittest.main()
