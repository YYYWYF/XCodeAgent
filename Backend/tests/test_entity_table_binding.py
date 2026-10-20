"""验证模拟数据库真实表名从技术规划到目标结构与执行计划保持一致。"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.graph.nodes.planning import _attach_technical_plan_contracts
from app.services.database_requirement_schema import derive_required_database_schema
from app.services.entity_definitions import (
    database_operation_field_errors, entity_design_summaries, entity_mysql_target_table,
    merge_entities, normalize_entities,
)
from app.services.entity_design import compile_entity_database_statements, entity_design_validation_errors
from app.services.entity_detail_plan import create_entity_detail_plan, refresh_entity_detail_table_design
from app.services.entity_table_binding import bind_plan_entity_tables
from app.services.project_plan import TECHNICAL_PLAN_ARTIFACT_TYPE, create_technical_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.detail_design_documents import externalize_detail_designs, hydrate_external_detail_designs
from app.workspace.plan_documents import render_project_plan_markdown


class EntityTableBindingTests(unittest.TestCase):
    """使用纯模型与临时工作区，不调用 SaaS、模型或真实数据库。"""

    def application(self, datasource_key: str = "12ab34cd56ef") -> dict:
        """提供已完成注册的当前 application.json 合同。"""

        return {
            "schemaVersion": 6, "configRevision": 3,
            "auth": {"enable": False},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"status": "REGISTERED", "datasourceKey": datasource_key, "datasourceId": 1},
        }

    def plan(self) -> dict:
        """构造订单与明细两个实体，保留业务 ID 和 API 路径。"""

        return {
            "entities": [
                {"id": "Order", "name": "订单", "fields": [
                    {"name": "amount", "label": "金额", "type": "decimal", "required": True},
                ]},
                {"id": "OrderItem", "name": "订单明细", "fields": [
                    {"name": "order_id", "label": "订单ID", "type": "number", "required": True},
                ]},
            ],
            "api_contracts": [{"id": "order_api", "endpoints": [{"path": "/orders"}]}],
        }

    def bound(self) -> dict:
        """按真实应用 key 绑定一次，供下游链路验证。"""

        return bind_plan_entity_tables(self.plan(), self.application())

    def test_two_apps_and_repeat_binding_keep_distinct_fixed_names(self) -> None:
        """不同 datasourceKey 使用不同表，重复绑定不叠加前缀，不改业务 ID 或 API。"""

        original = self.plan()
        first = bind_plan_entity_tables(original, self.application())
        second = bind_plan_entity_tables(original, self.application("ab1234cd56ef"))
        self.assertEqual([item["table_name"] for item in first["entities"]], [
            "12ab34cd56ef_order", "12ab34cd56ef_order_item",
        ])
        self.assertNotEqual(first["entities"][0]["table_name"], second["entities"][0]["table_name"])
        self.assertEqual(bind_plan_entity_tables(first, self.application()), first)
        self.assertEqual(first["api_contracts"], original["api_contracts"])
        self.assertNotIn("table_name", original["entities"][0])
        first["entities"][0]["name"] = "订单展示名修改"
        self.assertEqual(bind_plan_entity_tables(first, self.application())["entities"][0]["table_name"],
                         "12ab34cd56ef_order")

    def test_missing_or_unknown_registration_blocks_builtin_names(self) -> None:
        """缺失和未知注册都不能用本地 ID 或临时前缀生成模拟表。"""

        for state in [None, {"status": "REGISTERING"}, {"status": "UNKNOWN"}, {"status": "FAILED"}]:
            with self.subTest(registration=state):
                application = self.application()
                if state is None:
                    application.pop("managedDatabase")
                else:
                    application["managedDatabase"] = state
                with self.assertRaises(ValueError):
                    bind_plan_entity_tables(self.plan(), application)

    def test_external_database_does_not_require_registration_or_add_prefix(self) -> None:
        """外部数据库保留原有选表方式，不把 SaaS 身份强加到用户数据库。"""

        application = self.application()
        application["datasource"]["db"] = {"useBuiltin": False}
        application.pop("managedDatabase")
        plan = bind_plan_entity_tables(self.plan(), application)
        self.assertNotIn("table_name", plan["entities"][0])
        self.assertEqual(entity_mysql_target_table(plan["entities"][0])["name"], "order")

    def test_overlong_collision_and_rebinding_are_rejected(self) -> None:
        """不截断超长名称、不随机避让碰撞、不静默更换已绑定身份。"""

        too_long = {"entities": [{"id": "a" * 52, "fields": []}]}
        with self.assertRaisesRegex(ValueError, "64"):
            bind_plan_entity_tables(too_long, self.application())
        boundary = bind_plan_entity_tables({"entities": [{"id": "a" * 51}]}, self.application())
        self.assertEqual(len(boundary["entities"][0]["table_name"]), 64)
        # 两个不同稳定 ID 经业务名规范化后都形成 order_item，必须明确拒绝。
        collision = {"entities": [{"id": "OrderItem"}, {"id": "order_item"}]}
        with self.assertRaisesRegex(ValueError, "冲突"):
            bind_plan_entity_tables(collision, self.application())
        with self.assertRaisesRegex(ValueError, "不一致"):
            bind_plan_entity_tables(self.bound(), self.application("ab1234cd56ef"))
        for invalid in ["order;DROP TABLE users", "other.orders", "UPPER", ""]:
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                bind_plan_entity_tables({"entities": [{"id": "Order", "table_name": invalid}]}, self.application())

    def test_normalization_merge_and_detail_refresh_preserve_binding(self) -> None:
        """规范化、同一实体编辑和详情刷新都不退回未加前缀的名称。"""

        plan = self.bound()
        normalized = normalize_entities(plan["entities"], with_types=True)
        changed = deepcopy(normalized)
        changed[0]["name"] = "新订单名称"
        changed[0].pop("table_name")
        merged = merge_entities(normalized, changed, with_types=True)
        self.assertEqual(merged[0]["table_name"], normalized[0]["table_name"])
        detail = create_entity_detail_plan(plan, merged[0])
        refresh_entity_detail_table_design(detail)
        self.assertEqual(detail["table_name"], normalized[0]["table_name"])
        self.assertEqual(detail["table_design"]["name"], normalized[0]["table_name"])
        self.assertNotIn("table_name", normalize_entities(normalized)[0])

    def test_binding_does_not_discard_existing_artifact_references(self) -> None:
        """分配真实表名只增加该字段，保留当前实体详情的正式引用。"""

        plan = self.plan()
        plan["entities"][0]["source_binding"] = {"json_path": "plans/entities/order.json", "status": "confirmed"}
        plan["entities"][0]["source_binding_status"] = "confirmed"
        bound = bind_plan_entity_tables(plan, self.application())
        self.assertEqual(bound["entities"][0]["source_binding"], plan["entities"][0]["source_binding"])
        self.assertEqual(bound["entities"][0]["source_binding_status"], "confirmed")

    def test_create_sql_schema_and_field_validation_use_same_name(self) -> None:
        """建表编译、数据库需求和字段范围校验不再派生裸表名。"""

        plan = self.bound()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity)
        detail["database_design"] = {"database_operations": [
            {"operation": "create_table", "table": detail["table_design"]},
        ]}
        self.assertEqual(entity_design_validation_errors(plan, detail, builtin=True), [])
        statements = compile_entity_database_statements(detail)
        self.assertIn("`12ab34cd56ef_order`", statements[0])
        self.assertNotIn("`order`", statements[0])
        schema = derive_required_database_schema([
            {"entity_designs": [detail], "data_source_id": "database"},
        ], [{"id": "database", "type": "database", "entities": plan["entities"]}], {"Order"})
        self.assertEqual([table["name"] for table in schema["tables"]], ["12ab34cd56ef_order"])
        origin = {"database_operations": detail["database_design"]["database_operations"]}
        self.assertEqual(database_operation_field_errors(plan["entities"], origin, builtin=True), [])
        bare = deepcopy(origin)
        bare["database_operations"][0]["table"]["name"] = "order"
        self.assertTrue(database_operation_field_errors(plan["entities"], bare, builtin=True))

    def test_detail_submission_cannot_switch_to_bare_or_other_entity_table(self) -> None:
        """绑定、选表和操作提交都必须使用当前实体自己的固定表。"""

        plan = self.bound()
        for wrong in ["order", plan["entities"][1]["table_name"]]:
            with self.subTest(table=wrong):
                detail = create_entity_detail_plan(plan, plan["entities"][0])
                detail["database_design"] = {"matched_table": wrong, "bindings": [
                    {"entity_field": "amount", "table": wrong, "table_column": "amount"},
                ]}
                self.assertTrue(any("真实表名" in error for error in entity_design_validation_errors(plan, detail, builtin=True)))

    def test_markdown_and_externalized_detail_keep_formal_name(self) -> None:
        """真实表名随正式技术文档和外置详情保存，也进入业务生成摘要。"""

        plan = {**self.bound(), "artifact_type": TECHNICAL_PLAN_ARTIFACT_TYPE}
        detail = create_entity_detail_plan(plan, plan["entities"][0])
        detail["status"] = "confirmed"
        plan["entity_detail_plans"] = [detail]
        self.assertIn("数据库真实表名：`12ab34cd56ef_order`", render_project_plan_markdown(plan))
        self.assertEqual(entity_design_summaries(plan, ["Order"])[0]["table_name"], "12ab34cd56ef_order")
        with tempfile.TemporaryDirectory() as workspace:
            compact = externalize_detail_designs({"workspace": workspace}, plan)
            hydrated = hydrate_external_detail_designs(
                Path(workspace) / ".devagentstudio/plans/technical-plan.json", compact,
            )
            self.assertEqual(hydrated["entities"][0]["table_name"], "12ab34cd56ef_order")
            self.assertEqual(hydrated["entity_detail_plans"][0]["table_design"]["name"], "12ab34cd56ef_order")

    def test_technical_plan_generation_and_confirmation_bind_from_application(self) -> None:
        """生成入口及确认前装配都读取应用配置，Markdown 同步丢字段时重新确定绑定。"""

        spec = create_requirement_spec("创建订单管理系统")
        spec["application_config"] = self.application()
        plan = create_technical_plan(spec, agent_plan={"entities": self.plan()["entities"]})
        self.assertEqual(plan["entities"][0]["table_name"], "12ab34cd56ef_order")
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / ".devagentstudio/application.json"
            target.parent.mkdir()
            target.write_text(json.dumps(self.application()), encoding="utf-8")
            attached = _attach_technical_plan_contracts({
                "workflow_scope": "application_planning", "workspace": workspace,
                "product_plan": {}, "ui_designs": {},
            }, {"artifact_type": TECHNICAL_PLAN_ARTIFACT_TYPE,
                "entities": self.plan()["entities"], "pages": [], "api_contracts": []})
            self.assertEqual(attached["entities"][0]["table_name"], "12ab34cd56ef_order")


if __name__ == "__main__":
    unittest.main()
