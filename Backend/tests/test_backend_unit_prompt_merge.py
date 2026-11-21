"""验证本地后端生成规则已接入重构后的 Unit 规划入口。"""

import unittest

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from tests.unit_prompt_contexts import _backend_endpoint_context


class BackendUnitPromptMergeTests(unittest.TestCase):
    """覆盖数据库优先使用 BaseMapper 和业务错误码的规划约束。"""

    def test_database_prompt_preserves_mapper_and_error_code_rules(self) -> None:
        """数据库 Endpoint 同时获得受限 XML 和显式错误码文件规划规则。"""
        prompt = build_unit_generation_prompt(_backend_endpoint_context("database"))
        for expected in (
            # 当前措辞：优先 BaseMapper，且要求不得留下占位 XML。
            "Use BaseMapper and LambdaQueryWrapper/LambdaUpdateWrapper",
            "No placeholder XML",
            "ErrorCode", "IBizErrorCode", "confirmed business failure",
            "ApplicationService",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_external_api_prompt_keeps_error_codes_without_database_work(self) -> None:
        """纯外部接口仍规划业务错误码，但不引入数据库 Mapper 工作。"""
        prompt = build_unit_generation_prompt(_backend_endpoint_context("external_api"))
        self.assertIn("ErrorCode", prompt)
        self.assertNotIn("Use BaseMapper and LambdaQueryWrapper/LambdaUpdateWrapper", prompt)

    def test_value_rules_reach_both_endpoint_planning_prompts(self) -> None:
        """两种单来源规划都保留"按源绑定取值、不另立逐字段任务"的规划语义。"""
        for source in ("database", "external_api"):
            with self.subTest(source=source):
                prompt = build_unit_generation_prompt(_backend_endpoint_context(source))
                for expected in (
                    # 物理来源由确认的绑定固定，模型不得自造来源。
                    "sourceBinding",
                    "confirmed fieldMappings",
                    # 每个文件只声明自己真正实现的职责，不为了凑覆盖乱认领。
                    "Each file covers only responsibilities it actually implements",
                ):
                    self.assertIn(expected, prompt)
                self.assertNotIn("【字段取值契约】", prompt)
        # 来源专属的取值绑定只出现在对应来源的提示词里。
        self.assertNotIn(
            "externalApiBindings",
            build_unit_generation_prompt(_backend_endpoint_context("database")),
        )
        self.assertIn(
            "externalApiBindings",
            build_unit_generation_prompt(_backend_endpoint_context("external_api")),
        )
