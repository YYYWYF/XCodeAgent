"""验证 TechnicalPlan 模板能力的当前 Engine 边界。"""

from __future__ import annotations

import unittest

from app.services.template_reconcile.desired import (
    TemplateCapabilityError,
    compile_template_capabilities,
    requested_config_from_application_config,
)


class TemplateReconcileDesiredTests(unittest.TestCase):
    """验证 Desired Capability 的生成与一致性。"""

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
                {
                    "appName": "订单中心",
                    "appIcon": "AppstoreOutlined",
                    "theme": {"primaryColor": "#6b3cf0"},
                    "auth": {
                        "enable": True,
                        "yht": {
                            "clientId": "000",
                            "privateKey": "111",
                            "publicKey": "123",
                            "centerPublicKey": "456",
                            "centerUri": "", "authorizeUri": "", "redirectUri": "",
                            "endUrl": "", "logoutShareJs": "",
                        },
                    },
                    "authorization": {"enabled": False},
                }
            ),
            {
                "capabilities": {"login": {"enabled": True, "config": {}}},
                "applicationConfig": {
                    "appName": "订单中心",
                    "appIcon": "AppstoreOutlined",
                    "theme": {"primaryColor": "#6b3cf0"},
                    "auth": {
                        "enable": True,
                        "authnSource": "yst",
                        "yht": {
                            "clientId": "000",
                            "privateKey": "111",
                            "publicKey": "123",
                            "centerPublicKey": "456",
                            "centerUri": "", "authorizeUri": "", "redirectUri": "",
                            "endUrl": "", "logoutShareJs": "",
                        },
                    },
                },
            },
        )

    def test_rejects_enabled_login_without_complete_yht_configuration(self) -> None:
        """启用登录时拒绝把不完整的一号通配置交给模板引擎。"""

        with self.assertRaisesRegex(TemplateCapabilityError, "privateKey"):
            requested_config_from_application_config(
                {
                    "appName": "订单中心",
                    "appIcon": "AppstoreOutlined",
                    "theme": {"primaryColor": "#6b3cf0"},
                    "auth": {"enable": True, "yht": {"clientId": "000"}},
                    "authorization": {"enabled": False},
                }
            )
