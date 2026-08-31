from __future__ import annotations

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.entity_design import (
    ENTITY_DESIGN_STAGE_DATA_SOURCE_SELECTION,
    ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT,
    ENTITY_DESIGN_STAGE_REVIEW_READY,
    apply_complete_entity_design,
    apply_entity_design_action,
    attach_external_api_design,
    attach_static_design,
    compile_entity_database_statements,
    entity_bound_design_errors,
    entity_bound_design_gate,
    entity_design_selection_summary,
    entity_design_summary,
    entity_design_validation_errors,
    entity_related_endpoints,
    execute_entity_database_operations,
    list_database_tables,
    normalize_entity_design_action,
    prepare_database_design,
    select_database_table,
)
from app.services.entity_design_assist import entity_design_ai_suggestions
from app.services.entity_detail_plan import (
    attach_entity_detail_plan,
    create_entity_detail_plan,
)
from app.services.entity_definitions import (
    _external_json_shape,
    _mapped_entity_path,
    entity_design_summaries,
)
from app.services.entity_source_binding import (
    _entity_binding_summary,
    _entity_binding_items,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import render_entity_detail_markdown
from tests.entity_design_test_utils import confirm_entity_designs


def _plan_with_entities() -> dict:
    """构造包含实体定义的 ProjectPlan 测试夹具。"""

    return create_project_plan(create_requirement_spec("创建商品管理系统"))


def _external_design(
    plan: dict,
    detail: dict,
    *,
    method: str = "GET",
    path: str = "/api/products",
    response_body: object | None = None,
    entity_payload: bool = True,
) -> dict:
    """构造覆盖当前实体全部 Endpoint 的多操作外部 API 契约。"""

    required_names = [
        str(field.get("name") or "")
        for field in detail.get("fields") or []
        if isinstance(field, dict) and bool(field.get("required"))
    ]
    body = response_body if response_body is not None else {
        name: f"示例-{name}" for name in required_names
    }
    mappings = [
        {"entity_field": name, "source_field": name, "rule": "same_name"}
        for name in required_names
    ] if entity_payload else []
    return {
        "connection": {
            "base_url": "https://api.example.com",
            "base_url_config_key": "integrations.products.base-url",
            "timeout_ms": 10000,
            "headers": [],
        },
        "operations": [
            {
                "operation_id": "products-query",
                "name": "查询商品",
                "endpoint_refs": [
                    {
                        "api_contract_id": item["api_contract_id"],
                        "endpoint_id": item["endpoint_id"],
                    }
                    for item in entity_related_endpoints(
                        plan,
                        str(detail.get("entity_id") or ""),
                    )
                ],
                "api_info": {
                    "method": method,
                    "path": path,
                    "parameters": [],
                    "headers": [],
                    "request_body": None,
                    "response_body": body,
                },
                "response_handling": {
                    "entity_payload": entity_payload,
                    "cardinality": "object",
                    "payload_path": "",
                    "success_status_codes": [200],
                },
                "field_mappings": mappings,
            }
        ],
    }


class EntityDesignServiceTests(unittest.TestCase):
    def test_entity_bound_design_gate_lists_missing_entities(self) -> None:
        """门禁返回缺失实体描述，包含实体 id 与计划中的实体名称。"""

        plan = _plan_with_entities()
        contract = plan["api_contracts"][0]
        entity_ids = list(contract.get("entity_ids") or [])
        self.assertTrue(entity_ids)

        errors, missing = entity_bound_design_gate(plan, contract["id"])

        self.assertTrue(errors)
        self.assertEqual(
            {item["entity_id"] for item in missing},
            set(entity_ids),
        )
        for item in missing:
            entity = next(
                candidate
                for candidate in plan["entities"]
                if candidate.get("id") == item["entity_id"]
            )
            self.assertEqual(item["entity_name"], entity.get("name"))

    def test_entity_bound_design_gate_passes_when_entities_confirmed(self) -> None:
        """绑定实体全部确认后门禁不再返回缺失项。"""

        plan = confirm_entity_designs(_plan_with_entities(), source_type="database")
        contract = plan["api_contracts"][0]

        errors, missing = entity_bound_design_gate(plan, contract["id"])

        self.assertEqual(errors, [])
        self.assertEqual(missing, [])

    def test_entity_bound_design_errors_wraps_gate(self) -> None:
        """兼容包装函数只返回错误文案列表。"""

        plan = _plan_with_entities()
        contract = plan["api_contracts"][0]
        errors = entity_bound_design_errors(plan, contract["id"])
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(item, str) for item in errors))

    def test_selection_summary_lists_three_data_sources(self) -> None:
        """数据源选择摘要包含数据库 / 外部 API / 静态数据三个选项。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        summary = entity_design_selection_summary(entity)
        self.assertEqual(summary["stage"], ENTITY_DESIGN_STAGE_DATA_SOURCE_SELECTION)
        self.assertEqual(summary["entity_id"], entity["id"])
        self.assertEqual(
            {option["value"] for option in summary["data_source_options"]},
            {"database", "external_api", "static"},
        )

    def test_normalize_entity_design_action(self) -> None:
        """实体设计动作只接受白名单动作与合法数据源。"""

        action = normalize_entity_design_action(
            {
                "action": "select_data_source",
                "entity_id": "User",
                "data_source_type": "database",
            }
        )
        self.assertEqual(action["data_source_type"], "database")
        self.assertIsNone(
            normalize_entity_design_action(
                {
                    "action": "select_data_source",
                    "entity_id": "User",
                    "data_source_type": "invalid",
                }
            )
        )
        self.assertEqual(
            normalize_entity_design_action(
                {"action": "list_tables", "entity_id": "User"}
            )["action"],
            "list_tables",
        )
        self.assertEqual(
            normalize_entity_design_action(
                {
                    "action": "select_table",
                    "entity_id": "User",
                    "table_name": "products",
                }
            )["table_name"],
            "products",
        )
        self.assertIsNone(
            normalize_entity_design_action(
                {"action": "select_table", "entity_id": "User"}
            )
        )
        self.assertEqual(
            normalize_entity_design_action(
                {
                    "action": "submit_bindings",
                    "entity_id": "User",
                    "matched_table": "products",
                    "bindings": [],
                }
            )["matched_table"],
            "products",
        )
        self.assertEqual(
            normalize_entity_design_action(
                {
                    "action": "ai_assist",
                    "entity_id": "User",
                    "assist_type": "bindings",
                    "instruction": "按订单表绑定",
                    "context": {"table_columns": ["id", "name"]},
                }
            )["assist_type"],
            "bindings",
        )
        self.assertIsNone(
            normalize_entity_design_action(
                {
                    "action": "ai_assist",
                    "entity_id": "User",
                    "assist_type": "unknown",
                }
            )
        )
        submit = normalize_entity_design_action(
            {
                "action": "submit_entity_design",
                "entity_id": "User",
                "data_source_type": "external_api",
                "external_api_design": {
                    "connection": {},
                    "operations": [],
                },
                "acceptance_criteria": ["可正常查询"],
            }
        )
        self.assertEqual(submit["data_source_type"], "external_api")
        self.assertEqual(submit["acceptance_criteria"], ["可正常查询"])
        patch_submit = normalize_entity_design_action(
            {
                "action": "submit_entity_design",
                "entity_id": "User",
                "data_source_type": "external_api",
                "external_api_design": {
                    "connection": {},
                    "operations": [
                        {
                            "operation_id": "users-patch",
                            "name": "修改用户",
                            "api_info": {"method": "PATCH"},
                        }
                    ],
                },
            }
        )
        self.assertEqual(
            patch_submit["external_api_design"]["operations"][0]["api_info"]["method"],
            "GET",
        )
        self.assertTrue(
            any(
                "不支持的外部 API 请求方式：PATCH" in error
                for error in patch_submit["external_api_design"]["operations"][0].get("validation_errors", [])
            )
        )
        self.assertIsNone(
            normalize_entity_design_action(
                {
                    "action": "submit_entity_design",
                    "entity_id": "User",
                    "data_source_type": "invalid",
                }
            )
        )
        self.assertIsNone(normalize_entity_design_action({"action": "unknown"}))

    def test_apply_complete_entity_design_writes_full_design(self) -> None:
        """单卡片一次性提交写入数据源、分方案与规则/验收/风险。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="external_api",
            design_stage=ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT,
        )
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": _external_design(plan, detail),
                "business_rules": [{"name": "编码唯一", "rule_type": "unique"}],
                "acceptance_criteria": ["列表可查询"],
                "risks": ["接口延迟"],
            },
        )
        self.assertEqual(detail["design_stage"], ENTITY_DESIGN_STAGE_REVIEW_READY)
        self.assertEqual(detail["data_source_type"], "external_api")
        self.assertEqual(
            detail["external_api_design"]["operations"][0]["api_info"]["path"],
            "/api/products",
        )
        self.assertEqual(detail["business_rules"][0]["name"], "编码唯一")
        self.assertEqual(detail["acceptance_criteria"], ["列表可查询"])

    def test_ai_assist_unsupported_type_returns_error_without_model(self) -> None:
        """不支持的 AI 辅助类型直接返回错误建议，不触发模型调用。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        result = entity_design_ai_suggestions(
            entity,
            assist_type="unknown",
        )
        self.assertEqual(result["source"], "error")
        self.assertEqual(result["suggestions"], [])

    def test_ai_api_mapping_filters_fabricated_entity_fields_and_paths(self) -> None:
        """AI 接口映射只保留真实实体字段与响应路径，过滤臆造结果。"""

        entity = {
            "name": "商品",
            "fields": [{"name": "name", "type": "text", "required": True}],
        }
        model = MagicMock()
        model.bind.return_value = model
        model.invoke.return_value = SimpleNamespace(
            content='{"suggestions": ['
            '{"entity_field":"name","source_field":"data.items[].missing"},'
            '{"entity_field":"name","source_field":"data.items[].name"},'
            '{"entity_field":"ghost","source_field":"data.items[].name"}'
            ']}'
        )
        with patch("app.services.entity_design_assist.create_chat_model", return_value=model):
            result = entity_design_ai_suggestions(
                entity,
                assist_type="api_mapping",
                context={
                    "response_body": {"data": {"items": [{"name": "示例"}]}},
                    "response_paths": [
                        "data",
                        "data.items",
                        "data.items[]",
                        "data.items[].name",
                        "data.items[].missing",
                    ],
                },
            )
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(
            result["suggestions"][0]["payload"]["source_field"],
            "data.items[].name",
        )

    def test_list_database_tables_without_workspace_keeps_pending(self) -> None:
        """无工作区时查询表清单返回连接失败状态并保留待选择阶段。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="database",
            design_stage="database_design",
        )
        list_database_tables(detail, workspace_root=None)
        database_design = detail["database_design"]
        self.assertEqual(database_design["table_query_status"], "connection_failed")
        self.assertEqual(database_design["available_tables"], [])
        self.assertEqual(database_design["binding_status"], "pending_table_selection")

    def test_select_database_table_without_workspace_keeps_no_match(self) -> None:
        """选择表后无连接上下文时保留表名并等待用户重新操作。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="database",
            design_stage="database_design",
        )
        select_database_table(detail, "products", workspace_root=None)
        database_design = detail["database_design"]
        self.assertEqual(database_design["matched_table"], "products")
        self.assertEqual(database_design["selected_table"]["name"], "products")
        self.assertEqual(database_design["selected_table"]["columns"], [])
        self.assertEqual(database_design["binding_status"], "no_match")

    def test_submit_bindings_without_matched_table_fails_validation(self) -> None:
        """未选择目标表时提交绑定会被确定性校验拦截。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="database",
            design_stage="database_design",
        )
        field_name = detail["fields"][0]["name"]
        apply_entity_design_action(
            plan,
            detail,
            {
                "action": "submit_bindings",
                "entity_id": entity["id"],
                "bindings": [
                    {
                        "entity_field": field_name,
                        "table_column": field_name,
                    }
                ],
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("必须先选择目标表" in error for error in errors))

    def test_submit_bindings_with_matched_table_passes_validation(self) -> None:
        """选择目标表并绑定合法字段后进入 review 确认门禁。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="database",
            design_stage="database_design",
        )
        field_name = detail["fields"][0]["name"]
        apply_entity_design_action(
            plan,
            detail,
            {
                "action": "submit_bindings",
                "entity_id": entity["id"],
                "matched_table": "products",
                "bindings": [
                    {
                        "entity_field": field_name,
                        "table_column": field_name,
                        "rule": "same_name",
                    }
                ],
            },
        )
        self.assertEqual(detail["design_stage"], "review_ready")
        self.assertEqual(detail["database_design"]["matched_table"], "products")
        self.assertFalse(entity_design_validation_errors(plan, detail))

    def test_database_design_without_workspace_requires_table_generation(self) -> None:
        """数据库方案在缺少连接上下文时进入 no_context 并生成目标表建议。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="database")
        prepare_database_design(plan, entity, detail, workspace_root=None)
        database_design = detail["database_design"]
        self.assertEqual(database_design["binding_status"], "no_context")
        self.assertTrue(database_design["table_generation"]["required"])
        self.assertEqual(
            database_design["table_generation"]["proposal"]["name"],
            str(entity["id"]).lower(),
        )
        self.assertEqual(database_design["differences"][0]["kind"], "missing_table")

    def test_external_api_attach_writes_current_multi_operation_contract(self) -> None:
        """外部 API 动作只接受当前共享连接与多操作契约。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design_input = _external_design(plan, detail)
        mapped_field = str(detail["fields"][0]["name"])
        design_input["operations"][0]["api_info"]["response_body"] = {
            mapped_field: f"示例-{mapped_field}",
        }
        design_input["operations"][0]["field_mappings"] = [
            {
                "entity_field": mapped_field,
                "source_field": mapped_field,
                "rule": "same_name",
            }
        ]
        attach_external_api_design(
            detail,
            {"external_api_design": design_input},
        )
        design = detail["external_api_design"]
        self.assertEqual(design["connection"]["base_url"], "https://api.example.com")
        self.assertEqual(design["operations"][0]["api_info"]["path"], "/api/products")
        self.assertFalse(entity_design_validation_errors(plan, detail))
        markdown = render_entity_detail_markdown(detail)
        self.assertIn("### 共享连接", markdown)
        self.assertIn("integrations.products.base-url", markdown)
        self.assertIn("products-query", markdown)
        self.assertIn("关联 Endpoint", markdown)
        self.assertIn("返回体字段绑定", markdown)

    def test_external_api_removed_single_operation_shape_is_not_read(self) -> None:
        """已移除的单 API 结构不会被迁移、兼容读取或双写。"""

        normalized = normalize_entity_design_action({
            "action": "submit_entity_design",
            "entity_id": "Product",
            "data_source_type": "external_api",
            "external_api_design": {
                "api_info": {"method": "GET", "path": "/legacy"},
                "response_handling": {"cardinality": "object"},
                "field_mappings": [],
            },
        })

        self.assertEqual(normalized["external_api_design"]["connection"], {
            "base_url": "",
            "base_url_config_key": "",
            "timeout_ms": 10000,
            "headers": [],
        })
        self.assertEqual(normalized["external_api_design"]["operations"], [])

    def test_external_api_build_summary_filters_operations_by_endpoint(self) -> None:
        """单 Endpoint 构建摘要只携带其绑定操作及完整共享连接。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        contract = next(
            item
            for item in plan["api_contracts"]
            if str(entity.get("id") or "") in (item.get("entity_ids") or [])
        )
        contract["endpoints"].append({
            "id": "products.extra",
            "method": "GET",
            "path": "/products/extra",
            "summary": "补充接口",
        })
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design = _external_design(plan, detail)
        design["connection"]["headers"] = [{"name": "X-Locale", "value": "en-US"}]
        design["operations"][0]["api_info"]["headers"] = [
            {"name": "x-locale", "value": "zh-CN"}
        ]
        refs = design["operations"][0]["endpoint_refs"]
        first_operation = design["operations"][0]
        first_operation["endpoint_refs"] = [refs[0]]
        second_operation = deepcopy(first_operation)
        second_operation["operation_id"] = "products-extra"
        second_operation["name"] = "补充查询"
        second_operation["endpoint_refs"] = refs[1:]
        design["operations"].append(second_operation)
        attach_external_api_design(detail, {"external_api_design": design})
        detail["status"] = "confirmed"
        plan["entity_detail_plans"] = [detail]

        summary = entity_design_summaries(
            plan,
            [str(entity.get("id") or "")],
            {(refs[0]["api_contract_id"], refs[0]["endpoint_id"])},
        )[0]["external_api_design"]

        self.assertEqual(summary["connection"]["base_url_config_key"], "integrations.products.base-url")
        self.assertEqual(summary["operation_count"], 1)
        self.assertEqual(summary["operations"][0]["operation_id"], "products-query")
        self.assertEqual(
            summary["operations"][0]["effective_connection"]["headers"],
            [{"name": "x-locale", "value": "zh-CN"}],
        )

    def test_external_api_build_summary_projects_product_shapes_without_sample_values(self) -> None:
        """商品外部 API 构建摘要只保留结构、映射和共同数组路径。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        detail["fields"] = [
            {
                "name": "name",
                "label": "商品名称",
                "type": "text",
                "required": True,
                "description": "商品的名称",
            },
            {
                "name": "price",
                "label": "价格",
                "type": "decimal",
                "required": True,
                "description": "商品的销售价格",
            },
            {
                "name": "status",
                "label": "上下架状态",
                "type": "enum",
                "required": True,
                "description": "商品的上架或下架状态",
                "enum_values": ["on", "off"],
            },
            {
                "name": "created_at",
                "label": "创建时间",
                "type": "datetime",
                "required": True,
                "description": "商品记录的创建时间",
            },
        ]
        design = _external_design(plan, detail, method="POST", path="/v1/product/list")
        operation = design["operations"][0]
        operation["api_info"]["request_body"] = {
            "keyword": "PROMPT_SAMPLE_KEYWORD",
            "pageSize": 20,
            "current": 1,
        }
        operation["api_info"]["response_body"] = {
            "total": 10,
            "list": [
                {
                    "name": "PROMPT_SAMPLE_PRODUCT",
                    "price": 5999,
                    "status": "on",
                    "created_at": "2025-04-01 10:00:00",
                },
                {
                    "name": "PROMPT_SAMPLE_PRODUCT_SECOND",
                    "price": 89.9,
                    "status": "off",
                    "created_at": "2025-04-02 10:00:00",
                },
            ],
        }
        operation["field_mappings"] = [
            {
                "entity_field": field["name"],
                "source_field": f"list[].{field['name']}",
                "rule": "nested_match",
            }
            for field in detail["fields"]
        ]
        attach_external_api_design(detail, {"external_api_design": design})
        detail["status"] = "confirmed"
        plan["entity_detail_plans"] = [detail]

        summary = entity_design_summaries(
            plan,
            [str(entity.get("id") or "")],
            {
                (
                    operation["endpoint_refs"][0]["api_contract_id"],
                    operation["endpoint_refs"][0]["endpoint_id"],
                )
            },
        )[0]
        external = summary["external_api_design"]["operations"][0]

        self.assertEqual(external["mapped_entity_path"], "list[]")
        self.assertEqual(external["api_info"]["request_shape"]["root_type"], "object")
        self.assertIn(
            {"path": "pageSize", "type": "integer"},
            external["api_info"]["request_shape"]["fields"],
        )
        self.assertIn(
            {"path": "list[].price", "type": "decimal"},
            external["api_info"]["response_shape"]["fields"],
        )
        self.assertEqual(summary["fields"][2]["enum_values"], ["on", "off"])
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("request_body", external["api_info"])
        self.assertNotIn("response_body", external["api_info"])
        self.assertNotIn("PROMPT_SAMPLE_KEYWORD", serialized)
        self.assertNotIn("PROMPT_SAMPLE_PRODUCT", serialized)

    def test_external_api_shape_projection_bounds_arrays_and_multiple_mapping_branches(self) -> None:
        """结构投影覆盖根数组、空数组、嵌套对象、数量上限和无共同数组前缀。"""

        root_array = _external_json_shape([{"data": {"items": []}}])
        self.assertEqual(root_array["root_type"], "array")
        self.assertIn({"path": "[]", "type": "array"}, root_array["fields"])
        self.assertIn({"path": "[].data", "type": "object"}, root_array["fields"])
        self.assertIn({"path": "[].data.items[]", "type": "array"}, root_array["fields"])
        bounded = _external_json_shape({f"field_{index}": index for index in range(400)})
        self.assertEqual(len(bounded["fields"]), 300)
        self.assertEqual(
            _mapped_entity_path([
                {"source_field": "left[].name"},
                {"source_field": "right[].price"},
            ]),
            "",
        )

    def test_external_api_nested_response_accepts_explicit_nested_mapping(self) -> None:
        """多操作契约接受指向真实嵌套数组路径的显式映射。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan, entity, default_datasource_type="external_api"
        )
        first_field = detail["fields"][0]["name"]
        design = _external_design(
            plan,
            detail,
            response_body={"data": {"items": [{first_field: "示例值"}]}},
        )
        design["operations"][0]["field_mappings"] = [
            {
                "entity_field": first_field,
                "source_field": f"data.items[].{first_field}",
                "rule": "nested_match",
            }
        ]
        attach_external_api_design(detail, {"external_api_design": design})
        mapping = detail["external_api_design"]["operations"][0]["field_mappings"][0]
        self.assertEqual(mapping["source_field"], f"data.items[].{first_field}")

    def test_external_api_missing_path_fails_validation(self) -> None:
        """外部 API 方案缺少请求路径时校验失败。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan, entity, default_datasource_type="external_api"
        )
        design = _external_design(plan, detail)
        design["operations"][0]["api_info"]["path"] = ""
        attach_external_api_design(detail, {"external_api_design": design})
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("必须补充接口路径" in error for error in errors))

    def test_external_api_root_array_uses_canonical_array_path(self) -> None:
        """根数组响应也使用 [] 规范路径，避免数组映射退化为普通字段名。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan, entity, default_datasource_type="external_api"
        )
        first_field = detail["fields"][0]["name"]
        design = _external_design(plan, detail, response_body=[{first_field: "示例值"}])
        design["operations"][0]["response_handling"].update(
            {"cardinality": "array", "payload_path": "[]"}
        )
        design["operations"][0]["field_mappings"] = [
            {"entity_field": first_field, "source_field": f"[].{first_field}", "rule": "nested_match"}
        ]
        attach_external_api_design(detail, {"external_api_design": design})
        mapping = detail["external_api_design"]["operations"][0]["field_mappings"][0]
        self.assertEqual(mapping["source_field"], f"[].{first_field}")

    def test_submit_entity_design_with_external_api_passes_validation(self) -> None:
        """外部 API 全量提交（路径/方式/返回体/映射）后进入 review 且校验通过。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="external_api",
            design_stage=ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT,
        )
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": _external_design(plan, detail),
                "acceptance_criteria": ["列表可查询"],
            },
        )
        self.assertEqual(detail["design_stage"], ENTITY_DESIGN_STAGE_REVIEW_READY)
        self.assertFalse(entity_design_validation_errors(plan, detail))

    def test_external_api_validation_rejects_sensitive_header_and_invalid_url(self) -> None:
        """外部 API 契约拒绝凭据 Header 与带查询串的 Base URL。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design = _external_design(plan, detail)
        design["connection"]["base_url"] = "https://api.example.com?token=secret"
        design["operations"][0]["api_info"]["headers"] = [
            {"name": "Authorization", "value": "Bearer secret"}
        ]
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": design,
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("Base URL 不得包含" in error for error in errors))
        self.assertTrue(any("敏感 Header" in error for error in errors))

    def test_external_api_validation_handles_malformed_ipv6_url(self) -> None:
        """畸形 IPv6 Base URL 返回业务错误，不应中断 AG-UI 校验生命周期。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design = _external_design(plan, detail)
        design["connection"]["base_url"] = "http://[::1"
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": design,
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("合法的 HTTP(S) 地址" in error for error in errors))

    def test_external_api_validation_rejects_empty_mapping_source(self) -> None:
        """正式字段映射不允许保留空 source_field，选填字段未映射时应省略该行。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        field_name = detail["fields"][0]["name"]
        design = _external_design(plan, detail)
        design["operations"][0]["field_mappings"] = [
            {"entity_field": field_name, "source_field": ""}
        ]
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": design,
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("来源字段路径不存在" in error for error in errors))

    def test_external_api_validation_rejects_invalid_pagination_and_mapping_path(self) -> None:
        """分页语义必须引用 Query 参数，字段映射必须指向返回样例路径。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        field_name = detail["fields"][0]["name"]
        design = _external_design(
            plan,
            detail,
            response_body={"data": {"items": [{field_name: "商品"}], "total": 1}},
        )
        operation = design["operations"][0]
        operation["response_handling"] = {
            "entity_payload": True,
            "cardinality": "page",
            "payload_path": "data.items[]",
            "total_path": "data.total",
            "success_status_codes": [200],
            "pagination": {"page_parameter": "page", "size_parameter": "size"},
        }
        operation["field_mappings"] = [
            {"entity_field": field_name, "source_field": "data.missing"}
        ]
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": design,
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("分页页码参数" in error for error in errors))
        self.assertTrue(any("来源字段路径不存在" in error for error in errors))

    def test_external_api_allows_partial_unique_endpoint_coverage(self) -> None:
        """外部 API 可按 Endpoint 分批确认，但同一 Endpoint 不能重复占用。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design = _external_design(plan, detail)
        first_ref = design["operations"][0]["endpoint_refs"][0]
        design["operations"][0]["endpoint_refs"] = [first_ref]
        duplicate = deepcopy(design["operations"][0])
        duplicate["operation_id"] = "products-query-copy"
        duplicate["name"] = "重复查询"
        design["operations"].append(duplicate)
        apply_complete_entity_design(
            detail,
            {"data_source_type": "external_api", "external_api_design": design},
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("不能同时绑定多个上游操作" in error for error in errors))
        self.assertFalse(any("尚未绑定上游操作" in error for error in errors))

    def test_external_api_non_entity_response_does_not_require_mapping(self) -> None:
        """仅状态响应固定为 object，且不要求实体字段映射。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design = _external_design(
            plan,
            detail,
            method="DELETE",
            path="/api/products/{id}",
            response_body={"success": True},
            entity_payload=False,
        )
        operation = design["operations"][0]
        operation["api_info"]["parameters"] = [
            {"name": "id", "in": "path", "type": "string", "required": True}
        ]
        apply_complete_entity_design(
            detail,
            {"data_source_type": "external_api", "external_api_design": design},
        )
        self.assertFalse(entity_design_validation_errors(plan, detail))

    def test_external_api_operation_override_requires_url_and_config_key_pair(self) -> None:
        """操作覆盖 Base URL 时必须同时提供对应的生成配置键。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        design = _external_design(plan, detail)
        design["operations"][0]["connection_override"] = {
            "base_url": "https://other.example.com",
            "authorization": "secret",
        }
        apply_complete_entity_design(
            detail,
            {"data_source_type": "external_api", "external_api_design": design},
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("必须同时提供配置键" in error for error in errors))
        self.assertTrue(any("不允许提交鉴权" in error for error in errors))

    def test_static_validation_rejects_unknown_field_and_bad_enum(self) -> None:
        """静态数据方案校验拒绝实体外的字段与非法枚举取值。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="static")
        attach_static_design(
            detail,
            {
                "seed_rows": [{"unknown_field": "value"}],
                "field_values": {"unknown_field": ["x"]},
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("unknown_field" in error for error in errors))

    def test_static_value_type_validation(self) -> None:
        """静态数据方案按字段类型校验数字/布尔/日期/enum 取值。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        entity["fields"] = [
            {"name": "price", "label": "价格", "type": "number"},
            {"name": "enabled", "label": "启用", "type": "boolean"},
            {"name": "start_date", "label": "开始日期", "type": "date"},
            {"name": "created_at", "label": "创建时间", "type": "datetime"},
            {
                "name": "status",
                "label": "状态",
                "type": "enum",
                "enum_values": ["draft", "approved"],
            },
        ]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="static")
        attach_static_design(
            detail,
            {
                "seed_rows": [
                    {
                        "price": "not-a-number",
                        "enabled": "yes",
                        "start_date": "2026-99-99",
                        "created_at": "not-a-time",
                        "status": "archived",
                    }
                ],
                "field_values": {"status": ["approved"], "price": ["12.5"]},
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("必须是数字" in error for error in errors))
        self.assertTrue(any("必须是布尔值" in error for error in errors))
        self.assertTrue(any("必须是日期（YYYY-MM-DD）" in error for error in errors))
        self.assertTrue(any("必须是日期时间" in error for error in errors))
        self.assertTrue(any("不在枚举值集合内" in error for error in errors))
        self.assertTrue(any("ProjectPlan 允许" in error for error in errors))
        self.assertTrue(any("修订项目计划" in error for error in errors))

    def test_static_valid_values_pass_validation(self) -> None:
        """静态数据合法取值（数字/布尔/日期/枚举）通过校验。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        entity["fields"] = [
            {"name": "price", "label": "价格", "type": "decimal"},
            {"name": "enabled", "label": "启用", "type": "boolean"},
            {"name": "start_date", "label": "开始日期", "type": "date"},
            {"name": "created_at", "label": "创建时间", "type": "datetime"},
            {
                "name": "status",
                "label": "状态",
                "type": "enum",
                "enum_values": ["draft", "approved"],
            },
        ]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="static")
        attach_static_design(
            detail,
            {
                "seed_rows": [
                    {
                        "price": 12.5,
                        "enabled": True,
                        "start_date": "2026-08-16",
                        "created_at": "2026-08-16 10:00:00",
                        "status": "draft",
                    }
                ],
                "field_values": {"status": ["approved"], "price": ["12.5"]},
            },
        )
        self.assertFalse(entity_design_validation_errors(plan, detail))

    def test_apply_action_approve_table_generation_adds_create_table(self) -> None:
        """用户审批后表生成操作进入数据库操作清单。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="database")
        prepare_database_design(plan, entity, detail, workspace_root=None)
        self.assertFalse(detail["database_design"]["database_operations"])
        apply_entity_design_action(
            plan,
            detail,
            {"action": "approve_table_generation", "entity_id": entity["id"]},
        )
        operations = detail["database_design"]["database_operations"]
        self.assertTrue(any(op["operation"] == "create_table" for op in operations))
        statements = compile_entity_database_statements(detail)
        self.assertTrue(any(statement.startswith("CREATE TABLE") for statement in statements))

    def test_execute_skips_without_operations(self) -> None:
        """无数据库操作时落地执行返回 skipped 证据。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="static")
        execute_entity_database_operations(detail, workspace_root=None)
        self.assertEqual(detail["database_execution"]["status"], "skipped")
        self.assertFalse(detail["table_operations_executed"])

    def test_entity_design_summary_projects_stage(self) -> None:
        """实体设计摘要投射当前阶段与方案统计。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="external_api",
            design_stage=ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT,
        )
        attach_external_api_design(
            detail,
            {"external_api_design": _external_design(plan, detail)},
        )
        summary = entity_design_summary(detail)
        self.assertEqual(summary["entity_id"], entity["id"])
        self.assertEqual(summary["external_api_design"]["operation_count"], 1)
        self.assertEqual(summary["external_api_design"]["operations"][0]["path"], "/api/products")
        self.assertEqual(summary["stage"], ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT)

    def test_entity_design_summary_fields_carry_enum_values(self) -> None:
        """实体设计摘要的 fields 携带实体字段与 ProjectPlan 枚举值。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        entity["fields"] = [
            {"name": "name", "label": "名称", "type": "text", "required": True},
            {
                "name": "status",
                "label": "状态",
                "type": "enum",
                "required": False,
                "enum_values": ["on", "off"],
            },
        ]
        detail = create_entity_detail_plan(
            plan, entity, default_datasource_type="static"
        )
        plan = attach_entity_detail_plan(plan, detail)
        entities = _entity_binding_items(plan, selected_entity_id=entity["id"])
        summary = _entity_binding_summary(plan, entity["id"], entities)
        self.assertEqual(
            summary["related_endpoints"],
            entity_related_endpoints(plan, str(entity["id"])),
        )
        status_field = next(
            field
            for field in summary["fields"]
            if str(field.get("name") or "") == "status"
        )
        self.assertEqual(status_field["enum_values"], ["on", "off"])


if __name__ == "__main__":
    unittest.main()
