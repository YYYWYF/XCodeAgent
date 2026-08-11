from __future__ import annotations

import unittest

from app.agents.main.task_preparer import _task_preparation_datasource_type
from app.services.entity_definitions import (
    contract_data_source_id,
    entity_ids,
    plan_data_sources,
)
from app.services.project_plan import (
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

    def test_plan_groups_entities_into_sources_with_default_type(self) -> None:
        """规划阶段实体按数据源类型聚合，类型默认 database。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        plan = create_project_plan(spec, datasource_type="database")
        self.assertNotIn("data_sources", plan)
        sources = plan_data_sources(plan)
        self.assertEqual(
            entity_ids(spec["entities"]),
            [entity_id for source in sources for entity_id in entity_ids(source["entities"])],
        )
        self.assertTrue(all(source["type"] == "database" for source in sources))
        self.assertTrue(all(entity["data_source"] == "database" for entity in plan["entities"]))
        self.assertEqual(plan["architecture"]["backend_tech_stack"]["database"], "MySQL8")
        self.assertEqual(validate_project_plan_datasource_policy(plan, "database"), [])

    def test_plan_accepts_mixed_per_source_types(self) -> None:
        """规划模型可给不同实体选择数据库/静态/外部 API，架构按源聚合。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        user_entity = next(entity for entity in spec["entities"] if entity["id"] == "User")
        role_entity = next(entity for entity in spec["entities"] if entity["id"] == "Role")
        core_entity = next(
            entity for entity in spec["entities"] if entity.get("module_id")
        )
        plan = create_project_plan(
            spec,
            datasource_type="database",
            authoritative_agent_plan=True,
            agent_plan={
                "data_sources": [
                    {"id": "database", "type": "database", "entities": [user_entity]},
                    {"id": "static", "type": "static", "entities": [role_entity]},
                    {"id": "external_api", "type": "external_api", "entities": [core_entity]},
                ]
            },
        )
        types = {source["type"] for source in plan_data_sources(plan)}
        self.assertEqual(types, {"database", "static", "external_api"})
        self.assertEqual(
            {entity["data_source"] for entity in plan["entities"]},
            {"database", "static", "external_api"},
        )
        self.assertEqual(validate_project_plan_datasource_policy(plan, "database"), [])
        self.assertIn("MySQL8", plan["architecture"]["backend_tech_stack"]["database"])
        self.assertIn("前端内存", plan["architecture"]["frontend"])

    def test_plan_normalizes_invalid_source_type_to_default(self) -> None:
        """规划把 mock 等非法类型归一为应用默认类型，不落盘非法值。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={
                "data_sources": [{"id": "user_source", "type": "mock"}]
            },
        )
        self.assertEqual(plan_data_sources(plan)[0]["type"], "database")
        self.assertEqual(validate_project_plan_datasource_policy(plan, "database"), [])

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

    def test_plan_derives_data_source_type_from_entity(self) -> None:
        """契约绑定实体，data_source_id 由实体数据源类型推导。"""

        spec = create_requirement_spec("创建一个产品管理系统")
        plan = create_project_plan(
            spec,
            datasource_type="database",
            authoritative_agent_plan=True,
            agent_plan={
                "data_sources": [
                    {
                        "id": "database",
                        "type": "database",
                        "entities": [{"id": "Product", "name": "产品"}],
                    }
                ],
                "api_contracts": [
                    {
                        "id": "product_api",
                        "entity_ids": ["Product"],
                        "resource": "Product",
                        "base_path": "/api/product",
                        "schemas": {
                            "Product": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                            }
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
                ],
            },
        )
        sources = plan_data_sources(plan)
        source_ids = {source["id"] for source in sources}
        self.assertIn("database", source_ids)
        product_source = next(
            source
            for source in sources
            if source["id"] == "database"
        )
        self.assertEqual(product_source["type"], "database")
        contract = plan["api_contracts"][0]
        self.assertEqual(contract["entity_ids"], ["Product"])
        self.assertNotIn("data_source_id", contract)
        self.assertEqual(contract_data_source_id(plan, contract), "database")
        self.assertEqual(plan["entities"][0]["data_source"], "database")
        self.assertEqual(
            validate_project_plan_datasource_policy(plan, "database"),
            [],
        )

    def test_missing_contract_source_is_auto_added(self) -> None:
        """契约绑定未分配数据源的实体时，按默认类型自动补源，不再报未知数据源。"""

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
        self.assertEqual(
            validate_project_plan_datasource_policy(plan, "static"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
