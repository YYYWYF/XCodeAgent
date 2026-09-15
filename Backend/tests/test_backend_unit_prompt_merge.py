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
