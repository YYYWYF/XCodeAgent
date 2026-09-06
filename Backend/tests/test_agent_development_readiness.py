from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.agent_development_readiness import inspect_agent_development_readiness


class AgentDevelopmentReadinessTests(unittest.TestCase):
    """验证 Agent 开发门禁只消费当前正式产物与运行时派生合同。"""

    def test_page_binding_uses_runtime_page_implementation_contract(self) -> None:
        """正式 TechnicalPlan 未持久化页面合同也不应误判 Agent 入口。"""

        requirement_spec = {
            "confirmation_status": "confirmed",
            "app_info": {"name": "Agent App"},
        }
        product_plan = {
            "confirmation_status": "confirmed",
            "pages": [
                {
                    "pageId": "assistant_chat",
                    "name": "智能体会话",
                    "actions": [{"actionId": "ask_agent", "name": "发送消息"}],
                }
            ],
            "agents": [
                {
                    "agentId": "support_agent",
                    "pageActionBindings": [
                        {"pageId": "assistant_chat", "actionIds": ["ask_agent"]}
                    ],
                }
            ],
        }
        ui_designs = {"confirmation_status": "skipped", "pages": []}
        technical_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
            "pages": [
                {
                    "pageId": "assistant_chat",
                    "references": {
                        "endpoint_dependencies": [
                            {"endpoint_id": "agent_gateway.message", "usage": "write"}
                        ],
                        "action_implementations": [
                            {
                                "actionId": "ask_agent",
                                "endpointId": "agent_gateway.message",
                            }
                        ],
                    },
                }
            ],
            "agent_contracts": [
                {
                    "agentId": "support_agent",
                    "invocation": {"gatewayEndpointId": "agent_gateway.message"},
                    "agentSettings": {"tools": {"bindings": []}},
                }
            ],
        }
        artifacts = {
            "requirement-spec.json": requirement_spec,
            "product-plan.json": product_plan,
            "ui-designs.json": ui_designs,
            "technical-plan.json": technical_plan,
        }

        def load_artifact(path: Path) -> dict:
            """按测试文件名返回当前正式产物。"""

            return artifacts.get(path.name, {})

        with (
            patch(
                "app.services.agent_development_readiness._load_plan",
                side_effect=load_artifact,
            ),
            patch(
                "app.services.agent_development_readiness.inspect_template_generation_readiness",
                return_value={"ready": True},
            ),
            patch(
                "app.services.agent_development_readiness.validate_technical_plan_agent_contracts",
                return_value=[],
            ),
        ):
            result = inspect_agent_development_readiness(
                "/tmp/agent-readiness-test",
                "support_agent",
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
