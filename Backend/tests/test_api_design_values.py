"""验证单数据源业务取值的依赖、确认往返和下游规则保真。"""

import unittest
from unittest.mock import patch

from app.domain.api_design import BusinessQueryRight, ExternalApiBinding, ValueFieldMapping, SourceMapping
from app.services.api_design import (
    _parse_database_query, _validate_query_references, _validate_external_api_bindings,
    _validate_field_mappings, _validated_source_snapshots, api_design_mapping_flows,
)
from app.services.binding_workspace import BindingTarget, validate_binding_selection, _draft_database_writes, _draft_field_mappings
from app.services.api_design_values import value_rule_summary
from app.workspace.endpoint_design_documents import _external_api_binding_lines, _mapping_lines


def parameter(path="page", field_type="integer"):
    """构造与当前 Endpoint 一致的请求字段快照。"""
    return {"side": "request", "location": "query", "path": path, "type": field_type, "required": True, "description": ""}


def rule(**changes):
    """构造分页转换规则，允许各用例覆盖边界行为。"""
    return {"kind": "business", "origin": "endpoint", "endpointFields": [parameter()], "builtinFields": [],
            "businessDescription": "从页码计算偏移量：(page - 1) × 20。", "missingBehavior": "error", **changes}


def external(path="offset"):
    """构造同一外部 Operation 的目标字段。"""
    return {"sourceType": "external_api", "sourceId": "api", "directoryId": "dir", "operationId": "op", "section": "query", "path": path, "type": "integer"}


class ApiDesignValueTests(unittest.TestCase):
    """覆盖复用、字段方向、失效依赖、默认值与只读投影。"""

    def test_input_reuse_and_transformed_query(self):
        """同一输入可供多个外部目标和不同查询条件加工，跨类型转换不按直连拒绝。"""
        bindings = [ExternalApiBinding(externalField=external(path), right=rule()) for path in ("offset", "start")]
        _validate_external_api_bindings(bindings, [parameter()])
        target = {"kind": "condition", "sourceType": "database", "sourceId": "db", "schema": "app", "table": "orders", "column": "created_at", "type": "timestamp", "operator": "gte"}
        date = parameter("month", "string")
        query = _parse_database_query({"join": "and", "items": [{**target, "right": rule(endpointFields=[date], businessDescription="按 Asia/Shanghai 时区取月初零点。")}]})
        _validate_query_references(query, [date])
        with self.assertRaisesRegex(ValueError, "不属于当前"):
            _validate_query_references(query, [parameter("different", "string")])

    def test_invalid_dependency_and_defaults(self):
        """禁止使用响应生成当前请求，禁止重复依赖及不匹配的默认值。"""
        for invalid in (rule(endpointFields=[{**parameter(), "side": "response"}]), rule(endpointFields=[parameter(), parameter()]), rule(businessDescription=" "), rule(missingBehavior="default")):
            with self.assertRaises(ValueError):
                BusinessQueryRight.model_validate(invalid)
        binding = ExternalApiBinding(externalField=external(), right=rule(missingBehavior="default", defaultValue="wrong"))
        with self.assertRaisesRegex(ValueError, "类型不兼容"):
            _validate_external_api_bindings([binding], [parameter()])
        duplicate = ExternalApiBinding(externalField=external(), right={"kind": "fixed", "value": 0})
        with self.assertRaisesRegex(ValueError, "重复配置"):
            _validate_external_api_bindings([duplicate, duplicate], [parameter()])

    def test_business_generation_and_draft_round_trip(self):
        """无数据源字段的业务生成及写入草稿保留全部规则内容。"""
        generated = rule(origin="business", endpointFields=[], builtinFields=["current_time"], businessDescription="将当前服务端时间转换为 Unix 毫秒时间戳。")
        endpoint = {**parameter("generatedAt"), "side": "response", "location": "response_body"}
        mapping = ValueFieldMapping(endpointField=endpoint, right=generated)
        _validate_field_mappings([mapping], [endpoint])
        write = {"sourceType": "database", "sourceId": "db", "schema": "app", "table": "orders", "column": "created_at", "type": "bigint", "right": generated}
        self.assertEqual(_draft_database_writes([write])[0]["right"], generated)
        lines = _mapping_lines({"fieldMappings": [mapping.model_dump(by_alias=True)]}, side="response")
        self.assertIn("当前时间", "\n".join(lines))
        self.assertIn("报错", "\n".join(lines))

    def test_single_operation_required_and_optional_targets(self):
        """必填目标必须配置，不能通过省略规则跳过，响应字段可以复用。"""
        metadata = {"fields": [{"section": "query", "path": "offset", "type": "integer", "required": True}]}
        binding = ExternalApiBinding(externalField=external(), right=rule(missingBehavior="omit"))
        with patch("app.services.api_design.load_external_operation", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "不能在缺值时省略"):
                _validated_source_snapshots("unused", [], external_api_bindings=[binding])
            selection = BindingTarget(sourceType="external_api", sourceId="api", directoryId="dir", operationId="op")
            with self.assertRaisesRegex(ValueError, "必填字段"):
                validate_binding_selection("unused", selection, {"fieldMappings": [{"mappingType": "business_description", "businessDescription": "校验", "endpointField": parameter()}]})

    def test_rule_projection_retains_dependencies_and_missing_policy(self):
        """Markdown、构建数据流和取值摘要保留同一规则与缺值默认值。"""
        configured = rule(missingBehavior="default", defaultValue=0)
        design = {"externalApiBindings": [{"externalField": external(), "right": configured}], "fieldMappings": []}
        text = "\n".join(_external_api_binding_lines(design) + api_design_mapping_flows([design]))
        self.assertIn("page", text)
        self.assertIn("默认值 0", text)
        self.assertIn("offset", text)
        self.assertIn(configured["businessDescription"], value_rule_summary(configured))

    def test_response_processing_keeps_context_and_request_dependencies(self):
        """同表字段加工可组合当前请求和可信上下文，错误请求身份仍被拒绝。"""
        endpoint = {**parameter("canCancel", "boolean"), "side": "response", "location": "response_body"}
        source = {"sourceType": "database", "sourceId": "db", "schema": "app", "table": "orders", "column": "status", "type": "string", "usage": "read"}
        mapping = SourceMapping(endpointField=endpoint, sourceFields=[source], processingType="single_field_description",
            endpointFields=[parameter()], builtinFields=["current_time"], businessDescription="根据订单状态、页码和当前时间计算。", missingBehavior="default", defaultValue=False)
        _validate_field_mappings([mapping], [parameter(), endpoint], "read")
        with self.assertRaisesRegex(ValueError, "不属于当前"):
            _validate_field_mappings([mapping], [parameter("other"), endpoint], "read")
        from app.workspace.endpoint_design_documents import _business_description_lines
        text = "\n".join(_business_description_lines({"fieldMappings": [mapping.model_dump(by_alias=True)]}))
        self.assertIn("当前时间", text)
        self.assertIn("默认值 False", text)

    def test_incomplete_value_draft_is_not_a_confirmed_mapping(self):
        """未选参数可暂存为当前草稿，但不能成为正式返回值映射。"""
        draft = {"endpointField": {**parameter("name", "string"), "side": "response", "location": "response_body"}, "mappingType": "value_mapping", "right": {"kind": "endpoint"}}
        self.assertEqual(_draft_field_mappings([draft])[0]["right"], {"kind": "endpoint"})
        with self.assertRaises(ValueError):
            ValueFieldMapping.model_validate(draft)

    def test_write_acceptance_retains_business_rule(self):
        """写入验收必须消费完整业务规则，不能只投影为空参数或固定值。"""
        from app.services.business_acceptance import _database_write_expectations
        right = rule(missingBehavior="default", defaultValue=0)
        rows = _database_write_expectations({"databaseWrites": [{"sourceId": "db", "schema": "app", "table": "orders", "column": "offset", "type": "integer", "right": right}]})
        self.assertEqual(rows[0]["value_rule"], right)


if __name__ == "__main__":
    unittest.main()
