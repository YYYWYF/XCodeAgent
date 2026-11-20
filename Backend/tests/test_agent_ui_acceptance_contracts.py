"""T29 证据必须绑定实际消费的正式接口及映射，不能只绑定 Agent 声明。"""

from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.services.agent_ui_acceptance_contracts import formal_dependencies
from app.services.agent_ui_observation_identity import digest
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import plan, business


class FormalDependencyTests(TestCase):
    """使用真实目标依赖解析，只替代已确认动态映射的磁盘 owner。"""

    def setUp(self) -> None:
        """冻结一个查询 Tool 和另一个无关业务接口。"""
        candidate = plan()
        candidate['api_contracts'] = [business(), business('unrelated', 'unrelated.list', '/api/unrelated')]
        candidate['entities'] = [{'id': 'Order', 'table_name': 'orders', 'fields': []}]
        candidate['agent_contracts'][0]['agentSettings']['tools'] = {'enabled': True, 'bindings': [
            {'toolId': 'lookup', 'apiContractId': 'order_api', 'endpointId': 'order.list'}]}
        self.plan = materialize_runtime_endpoints(candidate)
        self.design = {'apiContractId': 'order_api', 'endpointId': 'order.list', 'status': 'confirmed',
                       'fieldMappings': [{'targetPath': 'status', 'description': 'current'}]}
        owner = patch('app.services.api_design.load_confirmed_endpoint_designs', side_effect=lambda *_args, **_kwargs: [deepcopy(self.design)])
        owner.start()
        self.addCleanup(owner.stop)

    def fingerprint(self) -> str:
        """直接计算当前依赖集合摘要，不执行模型或真实数据库操作。"""
        return digest(formal_dependencies(Path('/target'), self.plan, 'orders'))

    def test_tool_endpoint_mapping_and_entity_changes_invalidate(self) -> None:
        """接口实现合同、字段映射和实体物理信息都属于运行证据。"""
        before = self.fingerprint()
        self.design['fieldMappings'][0]['description'] = 'changed'
        self.assertNotEqual(before, self.fingerprint())
        before = self.fingerprint()
        self.plan['api_contracts'][0]['endpoints'][0]['path'] = '/api/changed'
        self.assertNotEqual(before, self.fingerprint())
        before = self.fingerprint()
        self.plan['entities'][0]['table_name'] = 'other_orders'
        self.assertNotEqual(before, self.fingerprint())

    def test_unrelated_endpoint_changes_preserve_local_evidence(self) -> None:
        """其他 API 的修改不使当前页面同一 Tool 的证据失效。"""
        before = self.fingerprint()
        self.plan['api_contracts'][1]['endpoints'][0]['path'] = '/other-unrelated'
        self.assertEqual(before, self.fingerprint())
