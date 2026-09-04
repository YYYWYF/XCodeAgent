"""T1.3 DAG 专属配置默认值、覆盖、非法输入和全局参数隔离回归。"""

from dataclasses import asdict
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

from app.config import Settings


_MODEL_ENV = {
    "MODEL_BASE_URL": "https://example.com/v1",
    "MODEL_API_KEY": "test-key",
    "MODEL_NAME": "test-model",
}
_DAG_FIELDS = {
    "XCODEAGENT_DAG_UNIT_MAX_TOKENS": ("dag_unit_max_tokens", 4096),
    "XCODEAGENT_DAG_UNIT_CONCURRENCY": ("dag_unit_generation_concurrency", 3),
    "XCODEAGENT_DAG_UNIT_LOCAL_MAX_ATTEMPTS": ("dag_unit_local_max_attempts", 3),
    "XCODEAGENT_DAG_GLOBAL_REPAIR_LIMIT": ("dag_global_repair_limit", 2),
}


def _settings(**overrides: str) -> Settings:
    """在隔离环境中加载测试配置，避免本机 .env 和真实凭据影响断言。"""

    with patch.dict(os.environ, {**_MODEL_ENV, **overrides}, clear=True):
        return Settings.from_env()


class DagSettingsTests(unittest.TestCase):
    def test_defaults_match_for_environment_and_direct_construction(self) -> None:
        """环境缺省与直接构造均提供 DAG=4096/3/3/2，保留全局原默认值。"""

        for settings in (_settings(), Settings(
            model_base_url=_MODEL_ENV["MODEL_BASE_URL"],
            model_api_key=_MODEL_ENV["MODEL_API_KEY"], model_name=_MODEL_ENV["MODEL_NAME"],
        )):
            for field, expected in _DAG_FIELDS.values():
                self.assertEqual(getattr(settings, field), expected)
            self.assertEqual(settings.default_max_tokens, 2048)
            self.assertEqual(settings.model_max_retries, 2)
            self.assertEqual(settings.build_task_plan_max_retries, 2)

    def test_each_environment_override_changes_only_its_own_field(self) -> None:
        """逐个覆盖 DAG 配置，其他所有 Settings 字段保持原值。"""

        defaults = asdict(_settings())
        for variable, (field, _) in _DAG_FIELDS.items():
            with self.subTest(variable=variable):
                settings = _settings(**{variable: "7"})
                self.assertEqual(asdict(settings), {**defaults, field: 7})

    def test_dag_overrides_do_not_change_global_agent_or_existing_build_settings(self) -> None:
        """同时覆盖 DAG 配置，不改变全局模型、UI 或旧 Build 重试参数。"""

        globals_env = {
            "AGENT_MAX_TOKENS": "8192", "MODEL_MAX_RETRIES": "5",
            "MODEL_TIMEOUT_SECONDS": "90", "BUILD_TASK_PLAN_MAX_RETRIES": "4",
            "XCODEAGENT_UI_DESIGN_MAX_TOKENS": "12000", "XCODEAGENT_UI_DESIGN_CONCURRENCY": "2",
        }
        expected = asdict(_settings(**globals_env))
        overrides = dict(zip(_DAG_FIELDS, ("6000", "1", "2", "0")))
        settings = _settings(**globals_env, **overrides)
        for variable, value in overrides.items():
            expected[_DAG_FIELDS[variable][0]] = int(value)
        self.assertEqual(asdict(settings), expected)
        self.assertEqual(settings.default_max_tokens, 8192)
        self.assertEqual(settings.model_max_retries, 5)

    def test_global_overrides_do_not_change_dag_defaults(self) -> None:
        """只修改全局参数不会改变四个 DAG 默认值。"""

        settings = _settings(AGENT_MAX_TOKENS="16384", MODEL_MAX_RETRIES="0", BUILD_TASK_PLAN_MAX_RETRIES="8")
        for field, expected in _DAG_FIELDS.values():
            self.assertEqual(getattr(settings, field), expected)
        self.assertEqual(settings.default_max_tokens, 16384)
        self.assertEqual(settings.model_max_retries, 0)
        self.assertEqual(settings.build_task_plan_max_retries, 8)

    def test_invalid_integer_text_is_rejected_with_variable_name(self) -> None:
        """非整数、空值、浮点数及布尔文本不得静默回退，错误必须定位变量。"""

        for variable in _DAG_FIELDS:
            for value in ("", " ", "abc", "1.5", "3.0", "true", "NaN"):
                with self.subTest(variable=variable, value=value), self.assertRaisesRegex(ValueError, variable):
                    _settings(**{variable: value})

    def test_negative_values_and_zero_positive_budgets_are_rejected(self) -> None:
        """所有配置拒绝负数，token、并发和 Local 总尝试次数另拒绝零。"""

        for variable in _DAG_FIELDS:
            invalid_values = ("-1",) if variable == "XCODEAGENT_DAG_GLOBAL_REPAIR_LIMIT" else ("-1", "0")
            for value in invalid_values:
                with self.subTest(variable=variable, value=value), self.assertRaisesRegex(ValueError, variable):
                    _settings(**{variable: value})

    def test_minimum_values_support_serial_generation_and_no_global_repair(self) -> None:
        """允许并发为 1 的验证配置和 Global=0，保持配置可显式覆盖。"""

        settings = _settings(
            XCODEAGENT_DAG_UNIT_MAX_TOKENS="1", XCODEAGENT_DAG_UNIT_CONCURRENCY="1",
            XCODEAGENT_DAG_UNIT_LOCAL_MAX_ATTEMPTS="1", XCODEAGENT_DAG_GLOBAL_REPAIR_LIMIT="0",
        )
        self.assertEqual([getattr(settings, field) for field, _ in _DAG_FIELDS.values()], [1, 1, 1, 0])

    def test_example_environment_documents_exact_names_and_defaults(self) -> None:
        """示例配置与 Settings 默认值一致，只读取 .env.example，不读取本机 .env。"""

        example = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example", interpolate=False)
        for variable, (_, expected) in _DAG_FIELDS.items():
            self.assertEqual(example[variable], str(expected))
