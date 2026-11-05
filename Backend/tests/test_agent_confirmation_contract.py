"""验证候选自动补齐确认能力，但不放宽正式合同与运行时确认。"""
from copy import deepcopy
import unittest
from app.graph.nodes.planning import _attach_technical_plan_contracts
from app.services.agent_confirmation_contract import complete_tool_confirmation
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import plan, business


class AgentConfirmationContractTests(unittest.TestCase):
    """覆盖截图中的声明遗漏、已有批准能力和只读工具。"""

    def candidate(self, method="POST"):
        """创建绑定明确接口的候选，其他 Agent 保持不变。"""
        candidate = plan()
        candidate["api_contracts"] = [business(method=method)]
        candidate["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True, "bindings": [
            {"toolId": "submit", "apiContractId": "order_api", "endpointId": "order.list"}]}
        return candidate

    def test_candidate_stage_completes_declaration_and_strict_reader_still_rejects(self):
        """候选补齐后可编译，原始不完整合同仍拒绝，原工具及原对象不改变。"""
        candidate = self.candidate()
        before = deepcopy(candidate)
        candidate["agent_contracts"][0]["interactionContract"]["interactionKinds"] = ["clarification"]
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(candidate)
        completed = _attach_technical_plan_contracts({}, candidate)
        compiled = materialize_runtime_endpoints(completed)
        self.assertEqual(compiled["agent_contracts"][0]["interactionContract"]["interactionKinds"],
                         ["clarification", "confirmation"])
        self.assertEqual(completed["agent_contracts"][0]["agentSettings"], before["agent_contracts"][0]["agentSettings"])
        self.assertEqual(candidate["agent_contracts"][0]["interactionContract"]["interactionKinds"], ["clarification"])
        self.assertEqual(complete_tool_confirmation(completed), completed)

    def test_existing_approval_is_preserved(self):
        """已有更明确的批准交互时不再追加确认或删除原值。"""
        candidate = self.candidate()
        candidate["agent_contracts"][0]["interactionContract"]["interactionKinds"] = ["approval_required"]
        self.assertEqual(complete_tool_confirmation(candidate), candidate)

    def test_read_only_tool_does_not_gain_confirmation(self):
        """查询工具不增加交互步骤。"""
        candidate = self.candidate("GET")
        self.assertEqual(complete_tool_confirmation(candidate), candidate)
