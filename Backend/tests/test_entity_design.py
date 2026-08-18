from __future__ import annotations

import unittest

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
from app.services.detail_review import (
    _entity_design_summary,
    _entity_review_items,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from tests.entity_design_test_utils import confirm_entity_designs


def _plan_with_entities() -> dict:
    """构造包含实体定义的 ProjectPlan 测试夹具。"""

    return create_project_plan(create_requirement_spec("创建商品管理系统"))


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
                "external_api_design": {"api_info": {"path": "/api/users"}},
                "acceptance_criteria": ["可正常查询"],
            }
        )
        self.assertEqual(submit["data_source_type"], "external_api")
        self.assertEqual(submit["acceptance_criteria"], ["可正常查询"])
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
                "external_api_design": {
                    "api_info": {"path": "/api/products", "method": "GET"},
                    "field_mappings": [],
                },
                "business_rules": [{"name": "编码唯一", "rule_type": "unique"}],
                "acceptance_criteria": ["列表可查询"],
                "risks": ["接口延迟"],
            },
        )
        self.assertEqual(detail["design_stage"], ENTITY_DESIGN_STAGE_REVIEW_READY)
        self.assertEqual(detail["data_source_type"], "external_api")
        self.assertEqual(
            detail["external_api_design"]["api_info"]["path"],
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

    def test_external_api_attach_generates_same_name_mappings(self) -> None:
        """外部 API 方案按返回体字段生成同名绑定建议。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity, default_datasource_type="external_api")
        entity_field_name = detail["fields"][0]["name"]
        action = {
            "action": "submit_external_api",
            "entity_id": entity["id"],
            "api_info": {
                "path": "/api/products",
                "method": "GET",
                "response_body": {entity_field_name: "示例值", "extra": 1.0},
            },
        }
        attach_external_api_design(detail, action)
        design = detail["external_api_design"]
        self.assertEqual(design["api_info"]["path"], "/api/products")
        self.assertTrue(any(mapping["rule"] == "same_name" for mapping in design["field_mappings"]))
        self.assertFalse(entity_design_validation_errors(plan, detail))

    def test_external_api_nested_response_generates_nested_mapping(self) -> None:
        """外部 API 方案按嵌套返回体路径生成映射建议。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan, entity, default_datasource_type="external_api"
        )
        first_field = detail["fields"][0]["name"]
        attach_external_api_design(
            detail,
            {
                "action": "submit_external_api",
                "entity_id": entity["id"],
                "api_info": {
                    "path": "/api/products",
                    "method": "GET",
                    "response_body": {
                        "data": {
                            "items": [
                                {first_field: "示例值"},
                            ]
                        }
                    },
                },
            },
        )
        mappings = {
            str(mapping["entity_field"]): mapping
            for mapping in detail["external_api_design"]["field_mappings"]
        }
        self.assertEqual(mappings[first_field]["source_field"], f"data.items.{first_field}")
        self.assertEqual(mappings[first_field]["rule"], "nested_match")

    def test_external_api_missing_path_fails_validation(self) -> None:
        """外部 API 方案缺少请求路径时校验失败。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan, entity, default_datasource_type="external_api"
        )
        attach_external_api_design(
            detail,
            {
                "action": "submit_external_api",
                "entity_id": entity["id"],
                "api_info": {"method": "GET", "response_body": {"name": "示例值"}},
            },
        )
        errors = entity_design_validation_errors(plan, detail)
        self.assertTrue(any("必须补充接口路径" in error for error in errors))

    def test_submit_external_api_full_design_passes_validation(self) -> None:
        """外部 API 全量提交（路径/方式/返回体/映射）后进入 review 且校验通过。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(
            plan,
            entity,
            default_datasource_type="external_api",
            design_stage=ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT,
        )
        field_name = detail["fields"][0]["name"]
        apply_complete_entity_design(
            detail,
            {
                "data_source_type": "external_api",
                "external_api_design": {
                    "api_info": {
                        "path": "/api/products",
                        "method": "GET",
                        "response_body": {field_name: "示例值"},
                    },
                    "field_mappings": [
                        {
                            "entity_field": field_name,
                            "source_field": field_name,
                            "rule": "same_name",
                        }
                    ],
                },
                "acceptance_criteria": ["列表可查询"],
            },
        )
        self.assertEqual(detail["design_stage"], ENTITY_DESIGN_STAGE_REVIEW_READY)
        self.assertFalse(entity_design_validation_errors(plan, detail))

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
            {
                "action": "submit_external_api",
                "entity_id": entity["id"],
                "api_info": {"path": "/api/products", "method": "GET"},
            },
        )
        summary = entity_design_summary(detail)
        self.assertEqual(summary["entity_id"], entity["id"])
        self.assertEqual(summary["external_api_design"]["path"], "/api/products")
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
        entities = _entity_review_items(plan, selected_entity_id=entity["id"])
        summary = _entity_design_summary(plan, entity["id"], entities)
        status_field = next(
            field
            for field in summary["fields"]
            if str(field.get("name") or "") == "status"
        )
        self.assertEqual(status_field["enum_values"], ["on", "off"])


if __name__ == "__main__":
    unittest.main()
