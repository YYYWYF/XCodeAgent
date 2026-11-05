"""复现局部修订剥离架构后误用正式运行编译校验的问题。"""
from copy import deepcopy
import unittest
from app.services.project_plan import validate_technical_plan_api_contracts
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import plan, business


class RuntimeCandidateTopologyTests(unittest.TestCase):
    """候选接口校验与正式架构边界分别保持有效。"""

    def candidate(self):
        """构造已有运行入口及普通业务接口的修订候选。"""
        current = plan()
        current['api_contracts'] = [business()]
        current = materialize_runtime_endpoints(current)
        current.pop('topology')
        return current

    def test_business_post_revision_passes_candidate_runtime_validation(self):
        """普通接口改 POST 后候选不再误报 Runtime 架构，且输入不被修改。"""
        current = self.candidate()
        current['api_contracts'][0]['endpoints'][0]['method'] = 'POST'
        before = deepcopy(current)
        errors = validate_technical_plan_api_contracts(current)
        self.assertFalse(any('运行 Endpoint' in error for error in errors), errors)
        compiled = materialize_runtime_endpoints(current, require_topology=False)
        self.assertEqual(compiled['api_contracts'][0]['endpoints'][0]['method'], 'POST')
        self.assertEqual(current, before)

    def test_formal_compilation_and_wrong_explicit_topology_still_fail(self):
        """候选例外不允许正式执行缺少架构或把 Agent 交给 Java 服务。"""
        current = self.candidate()
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(current)
        current['topology'] = {'type': 'backend_direct'}
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(current, require_topology=False)

    def test_forged_agent_route_is_still_rejected(self):
        """解除阶段耦合不能放过 Agent 派生运行入口篡改。"""
        current = self.candidate()
        current['api_contracts'][1]['endpoints'][0]['method'] = 'GET'
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(current, require_topology=False)
