"""复现余额/政策列表缺少分页以及局部修复候选不完整的结构问题。"""
from copy import deepcopy
import unittest
from app.services.technical_pagination import complete_technical_pagination
from app.services.project_plan import _validate_technical_pagination
from app.graph.nodes.planning import _attach_technical_plan_contracts


class TechnicalPaginationTests(unittest.TestCase):
    """验证候选补齐、数据保留和严格校验边界。"""

    def candidate(self):
        """构造与失败项目一致的空参数余额查询和带筛选条件政策查询。"""
        return {"api_contracts": [{"id": key, "schemas": {"Output": {"type": "object",
            "properties": {"list": {"type": "array", "items": {"type": "string"}}}, "required": ["list"]}},
            "endpoints": [{"id": key + ".list", "parameters": params, "response_schema_ref": "Output"}]}
            for key, params in (("leave_balance_api", []), ("company_policy_api", [{"name": "keyword", "in": "query", "schema": {"type": "string"}}]))]}

    def test_repair_candidate_is_completed_and_original_is_unchanged(self):
        """无需模型文本替换，两个列表均通过当前分页校验且保留原筛选与 list 定义。"""
        candidate = self.candidate()
        before = deepcopy(candidate)
        completed = _attach_technical_plan_contracts({}, candidate)
        for contract in completed["api_contracts"]:
            self.assertEqual(_validate_technical_pagination(contract, contract["endpoints"][0]), [])
        self.assertEqual(candidate, before)
        self.assertEqual(completed["api_contracts"][1]["endpoints"][0]["parameters"][0], before["api_contracts"][1]["endpoints"][0]["parameters"][0])
        self.assertEqual(complete_technical_pagination(completed), completed)

    def test_business_fields_and_bad_types_are_not_silently_overwritten(self):
        """复杂业务响应和错误类型仍报告错误，不能靠自动补齐隐藏。"""
        candidate = self.candidate()
        schema = candidate["api_contracts"][0]["schemas"]["Output"]
        schema["properties"]["summary"] = {"type": "string"}
        schema["properties"]["total"] = {"type": "string"}
        completed = complete_technical_pagination(candidate)
        contract = completed["api_contracts"][0]
        self.assertEqual(contract["schemas"]["Output"], schema)
        self.assertTrue(_validate_technical_pagination(contract, contract["endpoints"][0]))

    def test_non_list_endpoint_is_unchanged(self):
        """非列表接口不会因返回数组被自动改为分页。"""
        candidate = self.candidate()
        for contract in candidate["api_contracts"]:
            contract["endpoints"][0]["id"] = contract["id"] + ".overview"
        self.assertEqual(complete_technical_pagination(candidate), candidate)
