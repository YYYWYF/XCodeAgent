"""核对 Studio 辅助请求、英文标识归一及防止上下文误识别。"""

import unittest

from pydantic import ValidationError

from app.observation_models import Identity
from app.operation_detection import detect_operation


class OperationDetectionTests(unittest.TestCase):
    """以当前业务提示样本检查曾遗漏的调用，不依赖模型或 Studio 运行环境。"""

    def operation(self, prompt, role="user", extensions=None):
        """构造单次实际模型输入，仅在指定角色携带当前指令。"""
        return detect_operation({"messages": [{"role": role, "content": prompt}]}, extensions or {})

    def test_planning_auxiliary_requests_have_specific_operations(self):
        """权限分类、事实提取、设计路由与规划修复各自对应独立英文操作。"""
        samples = (
            ("Classify whether this application requirement explicitly describes authorization, not authentication or business flows. Return exactly one JSON object.", "classify_authorization_requirements"),
            ("You extract explicit authorization business facts for an application requirement. Return exactly one JSON object without markdown and never call tools.", "extract_authorization_facts"),
            ("You are the dedicated design-conversation routing agent for DevAgent Studio. Your only job is to classify product conversation semantics.", "classify_design_intent"),
            ("You handle conversation in the Technical Planning stage of DevAgent Studio. Return exactly JSON.", "handle_technical_planning_conversation"),
            ("You repair only missing TechnicalPlan business Action-to-Endpoint selections. First determine whether every issue can be correctly implemented using only the existing Endpoint catalog.", "repair_technical_action_bindings"),
            ("You repair API Contracts inside an existing TechnicalPlan. Return exactly one JSON object.", "repair_technical_api_contracts"),
            ("You synchronize a user-edited RequirementSpec Markdown document back into its internal JSON. This is a document-sync boundary.", "sync_requirement_spec"),
            ("You synchronize a user-edited ProductPlan Markdown document back into its internal JSON.", "sync_product_plan"),
            ("You synchronize a user-edited TechnicalPlan Markdown document back into its internal JSON.", "sync_technical_plan"),
        )
        for prompt, expected in samples:
            with self.subTest(operation=expected):
                self.assertEqual(self.operation(prompt), expected)

    def test_ui_generation_repair_continuation_and_adjustment_are_distinguished(self):
        """同一页面多次模型请求的生成、修复、续写和修改不混为同一动作。"""
        samples = (
            ("You are a UI design code generation model for an app-generation workflow. Generate ONE self-contained React page.", "generate_ui_design"),
            ("You previously generated a React + antd5 + @ant-design/pro-components .tsx page for the page below, but it failed validation. Fix the issues.", "repair_ui_design"),
            ("You previously generated a React + antd5 + @ant-design/pro-components .tsx page for the page below. The user has reviewed it and requested specific adjustments.", "adjust_ui_design"),
            ("You previously started generating a React + antd5 + @ant-design/pro-components .tsx page for the page below, but the output was CUT OFF before completion because the output token limit was reached mid-file.", "continue_ui_design"),
            ("You are deciding which pages need UI design adjustments based on a user's natural-language instruction.", "select_ui_design_targets"),
        )
        for prompt, expected in samples:
            with self.subTest(operation=expected):
                self.assertEqual(self.operation(prompt), expected)

    def test_current_actor_and_chinese_mapping_templates_are_supported(self):
        """覆盖开发、测试、审查、任务规划及中文接口映射入口。"""
        samples = (
            ("You are the TestGeneration Agent. Generate or update only the smallest set of tests.", "generate_tests"),
            ("You are the CodeAnalyze Agent. You perform a read-only security and quality review.", "review_code"),
            ("You are the CodeReviewRepairAgent. The user has explicitly chosen one-click repair.", "repair_code_review"),
            ("You are the SmallTask Coding Agent shared by the workbench repair flow and free chat.", "modify_code"),
            ("You are DevAgent Studio's read-only Workspace Assistant. Answer questions about the current workspace.", "answer_workspace_question"),
            ("## 1. Single Unit Role & Boundary\nPlan exactly one Unit and no other Unit. This attempt is isolated from every other concurrent Candidate.", "generate_unit_tasks"),
            ("【角色与边界】\n你是单表数据库操作识别助手，只使用本次接口概要、需求说明、补充说明和当前表身份。", "identify_database_operation"),
            ("【角色与边界】\n你是静态 Mock 样例生成助手，根据当前返回契约和用户要求生成明确虚构的测试数据。", "generate_static_mock_data"),
        )
        for prompt, expected in samples:
            with self.subTest(operation=expected):
                self.assertEqual(self.operation(prompt, role="system"), expected)

    def test_explicit_operations_are_english_and_invalid_text_falls_back_to_prompt(self):
        """旧中文说明与英文显示文本归一，未知中文和链接不能被原样写入。"""
        prompt = "You are the requirements model for an app-generation workflow."
        for explicit, expected in (
            ("生成技术规划", "generate_technical_plan"),
            ("Generate Technical Plan", "generate_technical_plan"),
            ("GenerateTechnicalPlan", "generate_technical_plan"),
            ("custom-operation-2", "custom_operation_2"),
            ("当前任务的未知中文说明", "analyze_requirements"),
            ("https://example.test/path", "analyze_requirements"),
            ("unknown", "analyze_requirements"),
        ):
            with self.subTest(explicit=explicit):
                operation = self.operation(prompt, extensions={"current_operation": explicit})
                self.assertEqual(operation, expected)
                self.assertRegex(operation, r"^[a-z][a-z0-9_]*$")

    def test_normalization_handles_multimodal_content_whitespace_and_case(self):
        """角色文本块和模板空白变化仍能识别，图像内容不参与判断。"""
        request = {"messages": [{"role": "developer", "content": [
            {"type": "image_url", "image_url": {"url": "ignored"}},
            {"type": "text", "text": "\ufeff  YOU ARE THE\n technical-planning MODEL for an app-generation workflow.\n"},
        ]}]}
        self.assertEqual(detect_operation(request, {}), "generate_technical_plan")

    def test_tool_definitions_history_and_quoted_prompts_do_not_choose_operation(self):
        """不根据工具声明、历史角色、业务正文里的模板引用推断当前职责。"""
        signature = "You are the product-planning model for an app-generation workflow."
        for messages in (
            [{"role": "user", "content": "请解释这段提示词：\n" + signature}],
            [{"role": "user", "content": "你好"}, {"role": "assistant", "content": signature}],
            [{"role": "user", "content": "你好"}, {"role": "user", "content": signature}],
            [{"role": "tool", "content": signature}],
        ):
            with self.subTest(messages=messages):
                self.assertEqual(detect_operation({"messages": messages, "tools": [{"name": "generate_product_plan"}]}, {}), "unknown")
        request = {"messages": [{"role": "system", "content": "You are the requirements model for an app-generation workflow."},
                                {"role": "user", "content": signature}]}
        self.assertEqual(detect_operation(request, {}), "analyze_requirements")

    def test_identity_operation_remains_optional_but_rejects_non_english_values(self):
        """字段可省略或为空，已填写的字段必须是英文稳定标识。"""
        self.assertNotIn("current_operation", Identity().model_dump(exclude_unset=True))
        self.assertIsNone(Identity(current_operation=None).current_operation)
        self.assertEqual(Identity(current_operation="generate_technical_plan").current_operation, "generate_technical_plan")
        with self.assertRaises(ValidationError):
            Identity(current_operation="生成技术规划")
