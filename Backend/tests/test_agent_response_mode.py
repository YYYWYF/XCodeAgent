"""验证非流式产品事实贯通需求、产品规划、技术校验及调试目录。"""

import unittest
from copy import deepcopy

from app.services.requirement_spec import normalize_agent_requirements
from app.services.product_plan import _normalized_agents, _ensure_agent_entry_actions
from app.services.runtime_agent_settings import validate_technical_plan_agent_contracts
from app.services.runtime_debug_endpoints import debug_endpoints
from tests.test_agent_capability_refactor import detection_plan


class AgentResponseModeTests(unittest.TestCase):
    """使用内存产物验证接口协议，不调用模型或修改应用文件。"""

    def test_requirement_product_preserve_json_without_chat_action(self):
        """非流式内部能力保留返回方式，不自动为普通表单添加聊天动作。"""
        requirements = normalize_agent_requirements([{
            "agentId": "api_scan", "name": "检测", "purpose": "检测表结构", "capabilities": ["识别敏感字段"],
            "entryPageIds": [], "interactionMode": "task", "responseMode": "json", "boundaries": []}])
        spec = {"agent_requirements": requirements}
        pages = [{"pageId": "scan", "actions": [{"actionId": "submit", "behavior": {"type": "business"}}]}]
        _ensure_agent_entry_actions(pages, spec, None)
        agents = _normalized_agents(spec, None, None, pages)
        self.assertEqual(agents[0]["interaction"]["responseMode"], "json")
        self.assertEqual(agents[0]["pageActionBindings"], [])
        self.assertEqual(len(pages[0]["actions"]), 1)

    def test_json_requires_business_entry_and_debug_selects_it(self):
        """只生成 AG-UI 或记录 CRUD 不能满足已确认的非流式能力调用。"""
        plan = detection_plan()
        product = {"agents": [{"agentId": agent["agentId"], "name": agent["name"], "purpose": agent["purpose"],
            "pageActionBindings": [], "interaction": {"mode": "task", **({"responseMode": "json"} if agent["agentId"] == "api_scan" else {})}}
            for agent in plan["agent_contracts"]]}
        self.assertEqual(validate_technical_plan_agent_contracts(plan, product), [])
        entries = debug_endpoints(plan, plan["agent_contracts"][0])
        self.assertEqual(entries[0]["responseMode"], "json")
        self.assertEqual(entries[0]["relation"], "agent_call")
        invalid = deepcopy(plan)
        invalid["api_contracts"][0]["endpoints"][0].pop("agentCall")
        self.assertIn("非流式 JSON", "；".join(validate_technical_plan_agent_contracts(invalid, product)))
        invalid = deepcopy(plan)
        invalid["api_contracts"][0]["endpoints"][0]["agentCall"]["responseMode"] = "ag-ui"
        self.assertIn("非流式 JSON", "；".join(validate_technical_plan_agent_contracts(invalid, product)))
