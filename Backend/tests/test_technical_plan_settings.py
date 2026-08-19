from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings


class TechnicalPlanSettingsTests(unittest.TestCase):
    """验证技术规划输出预算独立于其他模型调用且拒绝非法配置。"""

    def _settings(self, **overrides: str) -> Settings:
        """在隔离环境中读取设置，不使用开发者真实模型凭据。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_BASE_URL": "https://example.invalid/v1",
                "MODEL_API_KEY": "test-only",
                "MODEL_NAME": "test-model",
                "AGENT_MAX_TOKENS": "8192",
                **overrides,
            },
            clear=True,
        ):
            return Settings.from_env()

    def test_default_budget_does_not_change_global_budget(self) -> None:
        """技术规划默认使用独立预算，不提高其他 Agent 的输出上限。"""

        settings = self._settings()
        self.assertEqual(settings.technical_plan_max_tokens, 32768)
        self.assertEqual(settings.default_max_tokens, 8192)

    def test_budget_can_be_configured_independently(self) -> None:
        """环境变量只覆盖技术规划预算。"""

        settings = self._settings(XCODEAGENT_TECHNICAL_PLAN_MAX_TOKENS="49152")
        self.assertEqual(settings.technical_plan_max_tokens, 49152)
        self.assertEqual(settings.default_max_tokens, 8192)

    def test_non_positive_or_non_integer_budget_is_rejected(self) -> None:
        """非法预算在加载配置时显式失败，不能带入模型请求。"""

        for value in ("0", "-1", "invalid", "1.5"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, "XCODEAGENT_TECHNICAL_PLAN_MAX_TOKENS"
                ):
                    self._settings(XCODEAGENT_TECHNICAL_PLAN_MAX_TOKENS=value)
