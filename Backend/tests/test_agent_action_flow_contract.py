"""覆盖产品动作、固定页面与接口定位的跨阶段合同，不调用外部模型。"""

from copy import deepcopy
import json
from unittest import TestCase

from app.services.agent_action_bindings import agent_action_bindings
from app.services.agent_ui_build_contract import project_agent_ui_build_contracts
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_contracts import normalize_page_api_dependencies
from app.services.business_acceptance_verifiers.frontend_agent_ui import verify_agent_ui_runtime_source
from app.services.page_endpoint_identity import page_endpoint_keys
from app.services.page_dependencies import normalize_page_dependencies
from app.services.page_implementation_contract import build_page_implementation_contracts, expected_business_action_implementations
from app.services.product_plan import _normalized_action_behavior, _normalized_agents
from app.services.project_plan import _technical_plan_pages
from app.services.runtime_endpoint_dependencies import resolve_runtime_target
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from tests.test_runtime_endpoint_contracts import plan, business


def action(action_id: str, agent_id: str, operation="send_message", message="") -> dict:
    """构造直接 Agent 产品动作，清空声明保留确认。"""
    return {"actionId": action_id, "name": action_id, "description": "与助手交互。",
            "requiresConfirmation": operation == "clear_conversation",
            "behavior": {"type": "agent", "expectedResult": "展示回复或空白会话。",
                         "agentId": agent_id, "operation": operation, "message": message}}


def product() -> dict:
    """构造同页独立会话及浮窗，包含入口、推荐、追问和清空动作。"""
    actions = [action("ask", "order_assistant"), action("recommend", "order_assistant", message="查询我的余额"),
               action("follow_up", "order_assistant", message="请继续说明适用条件"),
               action("clear", "order_assistant", "clear_conversation"), action("policy_ask", "policy_assistant")]
    agents = [{"agentId": identity, "name": "助手", "purpose": "回答业务问题。", "capabilities": [],
               "entryPageIds": ["orders"], "pageActionBindings": [{"pageId": "orders", "actionIds": ids,
               "surface": {"type": surface, "enabled": True, "contextItemIds": []}}]}
              for identity, ids, surface in [("order_assistant", ["ask", "recommend", "follow_up", "clear"], "standalone_page"),
                                              ("policy_assistant", ["policy_ask"], "floating_panel")]]
    return {"agents": agents, "pages": [{"pageId": "orders", "information_items": [], "actions": actions}]}


class AgentActionFlowContractTests(TestCase):
    """验证合法产品变化能贯穿下游，真正缺失的业务实现仍能被发现。"""

    def test_multiple_agent_design_cannot_become_single_shared_runtime(self):
        """保留设计动作事实，但共享首版不能默默吞掉另一个 Agent。"""
        source_product = product()
        page = project_ui_design_pages(source_product)[0]
        self.assertEqual(len(page["agent_surfaces"]), 2)
        technical = plan()
        for agent in technical["agent_contracts"]:
            agent["pageIds"] = ["orders"]
        technical = materialize_runtime_endpoints(technical)
        with self.assertRaisesRegex(ValueError, "唯一正式 Agent"):
            project_agent_ui_build_contracts(source_product, technical)

    def test_explicit_secondary_action_needs_no_duplicate_binding_decision(self):
        """增加显式推荐动作即可获得 Agent 归属，不因遗漏重复清单要求业务接口。"""
        value = product()
        value["agents"][0]["pageActionBindings"][0]["actionIds"] = ["ask"]
        self.assertEqual(agent_action_bindings(value)["orders"]["follow_up"], "order_assistant")

    def test_mixed_sequence_keeps_business_step_even_when_whole_action_was_bound(self):
        """组合动作不能因包含 Agent 而吞掉业务 Endpoint 步骤。"""
        value = product()
        value["agents"] = value["agents"][:1]
        value["pages"][0]["actions"] = [{"actionId": "query_then_ask", "behavior": {"type": "sequence", "steps": [
            {"stepId": "query", "type": "business", "expectedResult": "查询当前余额。"},
            {"stepId": "explain", **action("unused", "order_assistant", message="解释查询结果")["behavior"]}]}}]
        value["agents"][0]["pageActionBindings"][0]["actionIds"] = ["query_then_ask"]
        self.assertEqual(expected_business_action_implementations(value["pages"][0], agent_action_ids={"query_then_ask"}),
                         {"query_then_ask": {"query"}})
        technical = {"artifact_type": "technical-plan", "api_contracts": [business()], "pages": [{"pageId": "orders",
            "references": {"endpoint_dependencies": [{"api_contract_id": "order_api", "endpoint_id": "order.list"}],
            "action_implementations": [{"actionId": "query_then_ask", "stepBindings": [{"stepId": "query", "endpointId": "order.list"}]}]}}]}
        binding = build_page_implementation_contracts(technical, value, {"confirmation_status": "skipped"})[0]["actionBindings"][0]
        self.assertEqual([step["type"] for step in binding["steps"]], ["endpoint", "agent"])
        self.assertEqual(binding["steps"][0]["apiContractId"], "order_api")
        self.assertEqual(binding["steps"][1]["agentId"], "order_assistant")

    def test_same_named_endpoints_keep_composite_scope_across_consumers(self):
        """相同短 ID 的不同接口在规范化、校验和目标解析中都保留正确身份。"""
        technical = {"api_contracts": [business("first", "list", "/first"), business("second", "list", "/second")],
                     "agent_contracts": [], "pages": [{"pageId": "orders"}], "entities": [{"id": "Order"}]}
        dependencies = [{"api_contract_id": identity, "endpoint_id": "list"} for identity in ("first", "second")]
        page = {"pageId": "orders", "requiredEndpointIds": ["list"],
                "technicalReferences": {"endpointDependencies": dependencies}}
        technical["page_implementation_contracts"] = [page]
        self.assertEqual(page_endpoint_keys(technical, page), [("first", "list"), ("second", "list")])
        self.assertEqual(validate_api_contract_consistency(technical), [])
        self.assertEqual(len(normalize_page_api_dependencies(technical["api_contracts"], [], dependencies)), 2)
        normalized = normalize_page_dependencies([{"pageId": "orders", "references": {"endpoint_dependencies": dependencies}}],
                                                technical["api_contracts"], include_action_implementations=True)
        self.assertEqual([item["api_contract_id"] for item in normalized[0]["references"]["endpoint_dependencies"]], ["first", "second"])
        persisted = _technical_plan_pages({"pages": [{"pageId": "orders"}]}, {"pages": normalized}, technical["api_contracts"])
        self.assertEqual([item["api_contract_id"] for item in persisted[0]["references"]["endpoint_dependencies"]], ["first", "second"])
        self.assertEqual(resolve_runtime_target(technical, "page", "orders")["endpoint_keys"], [("first", "list"), ("second", "list")])
        with self.assertRaisesRegex(ValueError, "歧义"):
            page_endpoint_keys(technical, {"requiredEndpointIds": ["list"]})

    def test_missing_behavior_is_reported_instead_of_inventing_business_api(self):
        """缺失行为分类不能悄悄变成必须实现的业务接口。"""
        with self.assertRaisesRegex(ValueError, "不能默认为业务接口"):
            _normalized_action_behavior("unknown", {"name": "未分类动作"})

    def test_partial_revision_preserves_existing_multi_action_bindings(self):
        """未修改的绑定保留原全集，避免局部修订退化成一个默认入口。"""
        original = product()
        requirement = {"agent_requirements": [{"agentId": "order_assistant", "name": "助手", "purpose": "回答问题。",
                        "entryPageIds": ["orders"], "capabilities": [], "interactionMode": "conversation"}]}
        result = _normalized_agents(requirement, None, deepcopy(original), original["pages"])
        self.assertEqual(result[0]["pageActionBindings"][0]["actionIds"], ["ask", "recommend", "follow_up", "clear"])
