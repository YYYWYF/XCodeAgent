from __future__ import annotations

import json
import unittest
from copy import deepcopy

from app.agents.main.planner import _technical_planning_prompt
from app.agents.main.product_planner import _product_plan_json_example
from app.services.product_plan import create_product_plan
from app.services.project_plan import (
    create_technical_plan,
    validate_technical_plan_agent_contracts,
)
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import render_project_plan_markdown


class AgentTechnicalPlanTests(unittest.TestCase):
    """验证业务智能体进入 TechnicalPlan 的正式运行时与接口契约。"""

    def _requirement_with_product_agent(self) -> dict:
        """构造已确认且包含库存助手的 RequirementSpec 与 ProductPlan。"""

        spec = create_requirement_spec("创建一个库存管理系统，并提供库存问答助手")
        page_id = spec["pages"][0]["pageId"]
        spec["agent_requirements"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "帮助用户理解库存状态并获得处理建议。",
                "capabilities": ["解释库存状态", "提供补货建议"],
                "entryPageIds": [page_id],
                "interactionMode": "conversation",
                "boundaries": ["不得直接修改库存数据"],
            }
        ]
        model_plan = json.loads(_product_plan_json_example(spec))
        action_id = f"{page_id}_ask_inventory_assistant"
        model_plan["pages"][0]["actions"] = [
            {
                "actionId": action_id,
                "name": "询问库存助手",
                "description": "向库存助手发送问题并获取业务建议。",
                "requiresConfirmation": False,
                "behavior": {
                    "type": "business",
                    "expectedResult": "用户获得基于当前库存信息的明确回复。",
                },
            }
        ]
        model_plan["agents"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "帮助用户理解库存状态并获得处理建议。",
                "capabilities": [
                    {
                        "capabilityId": "explain_inventory_status",
                        "name": "解释库存状态",
                        "expectedResult": "用户理解当前库存状态及异常原因。",
                    },
                    {
                        "capabilityId": "suggest_replenishment",
                        "name": "提供补货建议",
                        "expectedResult": "用户获得可执行的补货建议。",
                    },
                ],
                "entryPageIds": [page_id],
                "pageActionBindings": [
                    {"pageId": page_id, "actionIds": [action_id]}
                ],
                "interaction": {
                    "mode": "conversation",
                    "supportsMultiTurn": True,
                    "inputDescription": "用户输入库存相关的自然语言问题。",
                    "outputDescription": "返回库存解释、建议和必要的结果说明。",
                    "stateRequirements": {
                        "loading": "处理问题时展示生成状态。",
                        "empty": "无对话时展示可提问范围。",
                        "error": "失败时说明原因并允许重试。",
                        "success": "成功时展示完整回复。",
                        "validation": "空问题不能发送。",
                    },
                },
                "boundaries": ["不得直接修改库存数据"],
                "acceptanceCriteria": ["能够解释当前库存状态并提供明确建议。"],
            }
        ]
        product_plan = create_product_plan(spec, agent_plan=model_plan)
        return {**spec, "confirmed_product_plan": product_plan}

    def _technical_model_plan(self, requirement: dict) -> dict:
        """构造包含 Agent 网关、工具 API 与技术绑定的模型输出。"""

        page_id = requirement["confirmed_product_plan"]["pages"][0]["pageId"]
        action_id = requirement["confirmed_product_plan"]["pages"][0]["actions"][0]["actionId"]
        return {
            "architecture": {
                "frontend": "React 客户端通过 AG-UI SSE 与 Java 网关通信。",
                "backend": "Java8 + Springboot 提供业务 API 与 Agent 网关。",
                "data": "MySQL8 持久化，Redis 提供缓存。",
            },
            "entities": [
                {
                    "id": "InventorySnapshot",
                    "name": "库存快照",
                    "description": "智能体查询的库存状态。",
                    "fields": [
                        {
                            "name": "sku",
                            "label": "SKU",
                            "description": "库存商品编码。",
                            "type": "text",
                            "required": True,
                        }
                    ],
                }
            ],
            "api_contracts": [
                {
                    "id": "inventory_api",
                    "entity_ids": ["InventorySnapshot"],
                    "base_path": "/api/inventory",
                    "authentication": {"required": True},
                    "schemas": {
                        "InventoryStatusOutput": {
                            "type": "object",
                            "properties": {
                                "sku": {
                                    "type": "string",
                                    "entity_field_ref": "InventorySnapshot.sku",
                                }
                            },
                            "required": ["sku"],
                        }
                    },
                    "endpoints": [
                        {
                            "id": "inventory_api.get_status",
                            "method": "GET",
                            "path": "/api/inventory/{sku}",
                            "summary": "查询库存状态。",
                            "parameters": [
                                {
                                    "name": "sku",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "request_schema_ref": None,
                            "response_schema_ref": "InventoryStatusOutput",
                            "error_codes": ["NOT_FOUND"],
                            "authentication": {"required": True},
                        },
                        {
                            "id": "inventory_api.agent_message",
                            "method": "POST",
                            "path": "/api/agents/inventory-assistant/messages",
                            "summary": "通过 AG-UI SSE 调用库存助手。",
                            "parameters": [],
                            "request_schema_ref": None,
                            "response_schema_ref": None,
                            "error_codes": ["AGENT_UNAVAILABLE"],
                            "authentication": {"required": True},
                        },
                    ],
                }
            ],
            "pages": [
                {
                    "pageId": page_id,
                    "references": {
                        "endpoint_dependencies": [
                            {
                                "endpoint_id": "inventory_api.agent_message",
                                "usage": "write",
                                "trigger": "用户发送智能体消息",
                                "required_for_initial_load": False,
                            }
                        ],
                        "action_implementations": [
                            {
                                "actionId": action_id,
                                "endpointId": "inventory_api.agent_message",
                            }
                        ],
                    },
                }
            ],
            "agent_contracts": [
                {
                    "agentId": "inventory_assistant",
                    "invocation": {
                        "gatewayEndpointId": "inventory_api.agent_message"
                    },
                    "model": {"selection": "project_default"},
                    "capabilityBindings": [
                        {
                            "capabilityId": "explain_inventory_status",
                            "toolIds": ["get_inventory_status"],
                        },
                        {
                            "capabilityId": "suggest_replenishment",
                            "toolIds": ["get_inventory_status"],
                        },
                    ],
                    "toolBindings": [
                        {
                            "toolId": "get_inventory_status",
                            "apiContractId": "inventory_api",
                            "endpointId": "inventory_api.get_status",
                            "accessMode": "read",
                        }
                    ],
                    "knowledgeReferences": [],
                    "session": {
                        "supportsMultiTurn": True,
                        "memory": "conversation",
                    },
                }
            ],
        }

    def test_agent_technical_plan_materializes_python_sidecar_contract(self) -> None:
        """Agent TechnicalPlan 必须落盘 Python sidecar、AG-UI 和稳定产物路径。"""

        requirement = self._requirement_with_product_agent()
        plan = create_technical_plan(
            requirement,
            agent_plan=self._technical_model_plan(requirement),
        )

        self.assertIn("agent_runtime", plan["architecture"])
        contract = plan["agent_contracts"][0]
        self.assertEqual(contract["agentId"], "inventory_assistant")
        self.assertEqual(contract["runtime"]["language"], "Python")
        self.assertEqual(contract["runtime"]["pythonVersion"], "3.12")
        self.assertEqual(contract["runtime"]["framework"], "DeepAgents")
        self.assertEqual(contract["runtime"]["deployment"], "sidecar")
        self.assertEqual(contract["invocation"]["transport"], "ag-ui-sse")
        self.assertFalse(contract["security"]["directClientAccess"])
        self.assertEqual(
            contract["artifacts"]["agentPath"],
            "agent-runtime/agents/inventory_assistant.py",
        )
        self.assertEqual(
            validate_technical_plan_agent_contracts(
                plan,
                requirement["confirmed_product_plan"],
            ),
            [],
        )
        markdown = render_project_plan_markdown(plan)
        self.assertIn("智能体运行时契约", markdown)
        self.assertIn("Python 3.12", markdown)
        self.assertIn("AG-UI SSE", markdown)

    def test_complete_single_agent_object_matches_array_without_mutation(self) -> None:
        """单智能体完整对象必须无损收敛为数组，且不修改原始模型响应。"""

        requirement = self._requirement_with_product_agent()
        raw_plan = self._technical_model_plan(requirement)
        expected = create_technical_plan(requirement, agent_plan=raw_plan)
        raw_plan["agent_contracts"] = raw_plan["agent_contracts"][0]
        original = deepcopy(raw_plan)

        actual = create_technical_plan(requirement, agent_plan=raw_plan)

        self.assertEqual(actual, expected)
        self.assertEqual(raw_plan, original)
        self.assertEqual(
            validate_technical_plan_agent_contracts(
                actual, requirement["confirmed_product_plan"]
            ),
            [],
        )

    def test_ambiguous_non_array_agent_contracts_are_rejected(self) -> None:
        """缺失、空值、映射、字段缺失及身份不匹配均不能包装成合法契约。"""

        requirement = self._requirement_with_product_agent()
        contract = self._technical_model_plan(requirement)["agent_contracts"][0]
        candidates = [
            None,
            {},
            {"inventory_assistant": contract},
            {key: value for key, value in contract.items() if key != "session"},
            {**contract, "agentId": "another_assistant"},
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                raw_plan = self._technical_model_plan(requirement)
                raw_plan["agent_contracts"] = candidate
                with self.assertRaisesRegex(ValueError, "agent_contracts 必须是 JSON 数组"):
                    create_technical_plan(requirement, agent_plan=raw_plan)
        raw_plan.pop("agent_contracts")
        with self.assertRaisesRegex(ValueError, "agent_contracts 必须是 JSON 数组"):
            create_technical_plan(requirement, agent_plan=raw_plan)

    def test_single_contract_object_requires_exactly_one_product_agent(self) -> None:
        """普通应用或多智能体应用不得使用单对象收敛规则。"""

        requirement = self._requirement_with_product_agent()
        product_agent = requirement["confirmed_product_plan"]["agents"][0]
        for agents in ([], [product_agent, {**product_agent, "agentId": "other"}]):
            with self.subTest(agent_count=len(agents)):
                raw_plan = self._technical_model_plan(requirement)
                raw_plan["agent_contracts"] = raw_plan["agent_contracts"][0]
                requirement["confirmed_product_plan"]["agents"] = agents
                with self.assertRaisesRegex(ValueError, "agent_contracts 必须是 JSON 数组"):
                    create_technical_plan(requirement, agent_plan=raw_plan)

    def test_single_contract_object_still_validates_endpoint_references(self) -> None:
        """对象包装不允许绕过内部工具 Endpoint 引用校验。"""

        requirement = self._requirement_with_product_agent()
        raw_plan = self._technical_model_plan(requirement)
        contract = raw_plan["agent_contracts"][0]
        contract["toolBindings"][0]["endpointId"] = "inventory_api.missing"
        raw_plan["agent_contracts"] = contract
        with self.assertRaisesRegex(ValueError, "inventory_api.missing"):
            create_technical_plan(requirement, agent_plan=raw_plan)

    def test_ordinary_technical_plan_has_no_agent_runtime(self) -> None:
        """普通应用必须保持 Java 主流程且只产生空智能体契约。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        requirement = {
            **spec,
            "confirmed_product_plan": create_product_plan(spec),
        }
        plan = create_technical_plan(requirement, agent_plan={"entities": []})

        self.assertEqual(plan["agent_contracts"], [])
        self.assertNotIn("agent_runtime", plan["architecture"])
        self.assertNotIn("智能体运行时契约", render_project_plan_markdown(plan))

    def test_agent_contract_rejects_unknown_tool_endpoint(self) -> None:
        """Agent 工具必须引用同一 TechnicalPlan 中存在的 API Endpoint。"""

        requirement = self._requirement_with_product_agent()
        raw_plan = self._technical_model_plan(requirement)
        raw_plan["agent_contracts"][0]["toolBindings"][0]["endpointId"] = "inventory_api.missing"

        with self.assertRaisesRegex(ValueError, "inventory_api.missing"):
            create_technical_plan(requirement, agent_plan=raw_plan)

    def test_agent_contract_rejects_page_action_bound_to_another_endpoint(self) -> None:
        """ProductPlan 智能体入口 action 必须落到同一 Agent 网关 Endpoint。"""

        requirement = self._requirement_with_product_agent()
        raw_plan = self._technical_model_plan(requirement)
        raw_plan["pages"][0]["references"]["action_implementations"][0][
            "endpointId"
        ] = "inventory_api.get_status"

        with self.assertRaisesRegex(ValueError, "页面 action.*Agent 网关 Endpoint"):
            create_technical_plan(requirement, agent_plan=raw_plan)

    def test_agent_contract_rejects_memory_mode_inconsistent_with_multi_turn(self) -> None:
        """单轮/多轮交互与运行时会话 memory 必须保持确定性一致。"""

        requirement = self._requirement_with_product_agent()
        raw_plan = self._technical_model_plan(requirement)
        raw_plan["agent_contracts"][0]["session"]["memory"] = "none"

        with self.assertRaisesRegex(ValueError, "session.memory.*supportsMultiTurn"):
            create_technical_plan(requirement, agent_plan=raw_plan)

    def test_technical_prompt_contains_agent_contract_and_runtime_boundary(self) -> None:
        """TechnicalPlan 提示必须输入产品智能体并固定 Java 网关与 Python sidecar。"""

        requirement = self._requirement_with_product_agent()
        prompt = _technical_planning_prompt(requirement, None)

        self.assertIn("architecture, entities, api_contracts, pages, and agent_contracts", prompt)
        self.assertIn("Python 3.12", prompt)
        self.assertIn("DeepAgents", prompt)
        self.assertIn("AG-UI SSE", prompt)
        self.assertIn("Java8/Springboot", prompt)
        self.assertIn("Agent context", prompt)
        self.assertIn("inventory_assistant", prompt)
        example_text = prompt.split("Complete result example:\n", 1)[1].split(
            "\n\nDynamic context sections:",
            1,
        )[0]
        example = json.loads(example_text)
        gateway_endpoint_id = example["agent_contracts"][0]["invocation"][
            "gatewayEndpointId"
        ]
        self.assertEqual(
            example["pages"][0]["references"]["action_implementations"],
            [
                {
                    "actionId": "dashboard_page_ask_inventory_assistant",
                    "endpointId": gateway_endpoint_id,
                }
            ],
        )

    def test_technical_prompt_uses_no_memory_for_single_turn_agent(self) -> None:
        """单轮 ProductPlan 智能体示例不得诱导模型返回 conversation memory。"""

        requirement = self._requirement_with_product_agent()
        requirement["confirmed_product_plan"]["agents"][0]["interaction"][
            "supportsMultiTurn"
        ] = False

        prompt = _technical_planning_prompt(requirement, None)

        self.assertIn('"supportsMultiTurn": false', prompt)
        self.assertIn('"memory": "none"', prompt)


if __name__ == "__main__":
    unittest.main()
