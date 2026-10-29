"""技术规划模型输入与已确认产品智能体身份的回归。"""

from copy import deepcopy
import json
from typing import Any
import unittest

from app.agents.main.planner import _technical_planning_prompt
from app.services.product_plan import create_product_plan
from app.services.project_plan import create_technical_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.runtime_agent_settings import validate_technical_plan_agent_contracts
from app.workspace.runtime_agent_markdown import runtime_agents_markdown
from app.workspace.plan_documents import render_project_plan_markdown
from app.services.runtime_agent_contracts import agent_invocation


def _planning_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """构造首页浮窗和独立聊天页共用一个无工具智能体的当前合同。"""

    spec = create_requirement_spec("创建一个包含静态首页和独立聊天页的应用")
    page_ids = [page["pageId"] for page in spec["pages"][:2]]
    spec["agent_requirements"] = [{
        "agentId": "agent_general_chat",
        "name": "通用聊天助手",
        "purpose": "两个入口共用唯一的通用聊天智能体。",
        "capabilities": ["回答用户输入的问题"],
        "entryPageIds": page_ids,
        "interactionMode": "conversation",
        "boundaries": ["不调用工具，不查询业务数据。"],
    }]
    product = create_product_plan(spec)
    product["agents"][0]["pageActionBindings"][1]["surface"]["type"] = "standalone_page"
    product["confirmation_status"] = "confirmed"
    agent = product["agents"][0]
    candidate = {
        "architecture": {}, "entities": [], "api_contracts": [],
        "pages": [{"pageId": page_id} for page_id in page_ids],
        "agent_contracts": [{
            "agentId": agent["agentId"], "name": agent["name"], "purpose": agent["purpose"],
            "pageIds": page_ids,
            "agentSettings": {
                "prompt": {"systemPrompt": "请用简体中文回答用户的问题。"},
                "model": {"source": "project"}, "memory": {"isolation": "principal/thread"},
                "tools": {"enabled": False, "bindings": []},
                "skills": {"enabled": False}, "knowledge": {"enabled": False},
                "context": {"trusted": True},
            },
        }],
    }
    return {**spec, "confirmed_product_plan": product}, product, candidate


class TechnicalPlanningAgentContextTests(unittest.TestCase):
    """验证实际模型上下文、严格身份反馈及入口启停不发生漂移。"""

    def test_shared_agent_invocation_is_visible_in_markdown_without_business_api(self) -> None:
        """无实体聊天应用仍须公开唯一 AG-UI 调用入口，且不虚构普通 API。"""

        spec, _product, candidate = _planning_inputs()
        plan = create_technical_plan(spec, agent_plan=candidate)
        before = deepcopy(plan)
        markdown = runtime_agents_markdown(plan)
        for text in ('POST', '/agents/agent_general_chat/run', 'AG-UI', 'SSE',
                     'RunAgentInput', 'BaseEvent', 'agent-runtime'):
            self.assertIn(text, markdown)
        self.assertEqual(markdown.count('/agents/agent_general_chat/run'), 1)
        self.assertEqual(plan['api_contracts'], [])
        self.assertEqual(plan['entities'], [])
        self.assertEqual(plan, before)
        api_section = render_project_plan_markdown(plan).split('## API 契约\n', 1)[1].split('\n## ', 1)[0]
        self.assertIn('POST /agents/agent_general_chat/run', api_section)

    def test_invocation_rejects_invalid_identity_and_returns_isolated_values(self) -> None:
        """拒绝非法路径身份，派生结果被修改不能污染后续调用。"""

        for agent_id in ('', 'runtime', '../assistant', 'BadId'):
            with self.subTest(agent_id=agent_id), self.assertRaises(ValueError):
                agent_invocation(agent_id)
        invocation = agent_invocation('assistant')
        self.assertEqual(invocation['endpointId'], 'agent.assistant.run')
        self.assertEqual(invocation['requestType'], 'RunAgentInput')
        invocation['path'] = '/wrong'
        self.assertEqual(agent_invocation('assistant')['path'], '/agents/assistant/run')

    def test_prompt_carries_complete_confirmed_agents_in_generation_and_repair(self) -> None:
        """首次生成和完整修复都必须提供身份、职责、入口及业务边界。"""

        spec, product, candidate = _planning_inputs()
        before = deepcopy(product)
        for existing in (None, candidate):
            with self.subTest(repair=existing is not None):
                prompt = _technical_planning_prompt(spec, existing)
                self.assertIn(json.dumps({"agents": product["agents"]}, ensure_ascii=False), prompt)
        self.assertEqual(product, before)

    def test_prompt_preserves_disabled_surface_and_explicit_empty_agent_list(self) -> None:
        """禁用浮窗仍是显式输入；没有产品智能体时不能由页面名称猜测生成。"""

        spec, product, _candidate = _planning_inputs()
        product["agents"][0]["pageActionBindings"][0]["surface"]["enabled"] = False
        prompt = _technical_planning_prompt(spec, None)
        self.assertIn(json.dumps({"agents": product["agents"]}, ensure_ascii=False), prompt)
        product["agents"] = []
        prompt = _technical_planning_prompt(spec, None)
        self.assertIn(json.dumps({"agents": []}), prompt)

    def test_identity_feedback_names_missing_and_unexpected_ids(self) -> None:
        """复现模型重命名 ID 后失败，并明确告诉修复模型两个集合的差异。"""

        spec, product, candidate = _planning_inputs()
        candidate["agent_contracts"][0]["agentId"] = "general_chat_agent"
        errors = validate_technical_plan_agent_contracts(candidate, product)
        self.assertIn('缺失 agentId：["agent_general_chat"]', errors[0])
        self.assertIn('多出 agentId：["general_chat_agent"]', errors[0])
        with self.assertRaisesRegex(ValueError, "agent_general_chat"):
            create_technical_plan(spec, agent_plan=candidate)

    def test_omitted_agent_feedback_keeps_empty_unexpected_set(self) -> None:
        """完全漏生成智能体时反馈必须给出明确的待补身份。"""

        _spec, product, candidate = _planning_inputs()
        candidate.pop("agent_contracts")
        errors = validate_technical_plan_agent_contracts(candidate, product)
        self.assertIn('缺失 agentId：["agent_general_chat"]', errors[0])
        self.assertIn("多出 agentId：[]", errors[0])

    def test_two_entries_share_one_contract_and_disabled_panel_cannot_return(self) -> None:
        """两个入口绑定同一个 Agent；关闭浮窗后仅保留独立页且不接受旧绑定。"""

        spec, product, candidate = _planning_inputs()
        plan = create_technical_plan(spec, agent_plan=candidate)
        self.assertEqual(len(plan["agent_contracts"]), 1)
        self.assertEqual(validate_technical_plan_agent_contracts(plan, product), [])
        bindings = product["agents"][0]["pageActionBindings"]
        self.assertEqual(plan["agent_contracts"][0]["pageIds"], [item["pageId"] for item in bindings])
        bindings[0]["surface"]["enabled"] = False
        self.assertTrue(validate_technical_plan_agent_contracts(plan, product))
        candidate["agent_contracts"][0]["pageIds"] = [bindings[1]["pageId"]]
        plan = create_technical_plan(spec, agent_plan=candidate)
        self.assertEqual(plan["agent_contracts"][0]["pageIds"], [bindings[1]["pageId"]])

    def test_all_disabled_agent_cannot_be_reintroduced(self) -> None:
        """没有启用入口的 Agent 不进入技术合同，已有校验门不能被修复绕开。"""

        spec, product, candidate = _planning_inputs()
        for binding in product["agents"][0]["pageActionBindings"]:
            binding["surface"]["enabled"] = False
        errors = validate_technical_plan_agent_contracts(candidate, product)
        self.assertIn("缺失 agentId：[]", errors[0])
        self.assertIn('多出 agentId：["agent_general_chat"]', errors[0])
        candidate["agent_contracts"] = []
        plan = create_technical_plan(spec, agent_plan=candidate)
        self.assertEqual(validate_technical_plan_agent_contracts(plan, product), [])
