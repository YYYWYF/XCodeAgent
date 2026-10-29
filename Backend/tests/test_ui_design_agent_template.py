from __future__ import annotations

import unittest

from app.services.ui_design_agent_template import (
    AGENT_UI_TEMPLATE_MODULE,
    AGENT_UI_TEMPLATE_VERSION,
    build_agent_ui_template_config,
    build_agent_ui_template_source_contract,
    inspect_agent_ui_template_usage,
    validate_agent_ui_template_usage,
)


class UiDesignAgentTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        """准备一个包含稳定 Agent、action 与上下文白名单的浮窗页面。"""

        self.page = {
            "pageId": "orders",
            "name": "订单列表",
            "information_items": [
                {"itemId": "selected_order_ids", "label": "已选择订单"},
            ],
            "actions": [{"actionId": "open_order_assistant"}],
            "agent_surfaces": [
                {
                    "agentId": "order_assistant",
                    "type": "floating_panel",
                    "actionIds": ["open_order_assistant"],
                    "contextItemIds": ["selected_order_ids"],
                    "name": "订单助手",
                    "purpose": "分析订单并协助跟进。",
                    "capabilities": [
                        {"capabilityId": "analyze_orders", "name": "分析异常订单"}
                    ],
                }
            ],
        }

    def test_config_is_deterministically_projected_from_product_plan(self) -> None:
        """固定配置只能来自当前页面 ProductPlan 投影，不接受名称或 DOM 推断。"""

        config = build_agent_ui_template_config(self.page)

        self.assertEqual(config["templateVersion"], AGENT_UI_TEMPLATE_VERSION)
        self.assertEqual(config["agentId"], "order_assistant")
        self.assertEqual(config["surface"], "floating_panel")
        self.assertEqual(config["actionId"], "open_order_assistant")
        self.assertEqual(
            config["contextItems"],
            [
                {
                    "id": "selected_order_ids",
                    "label": "已选择订单",
                    "value": "已选择订单示例值",
                }
            ],
        )
        self.assertEqual(
            config["capabilities"],
            [{"id": "analyze_orders", "label": "分析异常订单"}],
        )

    def test_fixed_component_contract_round_trips_and_validates(self) -> None:
        """平台生成的固定组件契约必须可解析并与页面事实完全一致。"""

        source = build_agent_ui_template_source_contract(self.page)
        inspection = inspect_agent_ui_template_usage(source)
        validation = validate_agent_ui_template_usage(self.page, inspection)

        self.assertIn(
            f"from '{AGENT_UI_TEMPLATE_MODULE}'",
            source,
        )
        self.assertIn("AgentFloatingPanelTemplate", source)
        self.assertEqual(validation["errors"], [])
        self.assertEqual(inspection["usages"][0]["version"], AGENT_UI_TEMPLATE_VERSION)
        self.assertEqual(len(inspection["usages"]), 1)

    def test_custom_chat_or_wrong_component_is_rejected(self) -> None:
        """自制聊天核心和与 Surface 不匹配的固定组件都必须被拒绝。"""

        source = build_agent_ui_template_source_contract(self.page)
        wrong_component = source.replace(
            "AgentFloatingPanelTemplate", "AgentConversationTemplate"
        )
        inspection = inspect_agent_ui_template_usage(
            wrong_component + "\nfunction AgentChatCore() { return <div /> }"
        )
        validation = validate_agent_ui_template_usage(self.page, inspection)

        self.assertTrue(any("固定组件" in error for error in validation["errors"]))
        self.assertTrue(any("自制" in error for error in validation["errors"]))

    def test_page_without_agent_surface_rejects_template_usage(self) -> None:
        """普通页面不得加载或渲染 Agent UI 固定组件。"""

        source = build_agent_ui_template_source_contract(self.page)
        page_without_surface = {**self.page, "agent_surfaces": []}
        validation = validate_agent_ui_template_usage(
            page_without_surface,
            inspect_agent_ui_template_usage(source),
        )

        self.assertTrue(any("不需要 Agent UI" in error for error in validation["errors"]))


if __name__ == "__main__":
    unittest.main()
