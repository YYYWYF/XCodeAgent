"""验证本地后端生成规则已接入重构后的 Unit 规划入口。"""

import unittest

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from tests.test_unit_task_prompt import _backend_endpoint_context


class BackendUnitPromptMergeTests(unittest.TestCase):
    """覆盖数据库优先使用 BaseMapper 和业务错误码的规划约束。"""

    def test_database_prompt_preserves_mapper_and_error_code_rules(self) -> None:
        """数据库 Endpoint 同时获得受限 XML 和显式错误码文件规划规则。"""
        prompt = build_unit_generation_prompt(_backend_endpoint_context("database"))
        for expected in (
            "BaseMapper<PO>", "LambdaQueryWrapper/LambdaUpdateWrapper",
            "namespace-only Mapper XML placeholder", "name that reason",
            "<Module>ErrorCode.java", "IBizErrorCode", "confirmed business failure",
            "change_scope, allowed_paths, target_files", "ApplicationService",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_external_api_prompt_keeps_error_codes_without_database_work(self) -> None:
        """纯外部接口仍规划业务错误码，但不引入数据库 Mapper 工作。"""
        prompt = build_unit_generation_prompt(_backend_endpoint_context("external_api"))
        self.assertIn("<Module>ErrorCode.java", prompt)
        self.assertNotIn("BaseMapper<PO>", prompt)

    def test_value_rules_reach_both_endpoint_planning_prompts(self) -> None:
        """两种单来源规划均保留取值阶段和缺值语义，外部入参绑定不再被禁止。"""
        for source in ("database", "external_api"):
            with self.subTest(source=source):
                prompt = build_unit_generation_prompt(_backend_endpoint_context(source))
                for expected in (
                    "sourceBinding identifies the selected table or external Operation",
                    "origin is an editor category, not a runtime algorithm",
                    "databaseQuery right values", "pre-call or post-call phase",
                    "no per-field Task", "require rejecting update/delete when omit removes all effective predicates",
                    "externalApiBindings values to external request targets",
                ):
                    self.assertIn(expected, prompt)
                self.assertNotIn("must not create a separate mapping Task, bind confirmed externalApiBindings", prompt)
                self.assertNotIn("【字段取值契约】", prompt)
