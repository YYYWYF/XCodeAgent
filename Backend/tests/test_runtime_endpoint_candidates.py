"""复现 GET→POST 修订中 Agent 派生入口被模型改成架构类型的失败。"""
from copy import deepcopy
import json
import unittest
from deepagents.backends.utils import file_data_to_string
from app.agents.main.technical_plan_editor import _candidate_files, TECHNICAL_PLAN_CANDIDATE_PATH
from app.services.api_contract_validation import validate_api_contract_definitions
from app.services.project_plan import validate_technical_plan_api_contracts
from app.services.runtime_endpoint_candidates import editable_runtime_candidate, rebuild_candidate_runtime_endpoints
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import plan, business


class RuntimeEndpointCandidateTests(unittest.TestCase):
    """验证编辑隔离、重建、错误归因及普通接口修改保持。"""

    def candidate(self):
        """生成已发布运行入口，模拟修订剥离架构并损坏类型的快照。"""
        current = plan()
        current['entities'] = [{'id': 'Order', 'name': '订单', 'fields': []}]
        current['api_contracts'] = [business()]
        current = materialize_runtime_endpoints(current)
        current.pop('topology')
        current['api_contracts'][0]['endpoints'][0]['method'] = 'POST'
        current['api_contracts'][1]['endpoints'][0]['kind'] = 'agent_runtime_direct'
        return current

    def test_model_candidate_contains_only_business_contracts(self):
        """真实编辑输入不再把平台派生入口交给模型修改。"""
        current = self.candidate()
        current.update(architecture={}, entities=[])
        files = _candidate_files({'confirmed_product_plan': {}}, current)
        candidate = json.loads(file_data_to_string(files[TECHNICAL_PLAN_CANDIDATE_PATH]))
        self.assertEqual([api['id'] for api in candidate['api_contracts']], ['order_api'])
        self.assertEqual(candidate['api_contracts'][0]['endpoints'][0]['method'], 'POST')

    def test_corrupt_checkpoint_candidate_is_rebuilt_without_changing_business_post(self):
        """当前失败快照重建后走 AG-UI 校验，不再要求普通业务响应 Schema。"""
        current = self.candidate()
        before = deepcopy(current)
        rebuilt = rebuild_candidate_runtime_endpoints(current)
        self.assertEqual(validate_api_contract_definitions(rebuilt), [])
        self.assertFalse(any('运行 Endpoint' in error or 'kind=' in error
                             for error in validate_technical_plan_api_contracts(rebuilt)))
        self.assertEqual(rebuilt['api_contracts'][0]['endpoints'][0]['method'], 'POST')
        self.assertEqual(rebuilt['api_contracts'][1]['endpoints'][0]['kind'], 'agent_runtime')
        self.assertEqual(current, before)
        self.assertEqual(rebuild_candidate_runtime_endpoints(rebuilt), rebuilt)

    def test_invalid_kind_reports_type_instead_of_schema_errors(self):
        """原始非法合同明确报类型错误，不误导模型去伪造业务 Schema。"""
        errors = validate_api_contract_definitions(self.candidate())
        self.assertTrue(any('kind=' in error for error in errors))
        self.assertFalse(any('does not define schemas' in error or 'does not define response schema' in error for error in errors))

    def test_mixed_business_endpoint_cannot_be_discarded(self):
        """保留混入业务的冲突证据，不能为了恢复派生入口丢失业务修改。"""
        current = self.candidate()
        current['api_contracts'][1]['endpoints'].append({'id': 'business.extra', 'kind': 'business'})
        with self.assertRaises(ValueError):
            editable_runtime_candidate(current)
