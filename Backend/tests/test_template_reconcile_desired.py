"""验证 TechnicalPlan 模板能力的当前 Engine 边界。"""

from __future__ import annotations

import unittest

from app.services.template_reconcile.desired import (
    compile_template_capabilities,
    requested_config_from_application_config,
)
from app.workspace.plan_documents import render_project_plan_markdown


class TemplateReconcileDesiredTests(unittest.TestCase):
    """验证 Desired Capability 的生成、一致性和 Markdown 展示。"""

    def test_committed_login_configuration_compiles_desired_capability(self) -> None:
        """登录模板能力只由已提交 application.json 开关决定。"""

        self.assertEqual(
            compile_template_capabilities(
                {"auth": {"enable": True}, "authorization": {"enabled": False}},
            ),
            {"login": {"enabled": True, "config": {}}},
        )

    def test_natural_language_authorization_requirement_needs_no_policy_rule(self) -> None:
        """增加权限管理基础设施时允许暂时没有业务页面或操作规则。"""

        self.assertEqual(
            compile_template_capabilities(
                {"auth": {"enable": True}, "authorization": {"enabled": True}},
            ),
            {
                "login": {"enabled": True, "config": {}},
                "authorization": {"enabled": True, "config": {}},
            },
        )

    def test_compiles_requested_config_only_from_application_config(self) -> None:
        """确认 RequestedConfig 不读取或依赖 TechnicalPlan。"""

        self.assertEqual(
            requested_config_from_application_config(
                {"auth": {"enable": True}, "authorization": {"enabled": False}}
            ),
            {"capabilities": {"login": {"enabled": True, "config": {}}}},
        )

    def test_does_not_render_template_capabilities_in_technical_plan_markdown(self) -> None:
        """确认 TechnicalPlan 不再保存或展示应用级模板开关。"""

        markdown = render_project_plan_markdown(
            {
                "artifact_type": "technical-plan",
                "confirmation_status": "draft",
                "architecture": {},
                "entities": [],
                "api_contracts": [],
                "pages": [],
                "authorization_manifest": {},
            }
        )
        self.assertNotIn("## 模板能力", markdown)
