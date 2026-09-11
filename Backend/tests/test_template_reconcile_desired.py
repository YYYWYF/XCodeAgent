"""验证 TechnicalPlan 模板能力的当前 Engine 边界。"""

from __future__ import annotations

import unittest

from app.services.template_reconcile.desired import (
    compile_template_capabilities,
    initial_template_capabilities,
    requested_config_from_technical_plan,
    template_capability_errors,
)
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import render_project_plan_markdown


class TemplateReconcileDesiredTests(unittest.TestCase):
    """验证 Desired Capability 的生成、一致性和 Markdown 展示。"""

    def test_derives_initial_authorization_capability(self) -> None:
        """确认启用权限 Manifest 时默认启用 authorization 模板能力。"""

        self.assertEqual(
            initial_template_capabilities({"enabled": True}),
            {"authorization": {"enabled": True, "config": {}}},
        )
        self.assertEqual(initial_template_capabilities({"enabled": False}), {})

    def test_committed_login_configuration_compiles_desired_capability(self) -> None:
        """登录模板能力只由已提交 application.json 开关决定。"""

        spec = create_requirement_spec(
            "给这个应用增加登录功能",
            agent_spec={
                "authentication_requirements": {"enabled": False, "sourceRefs": []},
                "authorization_requirements": {
                    "enabled": False,
                    "restrictedPages": [],
                    "restrictedOperations": [],
                },
            },
        )

        self.assertEqual(
            compile_template_capabilities(
                {"auth": {"enable": True}, "authorization": {"enabled": False}},
                {"enabled": False},
            ),
            {"login": {"enabled": True, "config": {}}},
        )

    def test_natural_language_authorization_requirement_needs_no_policy_rule(self) -> None:
        """增加权限管理基础设施时允许暂时没有业务页面或操作规则。"""

        spec = create_requirement_spec(
            "给这个应用增加权限管理功能",
            agent_spec={
                "authorization_requirements": {
                    "enabled": False,
                    "restrictedPages": [],
                    "restrictedOperations": [],
                },
            },
        )

        self.assertEqual(
            compile_template_capabilities(
                {"auth": {"enable": True}, "authorization": {"enabled": True}},
                {"enabled": True},
            ),
            {
                "login": {"enabled": True, "config": {}},
                "authorization": {"enabled": True, "config": {}},
            },
        )

    def test_compiles_requested_config_only_from_technical_plan(self) -> None:
        """确认 RequestedConfig 不读取或依赖 application.json。"""

        plan = {
            "template_capabilities": {"login": {"enabled": True, "config": {}}},
            "authorization_manifest": {"enabled": False},
        }
        self.assertEqual(
            requested_config_from_technical_plan(plan),
            {"capabilities": {"login": {"enabled": True, "config": {}}}},
        )

    def test_rejects_manifest_drift_and_nonempty_config(self) -> None:
        """确认权限一致性和空配置限制均在确认前阻断。"""

        self.assertTrue(
            template_capability_errors(
                {
                    "template_capabilities": {},
                    "authorization_manifest": {"enabled": True},
                }
            )
        )
        self.assertTrue(
            template_capability_errors(
                {
                    "template_capabilities": {
                        "login": {"enabled": True, "config": {"redirect": "/"}}
                    },
                    "authorization_manifest": {"enabled": False},
                }
            )
        )

    def test_renders_template_capabilities_in_technical_plan_markdown(self) -> None:
        """确认 TechnicalPlan Markdown 明确呈现可同步的模板能力 JSON。"""

        markdown = render_project_plan_markdown(
            {
                "artifact_type": "technical-plan",
                "confirmation_status": "draft",
                "architecture": {},
                "entities": [],
                "api_contracts": [],
                "pages": [],
                "template_capabilities": {
                    "authorization": {"enabled": True, "config": {}}
                },
                "authorization_manifest": {"enabled": False},
            }
        )
        self.assertIn("## 模板能力", markdown)
        self.assertIn('"authorization"', markdown)
