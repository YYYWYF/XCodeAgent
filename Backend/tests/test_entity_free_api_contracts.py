"""无实体 API 可进入后续设计，显式实体关联仍严格校验。"""
from copy import deepcopy
import unittest
from app.services.project_plan import validate_technical_plan_api_contracts
from app.services.api_contract_validation import validate_api_contract_definitions


class EntityFreeApiContractTests(unittest.TestCase):
    """覆盖政策服务、字段映射及非法声明，不调用模型或修改实际项目。"""

    def candidate(self):
        """构造不绑定数据库实体的政策检索服务。"""
        return {'entities': [], 'api_contracts': [{'id': 'leave_policy_api', 'name': '请假政策查询',
            'entity_ids': [], 'base_path': '/api/leave-policy',
            'schemas': {'Answer': {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}},
            'endpoints': [{'id': 'leave_policy_api.query', 'kind': 'business', 'name': '查询请假政策',
                'method': 'POST', 'path': '/api/leave-policy', 'parameters': [],
                'request_schema_ref': None, 'response_schema_ref': 'Answer'}]}]}

    def test_entity_free_service_passes_both_contract_validators(self):
        """无实体不再构成错误，也不会绕过响应 Schema 校验。"""
        plan = self.candidate()
        self.assertEqual(validate_technical_plan_api_contracts(plan), [])
        self.assertEqual(validate_api_contract_definitions(plan), [])

    def test_declared_field_still_needs_bound_existing_entity(self):
        """允许空绑定不允许伪造字段来源。"""
        plan = self.candidate()
        plan['entities'] = [{'id': 'Policy', 'name': '政策', 'fields': [{'name': 'content'}]}]
        contract = plan['api_contracts'][0]
        field = contract['schemas']['Answer']['properties']['answer']
        field['entity_field_ref'] = 'Policy.content'
        self.assertTrue(any('未绑定实体' in e for e in validate_technical_plan_api_contracts(plan)))
        contract['entity_ids'] = ['Policy']
        self.assertEqual(validate_technical_plan_api_contracts(plan), [])
        field['entity_field_ref'] = 'Policy.missing'
        self.assertTrue(any('未知字段' in e for e in validate_technical_plan_api_contracts(plan)))

    def test_invalid_or_missing_array_and_unknown_entity_are_rejected(self):
        """只允许明确空数组，不接受漏字段、错误类型、空身份或未知实体。"""
        for value in (None, '', [''], [2], ['Unknown'], ['Unknown', 'Unknown']):
            with self.subTest(value=value):
                plan = deepcopy(self.candidate())
                plan['api_contracts'][0]['entity_ids'] = value
                self.assertTrue(validate_technical_plan_api_contracts(plan))
        plan = self.candidate()
        del plan['api_contracts'][0]['entity_ids']
        self.assertTrue(validate_technical_plan_api_contracts(plan))
