"""Agent 会话动作与普通业务 Endpoint 的确定性边界回归。"""

from copy import deepcopy
import json
from typing import Any
import unittest

from app.agents.main.planner import _technical_planning_prompt
from app.services.application_planning_persistence import _interactions, _page_contract_map
from app.services.page_implementation_contract import (
    attach_page_implementation_contracts,
    build_page_implementation_contracts,
    materialize_technical_plan_runtime,
    technical_action_binding_issues,
    validate_page_implementation_contracts,
)
from app.services.project_plan import create_technical_plan, validate_technical_plan_api_contracts
from app.services.runtime_agent_contracts import agent_contract_index, agent_endpoint_keys
from app.workspace.plan_documents import _action_binding_target
from tests.test_technical_planning_agent_context import _planning_inputs


def _plan_with_upstreams() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """生成无业务实体和 API 的技术规划，并绑定真实上游摘要。"""

    spec, product, candidate = _planning_inputs()
    ui = {"confirmation_status": "skipped", "pages": []}
    plan = attach_page_implementation_contracts(create_technical_plan(spec, agent_plan=candidate), product, ui)
    return spec, product, ui, plan


class AgentActionBindingTests(unittest.TestCase):
    """Agent 入口复用同一会话合同，普通业务与 Tool 仍保留独立 Endpoint。"""

    def test_agent_chat_needs_no_business_entity_or_endpoint(self) -> None:
        """复现极简聊天应用：只声明 Agent 即可通过页面及 API 规划校验。"""

        _spec, product, ui, plan = _plan_with_upstreams()
        self.assertEqual(plan["entities"], [])
        self.assertEqual(plan["api_contracts"], [])
        self.assertEqual(technical_action_binding_issues(plan, product), [])
        self.assertEqual(validate_page_implementation_contracts(plan, product, ui), [])
        self.assertEqual(validate_technical_plan_api_contracts(plan), [])
        bindings = [binding for page in build_page_implementation_contracts(plan, product, ui)
                    for binding in page["actionBindings"]]
        self.assertEqual(len(bindings), 2)
        self.assertTrue(all(binding["bindingType"] == "agent" for binding in bindings))
        self.assertEqual({binding["agentId"] for binding in bindings}, {"agent_general_chat"})
        self.assertTrue(all("endpointId" not in binding for binding in bindings))

    def test_prompt_excludes_agent_actions_from_business_endpoint_decisions(self) -> None:
        """模型仅在产品智能体上下文读取聊天动作，不为其猜测普通业务 API。"""

        spec, product, _ui, _plan = _plan_with_upstreams()
        prompt = _technical_planning_prompt(spec, None)
        actions = {"page_actions": [{"pageId": page["pageId"], "actions": []} for page in product["pages"]]}
        self.assertIn(json.dumps(actions, ensure_ascii=False), prompt)
        self.assertIn("Agent Runtime AG-UI", prompt)

    def test_disabled_panel_is_absent_from_compiled_bindings(self) -> None:
        """关闭浮窗后不再强制生成该聊天动作，只保留独立页的同一 Agent。"""

        spec, product, ui, plan = _plan_with_upstreams()
        agent = product["agents"][0]
        disabled, enabled = agent["pageActionBindings"]
        disabled["surface"]["enabled"] = False
        plan["agent_contracts"][0]["pageIds"] = [enabled["pageId"]]
        plan = attach_page_implementation_contracts(plan, product, ui)
        self.assertEqual(validate_page_implementation_contracts(plan, product, ui), [])
        runtime = materialize_technical_plan_runtime(plan, spec, product, ui)
        pages = {page["pageId"]: page for page in runtime["page_implementation_contracts"]}
        self.assertEqual(pages[disabled["pageId"]]["actionBindings"], [])
        self.assertEqual(pages[enabled["pageId"]]["actionBindings"][0]["agentId"], agent["agentId"])

    def test_agent_binding_requires_current_technical_agent(self) -> None:
        """不能将缺失技术合同的 Agent 伪装成已闭合的页面实现。"""

        _spec, product, ui, plan = _plan_with_upstreams()
        plan["agent_contracts"] = []
        errors = validate_page_implementation_contracts(plan, product, ui)
        self.assertTrue(any("agent_general_chat" in error for error in errors))

    def test_business_actions_still_require_endpoint_and_agent_cannot_claim_it(self) -> None:
        """同页普通业务动作仍报缺失 Endpoint；Agent 动作不允许模型另选业务接口。"""

        _spec, product, ui, plan = _plan_with_upstreams()
        product["pages"][0]["actions"].append({
            "actionId": "load_inventory", "behavior": {"type": "business", "expectedResult": "展示库存。"},
        })
        plan = attach_page_implementation_contracts(plan, product, ui)
        issues = technical_action_binding_issues(plan, product)
        self.assertEqual([issue["actionId"] for issue in issues], ["load_inventory"])
        action = product["agents"][0]["pageActionBindings"][0]["actionIds"][0]
        plan["pages"][0].setdefault("references", {})["action_implementations"] = [
            {"actionId": action, "endpointId": "fake_chat.send"},
        ]
        errors = validate_page_implementation_contracts(plan, product, ui)
        self.assertTrue(any(action in error for error in errors))
        plan["api_contracts"] = [{"id": "fake_chat", "name": "伪造聊天接口", "entity_ids": [],
                                  "base_path": "/chat", "schemas": {}, "endpoints": []}]
        self.assertTrue(any("非空 entity_ids" in error for error in validate_technical_plan_api_contracts(plan)))

    def test_agent_identity_survives_application_projection_and_markdown(self) -> None:
        """开发配置和确认文档保留 agentId，不把会话绑定降为没有目标的业务动作。"""

        spec, product, ui, plan = _plan_with_upstreams()
        runtime = materialize_technical_plan_runtime(plan, spec, product, ui)
        details = _page_contract_map(runtime)
        detail = details[product["pages"][0]["pageId"]]
        interaction = _interactions(detail)[0]
        self.assertEqual(interaction["bindingType"], "agent")
        self.assertEqual(interaction["agentId"], "agent_general_chat")
        self.assertIn("agent_general_chat", _action_binding_target(interaction))

    def test_same_action_cannot_belong_to_two_agents(self) -> None:
        """相同页面动作的 Agent 归属必须唯一，禁止用覆盖顺序决定身份。"""

        _spec, product, ui, plan = _plan_with_upstreams()
        other = deepcopy(product["agents"][0])
        other["agentId"] = "other_agent"
        product["agents"].append(other)
        with self.assertRaisesRegex(ValueError, "多个智能体"):
            build_page_implementation_contracts(plan, product, ui)

    def test_tools_keep_exact_endpoint_references_without_page_dependencies(self) -> None:
        """Tool 仍引用原业务 Endpoint，不把 Agent 内部调用变成页面直接 API 依赖。"""

        _spec, product, ui, plan = _plan_with_upstreams()
        plan["api_contracts"] = [{"id": "stock_api", "endpoints": [{"id": "stock_api.lookup"}]}]
        settings = plan["agent_contracts"][0]["agentSettings"]
        settings["tools"] = {"enabled": True, "bindings": [{
            "toolId": "lookup_stock", "apiContractId": "stock_api", "endpointId": "stock_api.lookup",
        }]}
        contracts = agent_contract_index(plan, require_runtime=False)
        self.assertEqual(agent_endpoint_keys(contracts["agent_general_chat"]), (("stock_api", "stock_api.lookup"),))
        self.assertEqual(technical_action_binding_issues(plan, product), [])
        pages = build_page_implementation_contracts(plan, product, ui)
        self.assertTrue(all(page["requiredEndpointIds"] == [] for page in pages))
        settings["tools"]["bindings"][0]["endpointId"] = "stock_api.missing"
        with self.assertRaisesRegex(ValueError, "不存在的 Endpoint"):
            agent_contract_index(plan, require_runtime=False)
