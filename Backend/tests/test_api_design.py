"""Endpoint 自包含字段映射领域服务测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.api_design import (
    BusinessDescriptionFieldMapping,
    DatabaseSourceField,
    EndpointFieldMappingDesign,
    ExternalSourceField,
    SourceMapping,
)
from app.services.api_design import (
    _safe_external_operation,
    _validate_field_mappings,
    _validated_source_snapshots,
    api_design_business_descriptions,
    api_design_mapping_flows,
    api_design_source_types,
    confirm_api_design,
    endpoint_api_fields,
    initial_api_design_payload,
    load_database_columns,
)


class ApiDesignTests(unittest.TestCase):
    """验证当前版字段映射、来源和业务说明契约。"""

    def test_database_column_load_keeps_table_candidates_for_next_cascade_step(self) -> None:
        """列查询结果继续携带表清单，避免下一帧丢失数据库表选择。"""

        with patch(
            "app.services.api_design._mysql_metadata",
            return_value={
                "database": "app",
                "tables": [{"table_name": "products", "comment": "商品"}],
                "schemas": {"products": [{"column_name": "id", "column_type": "bigint"}]},
            },
        ):
            result = load_database_columns("unused", "db", "products")
        self.assertEqual(result["tables"], [{"name": "products", "description": "商品"}])
        self.assertEqual(result["columns"][0]["name"], "id")

    def test_all_of_merges_fields_and_required_constraints(self) -> None:
        """组合分支及同名嵌套字段共同定义结构，必填约束不能丢失。"""

        contract = {"schemas": {
            "Base": {"type": "object", "properties": {"id": {"type": "string"}}},
            "Output": {"allOf": [
                {"$ref": "#/schemas/Base"},
                {"required": ["id", "data"], "properties": {"data": {
                    "type": "object", "properties": {"name": {"type": "string"}},
                }}},
                {"properties": {"data": {"required": ["name"], "properties": {
                    "optional": {"type": "string"},
                }}}},
            ]},
        }}
        fields = endpoint_api_fields(contract, {"response_schema_ref": "Output"})
        self.assertEqual(
            {field["path"]: field["required"] for field in fields},
            {"id": True, "data.name": True, "data.optional": False},
        )

    def test_all_of_nullable_array_and_recursive_reference(self) -> None:
        """组合结构支持可空数组，并在递归引用处停止。"""

        contract = {"schemas": {
            "Item": {"allOf": [{"type": "object", "required": ["id"], "properties": {
                "id": {"type": "string"}, "child": {"$ref": "#/schemas/Item"},
            }}]},
            "Output": {"type": ["array", "null"], "items": {"$ref": "#/schemas/Item"}},
        }}
        fields = endpoint_api_fields(contract, {"response_schema_ref": "Output"})
        self.assertEqual([(field["path"], field["required"]) for field in fields], [("[].id", True)])

    def test_enumerates_header_and_leaf_fields(self) -> None:
        """Path、Query、Header、请求体和响应体只展开叶子字段。"""

        plan = _technical_plan()
        contract = plan["api_contracts"][0]
        fields = endpoint_api_fields(contract, contract["endpoints"][0])
        self.assertEqual(
            [(item["location"], item["path"]) for item in fields],
            [
                ("path", "orderId"),
                ("query", "verbose"),
                ("header", "x-trace-id"),
                ("request_body", "customer.name"),
                ("response_body", "data.id"),
            ],
        )

    def test_business_description_requires_endpoint_and_text(self) -> None:
        """业务说明必须内嵌 Endpoint 字段和非空文本。"""

        mapping = BusinessDescriptionFieldMapping.model_validate({
            "endpointField": {
                "side": "request", "location": "query", "path": "current",
                "type": "integer", "required": False, "description": "",
            },
            "mappingType": "business_description",
            "businessDescription": "用于控制分页查询。",
        })
        self.assertEqual(mapping.mapping_type, "business_description")
        with self.assertRaises(ValueError):
            BusinessDescriptionFieldMapping.model_validate({
                "endpointField": mapping.endpoint_field.model_dump(by_alias=True),
                "mappingType": "business_description",
                "businessDescription": "   ",
            })

    def test_source_mapping_supports_single_and_multi_field_descriptions(self) -> None:
        """来源映射按处理类型强制单字段或多字段来源及非空说明。"""

        endpoint = {
            "side": "response", "location": "response_body", "path": "amount",
            "type": "number", "required": True, "description": "",
        }
        first = {
            "sourceType": "database", "sourceId": "db", "schema": "app",
            "table": "accounts", "column": "balance", "type": "decimal", "usage": "read",
        }
        second = {**first, "column": "frozen_amount"}
        single = SourceMapping.model_validate({
            "endpointField": endpoint,
            "mappingType": "source_mapping",
            "processingType": "single_field_description",
            "sourceFields": [first],
            "businessDescription": "按账户状态转换金额展示。",
        })
        self.assertEqual(len(single.source_fields), 1)
        multi = SourceMapping.model_validate({
            "endpointField": endpoint,
            "mappingType": "source_mapping",
            "processingType": "multi_field_description",
            "sourceFields": [first, second],
            "businessDescription": "余额减去冻结金额。\n任一字段为空时返回业务错误。",
        })
        self.assertEqual(len(multi.source_fields), 2)
        with self.assertRaises(ValueError):
            SourceMapping.model_validate({
                "endpointField": endpoint,
                "mappingType": "source_mapping",
                "processingType": "multi_field_description",
                "sourceFields": [first],
                "businessDescription": "缺少第二个来源。",
            })
        with self.assertRaises(ValueError):
            SourceMapping.model_validate({
                "endpointField": endpoint,
                "mappingType": "source_mapping",
                "processingType": "direct",
                "sourceFields": [first],
                "businessDescription": "直接映射不应有说明。",
            })

    def test_formal_design_rejects_draft_and_removed_mapping_shapes(self) -> None:
        """正式 v3 产物只接受当前来源映射或业务说明，不接受草稿态和旧中转字段。"""

        base = {
            "apiContractId": "orders-api",
            "endpointId": "orders.list",
            "endpointContract": {"id": "orders.list", "method": "GET", "path": "/orders"},
            "artifactRevision": "0123456789abcdef0123456789abcdef",
            "sourceSnapshots": [],
            "basedOn": [{"artifactKey": "technical-plan", "sha256": "0" * 64}],
            "confirmedAt": "2026-01-01T00:00:00Z",
        }
        endpoint_field = {
            "side": "response",
            "location": "response_body",
            "path": "id",
            "type": "string",
            "required": False,
            "description": "",
        }
        with self.assertRaises(ValueError):
            EndpointFieldMappingDesign.model_validate({
                **base,
                "fieldMappings": [{"endpointField": endpoint_field, "mappingType": "unconfigured"}],
            })
        with self.assertRaises(ValueError):
            EndpointFieldMappingDesign.model_validate({
                **base,
                "fieldMappings": [{
                    "endpointField": endpoint_field,
                    "mappingType": "through_entity",
                    "entityField": {"entityId": "scene:Order", "fieldId": "id", "path": "id", "type": "string"},
                }],
            })
        with self.assertRaises(ValueError):
            EndpointFieldMappingDesign.model_validate({
                **base,
                "sceneEntities": [],
                "fieldMappings": [],
            })

    def test_endpoint_implementation_description_is_optional_and_bounded(self) -> None:
        """Endpoint 实现描述可以缺省，填写后必须是有限长度文本。"""

        base = {
            "apiContractId": "orders-api",
            "endpointId": "orders.list",
            "endpointContract": {"method": "GET", "path": "/orders"},
            "artifactRevision": "0123456789abcdef0123456789abcdef",
            "fieldMappings": [],
            "sourceSnapshots": [],
            "basedOn": [{"artifactKey": "technical-plan", "sha256": "0" * 64}],
            "confirmedAt": "2026-01-01T00:00:00Z",
        }
        self.assertIsNone(EndpointFieldMappingDesign.model_validate(base).implementation_description)
        with self.assertRaises(ValueError):
            EndpointFieldMappingDesign.model_validate({
                **base, "implementationDescription": "x" * 4001,
            })

    def test_initial_payload_restores_current_field_mappings(self) -> None:
        """重新打开 Endpoint 设计时保留当前实现描述和字段映射。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _technical_plan()
            _write_plan(workspace, plan)
            fields = endpoint_api_fields(plan["api_contracts"][0], plan["api_contracts"][0]["endpoints"][0])
            mappings = _unconfigured_mappings(fields)
            with patch(
                "app.services.api_design.read_endpoint_design",
                return_value={
                    "implementationDescription": "先校验条件，再执行分页查询。",
                    "fieldMappings": mappings,
                },
            ):
                payload = initial_api_design_payload(workspace, plan, "orders-api", "orders.create")
            self.assertEqual(payload["draft"]["implementationDescription"], "先校验条件，再执行分页查询。")
            self.assertEqual(payload["draft"]["fieldMappings"], mappings)

    def test_business_description_projection_and_flow_include_description(self) -> None:
        """业务说明的可读流和结构化投影保留字段路径。"""

        design = {
            "apiContractId": "orders-api",
            "endpointId": "orders.list",
            "fieldMappings": [{
                "endpointField": {
                    "side": "request", "location": "query", "path": "current",
                    "type": "integer", "required": False, "description": "",
                },
                "mappingType": "business_description",
                "businessDescription": "用于控制分页查询。",
            }],
        }
        self.assertIn("用于控制分页查询", api_design_mapping_flows([design])[0])
        self.assertEqual(api_design_business_descriptions([design])[0]["path"], "current")

    def test_confirm_business_description_writes_only_field_mappings(self) -> None:
        """业务说明确认后正式产物不再包含 nodes 或 mappings。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _technical_plan()
            _write_plan(workspace, plan)
            fields = endpoint_api_fields(plan["api_contracts"][0], plan["api_contracts"][0]["endpoints"][0])
            mappings = _configured_mappings(fields)
            required = next(item for item in fields if item["path"] == "orderId")
            mappings = _replace_mapping(mappings, {
                "endpointField": _snapshot(required),
                "mappingType": "business_description",
                "businessDescription": "将请求标识交给业务服务处理。",
            })
            result = confirm_api_design(
                workspace,
                plan,
                {
                    "action": "confirm",
                    "apiContractId": "orders-api",
                    "endpointId": "orders.create",
                    "draft": {
                        "fieldMappings": mappings,
                        "implementationDescription": "  先校验请求参数。  ",
                    },
                },
            )
            self.assertNotIn("nodes", result["design"])
            self.assertNotIn("mappings", result["design"])
            self.assertEqual(result["design"]["implementationDescription"], "先校验请求参数。")

    def test_old_graph_shape_is_rejected(self) -> None:
        """确认动作不读取或迁移旧 nodes/mappings 图结构。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _technical_plan()
            _write_plan(workspace, plan)
            with self.assertRaisesRegex(ValueError, "fieldMappings"):
                confirm_api_design(
                    workspace,
                    plan,
                    {
                        "action": "confirm",
                        "apiContractId": "orders-api",
                        "endpointId": "orders.create",
                        "draft": {"nodes": [], "mappings": []},
                    },
                )

    def test_unconfigured_and_source_direction_are_rejected(self) -> None:
        """包括可选字段在内不得未配置，外部请求字段不能作为 Response 来源。"""

        endpoint = {
            "nodeType": "endpoint_field", "id": "endpoint:response",
            "side": "response", "location": "response_body", "path": "id",
            "type": "number", "required": False, "description": "",
        }
        with self.assertRaisesRegex(ValueError, "尚未配置映射"):
            _validate_field_mappings(
                [_mapping_adapter({
                    "endpointField": _snapshot(endpoint),
                    "mappingType": "unconfigured",
                })],
                [endpoint],
            )
        invalid = _mapping_adapter({
            "endpointField": _snapshot(endpoint),
            "mappingType": "source_mapping",
            "processingType": "direct", "sourceFields": [{
                "sourceType": "external_api", "sourceId": "upstream",
                "directoryId": "directory", "operationId": "operation",
                "section": "query", "path": "id", "type": "number",
            }],
        })
        with self.assertRaisesRegex(ValueError, "只能映射外部 API 响应字段"):
            _validate_field_mappings([invalid], [endpoint])

    def test_source_types_read_only_embedded_sources(self) -> None:
        """Build 依赖只来自 fieldMappings 内嵌的来源字段。"""

        self.assertEqual(
            api_design_source_types([{
                "fieldMappings": [{
                    "endpointField": {
                        "side": "response", "location": "response_body", "path": "id",
                        "type": "number", "required": True, "description": "",
                    },
                    "mappingType": "source_mapping",
                    "processingType": "direct", "sourceFields": [{
                        "sourceType": "database", "sourceId": "db", "schema": "app",
                        "table": "orders", "column": "id", "type": "number", "usage": "read",
                    }],
                }],
            }]),
            ["database"],
        )

    def test_source_snapshot_reloads_and_projects_selected_fields(self) -> None:
        """确认时重新读取来源，并只保存实际映射字段。"""

        mapping = _mapping_adapter({
            "endpointField": {
                "side": "response", "location": "response_body", "path": "id",
                "type": "number", "required": True, "description": "",
            },
            "mappingType": "source_mapping",
            "processingType": "direct", "sourceFields": [{
                "sourceType": "database", "sourceId": "db", "schema": "app",
                "table": "orders", "column": "id", "type": "number", "usage": "read",
            }],
        })
        with patch(
            "app.services.api_design.load_database_columns",
            return_value={
                "sourceId": "db", "schema": "app", "table": "orders",
                "columns": [
                    {"name": "id", "type": "number"},
                    {"name": "secret_unused", "type": "string"},
                ],
            },
        ):
            snapshots = _validated_source_snapshots("unused", [mapping])
        self.assertEqual([item["name"] for item in snapshots[0]["details"]["columns"]], ["id"])

    def test_source_snapshot_uses_database_business_name(self) -> None:
        """数据库来源快照保存目录中的业务名称，不把内部数据源 ID 展示给用户。"""

        mapping = _mapping_adapter({
            "endpointField": {
                "side": "response", "location": "response_body", "path": "id",
                "type": "number", "required": True, "description": "",
            },
            "mappingType": "source_mapping",
            "processingType": "direct", "sourceFields": [{
                "sourceType": "database", "sourceId": "db", "schema": "app",
                "table": "orders", "column": "id", "type": "number", "usage": "read",
            }],
        })
        with patch(
            "app.services.api_design.load_database_columns",
            return_value={
                "sourceId": "db", "schema": "app", "table": "orders",
                "columns": [{"name": "id", "type": "number"}],
            },
        ), patch(
            "app.services.api_design.public_catalog",
            return_value=SimpleNamespace(sources=[SimpleNamespace(id="db", name="业务数据库")]),
        ):
            snapshots = _validated_source_snapshots("unused", [mapping])
        self.assertEqual(snapshots[0]["name"], "业务数据库")

    def test_external_operation_sanitization_removes_samples_and_header_values(self) -> None:
        """外部 Operation 快照不携带样例数据或 Header 值。"""

        safe = _safe_external_operation({
            "id": "operation",
            "headers": [{"name": "X-Trace", "value": "secret"}],
            "requestSample": {"token": "secret"},
            "responseSample": {"email": "user@example.com"},
        })
        self.assertEqual(safe["headers"], [{"name": "X-Trace"}])
        self.assertNotIn("requestSample", safe)
        self.assertNotIn("responseSample", safe)

def _mapping_adapter(value: dict):
    """通过正式联合模型解析一条测试字段映射。"""

    mapping_type = value.get("mappingType")
    if mapping_type == "source_mapping":
        return SourceMapping.model_validate(value)
    if mapping_type == "business_description":
        return BusinessDescriptionFieldMapping.model_validate(value)
    from app.domain.api_design import UnconfiguredFieldMapping
    return UnconfiguredFieldMapping.model_validate(value)


def _snapshot(field: dict) -> dict:
    """从工作台字段投影正式 Endpoint 字段快照。"""

    return {
        key: field.get(key)
        for key in ("side", "location", "path", "type", "required", "description")
    }


def _unconfigured_mappings(fields: list[dict]) -> list[dict]:
    """为全部 Endpoint 字段生成未配置记录。"""

    return [
        {"endpointField": _snapshot(field), "mappingType": "unconfigured"}
        for field in fields
    ]


def _configured_mappings(fields: list[dict]) -> list[dict]:
    """为全部 Endpoint 叶子字段生成可确认的业务说明映射。"""

    return [
        {
            "endpointField": _snapshot(field),
            "mappingType": "business_description",
            "businessDescription": f"按业务规则处理 {field['path']}。",
        }
        for field in fields
    ]


def _replace_mapping(mappings: list[dict], replacement: dict) -> list[dict]:
    """按 Endpoint 语义键替换一条字段映射。"""

    endpoint = replacement["endpointField"]
    key = (endpoint["side"], endpoint["location"], endpoint["path"])
    return [
        replacement
        if (
            item["endpointField"]["side"],
            item["endpointField"]["location"],
            item["endpointField"]["path"],
        ) == key
        else item
        for item in mappings
    ]


def _technical_plan() -> dict:
    """返回包含 Header 和请求响应叶子字段的最小 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "entities": [{"id": "Order", "name": "订单", "fields": [{"name": "id", "type": "string"}]}],
        "api_contracts": [{
            "id": "orders-api",
            "entity_ids": ["Order"],
            "schemas": {
                "OrderInput": {"type": "object", "properties": {
                    "customer": {"type": "object", "properties": {"name": {"type": "string"}}},
                }},
                "OrderOutput": {"type": "object", "properties": {
                    "data": {"type": "object", "properties": {"id": {"type": "string"}}},
                }},
            },
            "endpoints": [{
                "id": "orders.create",
                "method": "POST",
                "path": "/orders/{orderId}",
                "parameters": [
                    {"name": "orderId", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
                    {"name": "x-trace-id", "in": "header", "schema": {"type": "string"}},
                ],
                "request_schema_ref": "OrderInput",
                "response_schema_ref": "OrderOutput",
            }],
        }],
    }


def _write_plan(workspace: str, plan: dict) -> None:
    """写入规范 TechnicalPlan 以供指纹计算。"""

    path = Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
