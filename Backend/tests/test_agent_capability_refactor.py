"""独立 Agent、业务调用及依赖闭包的离线合同回归。"""

from copy import deepcopy
import unittest

from app.domain.runtime_endpoint_contract import AgentExecutionContract
from app.services.runtime_agent_calls import validate_agent_call_design
from app.services.runtime_agent_contracts import agent_contract_index
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints, agent_execution_capability_snapshot
from app.services.runtime_endpoint_dependencies import resolve_runtime_target
from tests.test_runtime_endpoint_contracts import agent, business, plan


def detection_contract() -> dict:
    """给出有真实业务参数和检测结果的单次任务能力。"""
    return {"mode": "task", "inputSchema": {"type": "object", "properties": {"sample": {"type": "string"}},
        "required": ["sample"], "additionalProperties": False},
        "resultSchema": {"type": "object", "properties": {"risk": {"type": "string"}}, "required": ["risk"]},
        "textOutput": False, "publicStateSchema": None, "outcomes": [], "interactionKinds": []}


def detection_plan() -> dict:
    """构造三个纯后台 Agent，以及一个使用其中 Agent 的普通业务接口。"""
    current = plan()
    current["agent_contracts"] = []
    for identity in ("api_scan", "database_scan", "page_scan"):
        item = agent(identity, "orders")
        item.update(pageIds=[], publicEndpoint=False, interactionContract=detection_contract())
        current["agent_contracts"].append(item)
    api = business(method="POST")
    api["schemas"] = {"Result": detection_contract()["resultSchema"]}
    api["endpoints"][0].update(response_schema_ref="Result", agentCall={"agentId": "api_scan",
        "inputBindings": {"/sample": "/body/sample"}, "resultBindings": {"": ""}, "responseMode": "json"})
    current["api_contracts"] = [api]
    return current


class AgentCapabilityRefactorTests(unittest.TestCase):
    """仅验证正式数据流，不调用模型、数据库、服务或工作区工具。"""

    def test_three_backend_agents_do_not_publish_chat_routes(self):
        """无页面能力保持完整定义，但不会被自动发布为聊天接口。"""
        current = materialize_runtime_endpoints(detection_plan())
        self.assertEqual(set(agent_contract_index(current)), {"api_scan", "database_scan", "page_scan"})
        self.assertEqual(len(current["api_contracts"]), 1)
        self.assertEqual(agent_execution_capability_snapshot(current, "api_scan")["invocation"]["exposure"], "internal")

    def test_business_interface_brings_only_its_agent_and_tools(self):
        """业务接口带入所调用能力和 Tool 前置接口，不扩大到另外两个 Agent 或页面。"""
        current = detection_plan()
        dependency = business("data_api", "data.read", "/api/data")
        current["api_contracts"].append(dependency)
        current["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True, "bindings": [
            {"toolId": "read_data", "apiContractId": "data_api", "endpointId": "data.read"}]}
        resolved = resolve_runtime_target(current, "endpoint", "order.list", "order_api")
        self.assertEqual(resolved["agent_ids"], ["api_scan"])
        self.assertEqual(resolved["endpoint_keys"], [("data_api", "data.read"), ("order_api", "order.list")])

    def test_agent_tool_cannot_call_its_own_business_entry(self):
        """规划阶段拒绝接口→Agent→同一接口的环，不留给运行时递归。"""
        current = detection_plan()
        current["api_contracts"][0]["endpoints"][0]["method"] = "GET"
        current["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True, "bindings": [
            {"toolId": "scan_again", "apiContractId": "order_api", "endpointId": "order.list"}]}
        with self.assertRaisesRegex(ValueError, "循环依赖"):
            agent_contract_index(current)

    def test_result_schema_change_requires_new_endpoint_confirmation(self):
        """Agent 能力变化后旧调用设计失效，不能仅凭 confirmed 继续开发。"""
        current = detection_plan()
        endpoint = current["api_contracts"][0]["endpoints"][0]
        draft = {"agentCall": endpoint["agentCall"], "fieldMappings": [],
                 "agentCapabilitySha256": agent_execution_capability_snapshot(current, "api_scan")["capabilitySha256"]}
        validate_agent_call_design(current, endpoint, draft)
        changed = deepcopy(current)
        changed["agent_contracts"][0]["interactionContract"]["resultSchema"]["required"].append("evidence")
        with self.assertRaisesRegex(ValueError, "能力已变化"):
            validate_agent_call_design(changed, changed["api_contracts"][0]["endpoints"][0], draft)

    def test_output_contract_and_external_schema_references(self):
        """纯文本任务可无结果 Schema；关闭文本和非法 Schema 仍拒绝。"""
        AgentExecutionContract.model_validate(detection_contract())
        AgentExecutionContract.model_validate({**detection_contract(), "resultSchema": None, "textOutput": True})
        for update in ({"resultSchema": None}, {"inputSchema": {"type": "object", "$ref": "https://example.test/schema"}}):
            with self.assertRaises(ValueError):
                AgentExecutionContract.model_validate({**detection_contract(), **update})

    def test_business_interface_cannot_call_text_only_agent(self):
        """放宽纯文本任务不改变普通业务接口的结构化结果保证。"""
        current = detection_plan()
        current["agent_contracts"][0]["interactionContract"].update(resultSchema=None, textOutput=True)
        with self.assertRaisesRegex(ValueError, "resultSchema"):
            agent_contract_index(current)
