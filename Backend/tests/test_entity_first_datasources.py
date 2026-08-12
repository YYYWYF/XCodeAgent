from __future__ import annotations

import unittest

from app.agents.main.task_preparer import _task_preparation_datasource_type
from tests.entity_design_test_utils import confirm_entity_designs
from app.services.entity_definitions import (
    contract_data_source_id,
    entity_ids,
    plan_data_sources,
)
from app.services.project_plan import (
    apply_project_plan_datasource_policy,
    create_project_plan,
    validate_project_plan_datasource_policy,
)
from app.services.requirement_spec import create_requirement_spec


class EntityFirstDatasourceTests(unittest.TestCase):
    def test_requirement_spec_has_top_level_entities_without_data_sources(self) -> None:
        """需求阶段只生成顶层实体，不携带数据源与类型。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        self.assertNotIn("data_sources", spec)
        self.assertTrue(spec["entities"])
        module_entity = next(
            entity
            for entity in spec["entities"]
            if entity.get("module_id")
        )
        self.assertTrue(module_entity["module_id"])
        self.assertTrue(module_entity["fields"])
        self.assertNotIn("type", module_entity["fields"][0])

    def test_plan_entities_do_not_carry_data_source(self) -> None:
        """规划阶段实体不生成 data_source，数据源清单为空直到实体设计确认。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        plan = create_project_plan(spec, datasource_type="database")
        self.assertNotIn("data_sources", plan)
        sources = plan_data_sources(plan)
        self.assertEqual(sources, [])
        self.assertTrue(all("data_source" not in entity for entity in plan["entities"]))
        self.assertEqual(
            entity_ids(spec["entities"]),
            entity_ids(plan["entities"]),
        )
        self.assertEqual(plan["architecture"]["backend_tech_stack"]["database"], "MySQL8")
        self.assertEqual(validate_project_plan_datasource_policy(plan), [])

    def test_entity_design_confirmation_resolves_mixed_sources(self) -> None:
        """数据源由实体设计确认决定，可混合数据库/静态/外部 API 并按源聚合。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        user_entity = next(entity for entity in spec["entities"] if entity["id"] == "User")
        role_entity = next(entity for entity in spec["entities"] if entity["id"] == "Role")
        core_entity = next(
            entity for entity in spec["entities"] if entity.get("module_id")
        )
        plan = create_project_plan(
            spec,
            datasource_type="database",
        )
        plan = confirm_entity_designs(
            plan,
            source_type="database",
            entity_ids=[user_entity["id"]],
        )
        plan = confirm_entity_designs(plan, source_type="static", entity_ids=[role_entity["id"]])
        plan = confirm_entity_designs(
            plan,
            source_type="external_api",
            entity_ids=[core_entity["id"]],
        )
        types = {source["type"] for source in plan_data_sources(plan)}
        self.assertEqual(types, {"database", "static", "external_api"})
        self.assertTrue(all("data_source" not in entity for entity in plan["entities"]))
        self.assertEqual(validate_project_plan_datasource_policy(plan), [])
        plan = apply_project_plan_datasource_policy(plan)
        self.assertIn("MySQL8", plan["architecture"]["backend_tech_stack"]["database"])
        self.assertIn("前端内存", plan["architecture"]["frontend"])

    def test_plan_ignores_model_declared_data_sources(self) -> None:
        """模型输出 data_sources 时规划不落盘 data_source，实体保持待实体设计。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={
                "data_sources": [{"id": "user_source", "type": "mock"}]
            },
        )
        self.assertNotIn("data_sources", plan)
        self.assertEqual(plan_data_sources(plan), [])
        self.assertTrue(all("data_source" not in entity for entity in plan["entities"]))
        self.assertEqual(validate_project_plan_datasource_policy(plan), [])

    def test_task_preparation_supports_mixed_types(self) -> None:
        """任务准备类型检测支持混合源：全 static 走前端，其余走后端。"""

        def plan_with_types(types: list[str]) -> dict:
            """构造带 application_skeleton 数据源类型的最小计划。"""

            return {
                "application_skeleton": {
                    "data_sources": [
                        {"id": f"source_{index}", "type": source_type}
                        for index, source_type in enumerate(types)
                    ]
                }
            }

        self.assertEqual(
            _task_preparation_datasource_type(plan_with_types(["static"])),
            "static",
        )
        self.assertEqual(
            _task_preparation_datasource_type(plan_with_types(["database", "static"])),
            "database",
        )
        self.assertEqual(
            _task_preparation_datasource_type(plan_with_types(["external_api"])),
            "database",
        )

    def test_plan_derives_data_source_type_from_entity_design(self) -> None:
        """契约绑定实体，data_source_id 由已确认实体设计的数据源类型推导。"""

        spec = create_requirement_spec("创建一个产品管理系统")
        plan = create_project_plan(spec, datasource_type="database")
        plan = confirm_entity_designs(plan, source_type="database", entity_ids=["Core"])
        sources = plan_data_sources(plan)
        source_ids = {source["id"] for source in sources}
        self.assertIn("database", source_ids)
        product_source = next(
            source
            for source in sources
            if source["id"] == "database"
        )
        self.assertEqual(product_source["type"], "database")
        contract = next(
            contract
            for contract in plan["api_contracts"]
            if contract.get("id") == "core_api"
        )
        self.assertEqual(contract["entity_ids"], ["Core"])
        self.assertNotIn("data_source_id", contract)
        self.assertEqual(contract_data_source_id(plan, contract), "database")
        self.assertNotIn("data_source", next(e for e in plan["entities"] if e["id"] == "Core"))
        self.assertEqual(validate_project_plan_datasource_policy(plan), [])

    def test_missing_contract_source_is_auto_added(self) -> None:
        """契约绑定实体清单外的实体时补空壳实体（无 data_source），确认实体设计后解析来源。"""

        plan = create_project_plan(
            create_requirement_spec("创建一个产品管理系统"),
            datasource_type="static",
            authoritative_agent_plan=True,
            agent_plan={
                "api_contracts": [
                    {
                        "id": "product_api",
                        "entity_ids": ["Product"],
                        "resource": "Product",
                        "base_path": "/api/product",
                        "schemas": {
                            "Product": {"type": "object"}
                        },
                        "endpoints": [
                            {
                                "id": "product_api.list",
                                "method": "GET",
                                "path": "/api/product",
                                "response_schema_ref": "Product",
                            }
                        ],
                    }
                ]
            },
        )
        product_entity = next(entity for entity in plan["entities"] if entity["id"] == "Product")
        self.assertNotIn("data_source", product_entity)
        self.assertEqual(plan_data_sources(plan), [])
        plan = confirm_entity_designs(plan, source_type="static", entity_ids=["Product"])
        product_sources = [
            source
            for source in plan_data_sources(plan)
            if "Product" in entity_ids(source.get("entities"))
        ]
        self.assertTrue(product_sources)
        self.assertEqual(product_sources[0]["type"], "static")
        self.assertEqual(
            contract_data_source_id(plan, plan["api_contracts"][0]),
            product_sources[0]["id"],
        )
        self.assertEqual(validate_project_plan_datasource_policy(plan), [])


if __name__ == "__main__":
    unittest.main()
