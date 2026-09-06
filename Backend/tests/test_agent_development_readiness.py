from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.development_readiness import development_readiness_gate
from app.graph.nodes.tasks import _agent_generation_unit_ids
from app.services.agent_development_readiness import inspect_agent_development_readiness
from app.services.build_context_resolver import resolve_target_build_context


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

    def test_agent_gate_allows_explicit_entity_binding_bypass(self) -> None:
        """用户显式确认后只跳过 Agent 的实体绑定阻断。"""

        with patch(
            "app.graph.nodes.development_readiness.inspect_agent_development_readiness",
            return_value={
                "ready": False,
                "missing_entities": [
                    {"entity_id": "inventory", "entity_name": "库存"}
                ],
                "blockers": [
                    {
                        "type": "entity_source_binding",
                        "target_id": "inventory",
                        "message": "库存实体尚未完成数据源绑定。",
                    }
                ],
            },
        ):
            result = development_readiness_gate(
                {
                    "project_plan": {
                        "artifact_type": "technical-plan",
                        "agent_contracts": [{"agentId": "support_agent"}],
                    },
                    "selected_agent_id": "support_agent",
                    "workspace": "/tmp/agent-readiness-test",
                    "agent_entity_binding_bypass": {
                        "confirmed": True,
                        "agent_id": "support_agent",
                    },
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(
            result["development_readiness"]["entity_binding_bypassed"]
        )

    def test_agent_gate_exposes_manual_skip_only_for_entity_blockers(self) -> None:
        """只有 Agent 且只缺实体绑定时才向前端公开跳过动作。"""

        with patch(
            "app.graph.nodes.development_readiness.inspect_agent_development_readiness",
            return_value={
                "ready": False,
                "missing_entities": [
                    {"entity_id": "inventory", "entity_name": "库存"}
                ],
                "blockers": [
                    {
                        "type": "entity_source_binding",
                        "target_id": "inventory",
                        "message": "库存实体尚未完成数据源绑定。",
                    }
                ],
            },
        ):
            result = development_readiness_gate(
                {
                    "project_plan": {
                        "artifact_type": "technical-plan",
                        "agent_contracts": [{"agentId": "support_agent"}],
                    },
                    "selected_agent_id": "support_agent",
                    "workspace": "/tmp/agent-readiness-test",
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertTrue(
            result["clarification"]["can_skip_agent_entity_binding"]
        )

    def test_agent_bypass_build_context_defers_entities_and_uses_python_units(self) -> None:
        """跳过后的 Build Context 保留待联调实体，但只选 Python Unit。"""

        context = resolve_target_build_context(
            {
                "entities": [{"id": "inventory", "name": "库存"}],
                "entity_detail_plans": [],
                "frontend_pages": [],
                "page_implementation_contracts": [],
                "api_contracts": [
                    {
                        "id": "inventory_api",
                        "entity_ids": ["inventory"],
                        "endpoints": [{"id": "inventory_api.query"}],
                    },
                    {
                        "id": "agent_gateway_api",
                        "entity_ids": ["inventory"],
                        "endpoints": [{"id": "agent_gateway_api.message"}],
                    },
                ],
                "agent_contracts": [
                    {
                        "agentId": "support_agent",
                        "invocation": {
                            "gatewayEndpointId": "agent_gateway_api.message"
                        },
                        "agentSettings": {
                            "tools": {
                                "bindings": [
                                    {
                                        "toolId": "query_inventory",
                                        "endpoint": {
                                            "apiContractId": "inventory_api",
                                            "endpointId": "inventory_api.query",
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ],
            },
            target_type="agent",
            target_id="support_agent",
            product_plan={
                "agents": [{"agentId": "support_agent", "entryPageIds": []}]
            },
            allow_deferred_agent_entities=True,
        )

        self.assertEqual(context["deferred_entity_ids"], ["inventory"])
        self.assertEqual(
            _agent_generation_unit_ids(
                {
                    "build_units": {
                        "agent:runtime": {},
                        "agent:support_agent": {},
                        "backend:endpoint:inventory_api:inventory_api.query": {},
                    }
                },
                "support_agent",
            ),
            ["agent:runtime", "agent:support_agent"],
        )


if __name__ == "__main__":
    unittest.main()
