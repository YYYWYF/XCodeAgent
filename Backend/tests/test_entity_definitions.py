from __future__ import annotations

import unittest

from app.services.database_requirement_schema import derive_required_database_schema
from app.services.entity_detail_plan import (
    attach_entity_detail_plan,
    create_entity_detail_plan,
)
from app.services.entity_definitions import (
    database_operation_field_errors,
    entity_json_schema,
    entity_mysql_target_table,
    entity_table_name,
    merge_entities,
    normalize_entities,
    plan_data_sources,
    validate_entity_definitions,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.page_detail_plan import _default_endpoint_data_origin
from app.workspace.spec_documents import render_requirement_spec_markdown


class EntityDefinitionTests(unittest.TestCase):
    def test_legacy_string_entities_normalize_to_objects(self) -> None:
        """旧字符串实体兼容为实体对象且保留 id。"""

        entities = normalize_entities(["Book", "Author"])
        self.assertEqual([item["id"] for item in entities], ["Book", "Author"])
        self.assertEqual(entities[0]["name"], "Book")
        self.assertEqual(entities[0]["fields"], [])

    def test_requirement_fields_keep_only_display_info(self) -> None:
        """需求层实体字段只保留名称与说明，不生成字段名和类型。"""

        entities = normalize_entities(
            [
                {
                    "id": "Book",
                    "name": "书籍",
                    "fields": [
                        {"name": "title", "label": "书名", "type": "text", "required": True},
                        {"name": "price", "label": "价格", "type": "decimal"},
                    ],
                }
            ]
        )
        fields = entities[0]["fields"]
        self.assertEqual([item["label"] for item in fields], ["书名", "价格"])
        self.assertNotIn("name", fields[0])
        self.assertNotIn("type", fields[0])
        self.assertNotIn("required", fields[0])

    def test_plan_fields_generate_names_and_whitelist_types(self) -> None:
        """规划层为展示信息生成字段名与类型，未知类型回退文本并去重。"""

        entities = normalize_entities(
            [
                {
                    "id": "Book",
                    "name": "书籍",
                    "fields": [
                        {"label": "书名", "type": "text", "required": True},
                        {"label": "价格", "type": "decimal"},
                        {"label": "书名", "type": "text"},
                        {"label": "状态", "type": "unknown_type"},
                    ],
                }
            ],
            with_types=True,
        )
        fields = entities[0]["fields"]
        self.assertEqual([item["label"] for item in fields], ["书名", "价格", "状态"])
        self.assertEqual(fields[0]["name"], "field_1")
        self.assertEqual(fields[0]["required"], True)
        self.assertEqual(fields[1]["name"], "field_2")
        self.assertEqual(fields[2]["type"], "text")

    def test_plan_fields_derive_ascii_names_from_labels(self) -> None:
        """ASCII 标签可派生 snake_case 字段名。"""

        entities = normalize_entities(
            [{"id": "Book", "fields": [{"label": "Publish Date", "type": "date"}]}],
            with_types=True,
        )
        self.assertEqual(entities[0]["fields"][0]["name"], "publish_date")

    def test_validation_reports_duplicate_and_reserved_names(self) -> None:
        """显式校验入口能报告重复实体、保留字段和非法类型。"""

        errors = validate_entity_definitions(
            [
                {"id": "Book", "fields": [{"name": "id", "type": "text"}]},
                {"id": "Book", "fields": [{"name": "price", "type": "mysql_type"}]},
            ],
            with_types=True,
        )
        self.assertTrue(any("重复" in error for error in errors))
        self.assertTrue(any("保留字段" in error for error in errors))
        self.assertTrue(any("类型非法" in error for error in errors))

    def test_merge_entities_preserves_stable_ids_at_both_levels(self) -> None:
        """需求层合并只保留展示信息，规划层合并保留字段名与类型。"""

        existing = [
            {
                "id": "Book",
                "name": "书籍",
                "description": "旧描述",
                "fields": [{"name": "title", "type": "text"}],
            }
        ]
        incoming = [
            {
                "id": "book",
                "name": "书籍管理",
                "description": "新描述",
                "fields": [{"name": "title", "type": "long_text"}],
            }
        ]
        merged = merge_entities(existing, incoming)
        self.assertEqual(merged[0]["id"], "Book")
        self.assertEqual(merged[0]["name"], "书籍管理")
        self.assertNotIn("name", merged[0]["fields"][0])
        self.assertNotIn("type", merged[0]["fields"][0])

        merged_plan = merge_entities(existing, incoming, with_types=True)
        self.assertEqual(merged_plan[0]["id"], "Book")
        self.assertEqual(merged_plan[0]["fields"][0]["name"], "title")
        self.assertEqual(merged_plan[0]["fields"][0]["type"], "long_text")

    def test_entity_json_schema_derives_fields(self) -> None:
        """API Schema 从实体字段派生并自动追加隐式 id。"""

        schema = entity_json_schema(
            {
                "id": "Book",
                "fields": [
                    {"name": "title", "type": "text", "required": True},
                    {"name": "price", "type": "decimal"},
                    {"name": "publish_date", "type": "date"},
                    {"name": "category", "type": "enum", "enum_values": ["novel", "tech"]},
                ],
            }
        )
        self.assertEqual(schema["required"], ["id", "title"])
        self.assertEqual(schema["properties"]["id"], {"type": "string"})
        self.assertEqual(schema["properties"]["price"]["type"], "number")
        self.assertEqual(schema["properties"]["publish_date"]["format"], "date")
        self.assertEqual(schema["properties"]["category"]["enum"], ["novel", "tech"])

    def test_entity_mysql_target_table_mapping(self) -> None:
        """实体确定性编译为目标表，字段类型走固定映射且 id 为主键。"""

        table = entity_mysql_target_table(
            {
                "id": "Book",
                "name": "书籍",
                "description": "书籍实体",
                "fields": [
                    {"name": "title", "type": "text", "required": True},
                    {"name": "price", "type": "decimal"},
                    {"name": "status", "type": "enum"},
                    {"name": "is_active", "type": "boolean"},
                ],
            }
        )
        self.assertEqual(entity_table_name("Book"), "book")
        self.assertEqual(table["name"], "book")
        self.assertEqual(table["primary_key"], ["id"])
        by_name = {column["name"]: column for column in table["columns"]}
        self.assertEqual(by_name["id"]["type"], "BIGINT")
        self.assertEqual(by_name["id"]["auto_increment"], True)
        self.assertEqual(by_name["title"]["type"], "VARCHAR(255)")
        self.assertEqual(by_name["title"]["nullable"], False)
        self.assertEqual(by_name["price"]["type"], "DECIMAL(12,2)")
        self.assertEqual(by_name["status"]["type"], "VARCHAR(32)")
        self.assertEqual(by_name["is_active"]["type"], "TINYINT(1)")

    def test_business_id_field_keeps_single_implicit_id(self) -> None:
        """实体字段名为 id 时由隐式主键承载，schema 与目标表不重复定义。"""

        table = entity_mysql_target_table(
            {
                "id": "Product",
                "fields": [
                    {
                        "name": "id",
                        "label": "商品ID",
                        "type": "number",
                        "required": True,
                    },
                    {"name": "name", "label": "商品名称", "type": "text"},
                ],
            }
        )
        self.assertEqual(
            [column["name"] for column in table["columns"]],
            ["id", "name"],
        )
        schema = entity_json_schema(
            {
                "id": "Product",
                "fields": [
                    {
                        "name": "id",
                        "label": "商品ID",
                        "type": "number",
                        "required": True,
                    }
                ],
            }
        )
        self.assertEqual(list(schema["properties"].keys()), ["id"])
        self.assertEqual(schema["required"], ["id"])

    def test_database_operation_field_validation(self) -> None:
        """未知字段、错误建表名会被拒绝，id 与实体字段允许。"""

        entities = [
            {
                "id": "Book",
                "fields": [
                    {"name": "title", "type": "text"},
                    {"name": "price", "type": "decimal"},
                ],
            }
        ]
        self.assertEqual(
            database_operation_field_errors(
                entities,
                {
                    "effective_source": {"kind": "mysql_new_table"},
                    "database_operations": [
                        {
                            "operation": "create_table",
                            "table": {
                                "name": "book",
                                "columns": [
                                    {"name": "id"},
                                    {"name": "title"},
                                    {"name": "invented_field"},
                                ],
                            },
                        }
                    ],
                },
            ),
            ["create_table 字段 book.invented_field 未在实体定义中。"],
        )
        self.assertEqual(
            database_operation_field_errors(
                entities,
                {
                    "effective_source": {"kind": "mysql_new_table"},
                    "database_operations": [
                        {
                            "operation": "create_table",
                            "table": {
                                "name": "t_book",
                                "columns": [{"name": "title"}],
                            },
                        }
                    ],
                },
            ),
            ["create_table 表名 t_book 不是实体定义的目标表，可用表名：book。"],
        )
        self.assertEqual(
            database_operation_field_errors(
                entities,
                {
                    "effective_source": {"kind": "mysql_existing"},
                    "database_operations": [
                        {"operation": "add_column", "table": "book", "column": "price"}
                    ],
                },
            ),
            [],
        )
        self.assertEqual(database_operation_field_errors([], {}), [])

    def test_required_schema_merges_entity_tables(self) -> None:
        """目标 Schema 以实体为基线，并让操作定义覆盖实体列类型。"""

        targets = [
            {
                "api_contract_id": "book_api",
                "endpoint_id": "book.create",
                "method": "POST",
                "path": "/api/book",
                "data_source_id": "book_source",
                "endpoint_detail": {
                    "data_origin": {
                        "source_type": "database",
                        "effective_source": {
                            "kind": "mysql_new_table",
                            "database": "demo",
                            "tables": ["book"],
                        },
                        "database_operations": [
                            {
                                "operation": "create_table",
                                "database": "demo",
                                "table": {
                                    "name": "book",
                                    "columns": [
                                        {
                                            "name": "title",
                                            "type": "VARCHAR(500)",
                                            "nullable": True,
                                        }
                                    ],
                                    "primary_key": ["id"],
                                },
                            }
                        ],
                    }
                },
            }
        ]
        data_sources = [
            {
                "id": "book_source",
                "name": "书籍数据源",
                "type": "database",
                "entities": [
                    {
                        "id": "Book",
                        "name": "书籍",
                        "fields": [
                            {"name": "title", "type": "text", "required": True},
                            {"name": "price", "type": "decimal"},
                        ],
                    }
                ],
            }
        ]
        required = derive_required_database_schema(
            targets,
            data_sources=data_sources,
        )
        book_table = next(
            table
            for table in required["tables"]
            if table["name"] == "book"
        )
        columns = {column["name"]: column for column in book_table["columns"]}
        # 操作列优先保留模型定义的类型；实体补充缺失的 price 列与 id 主键。
        self.assertEqual(columns["title"]["type"], "VARCHAR(500)")
        self.assertEqual(columns["price"]["type"], "DECIMAL(12,2)")
        self.assertEqual(columns["id"]["type"], "BIGINT")
        self.assertEqual(book_table["primary_key"], ["id"])

    def test_requirement_spec_default_entities_have_fields(self) -> None:
        """需求层只有顶层实体展示信息，规划层生成字段名与类型。"""

        spec = create_requirement_spec("创建一个书籍管理系统")
        self.assertNotIn("data_sources", spec)
        entity_with_fields = next(
            entity
            for entity in spec["entities"]
            if isinstance(entity, dict) and entity.get("fields")
        )
        self.assertTrue(entity_with_fields)
        requirement_field = entity_with_fields["fields"][0]
        self.assertNotIn("name", requirement_field)
        self.assertNotIn("type", requirement_field)
        self.assertTrue(requirement_field["label"])
        markdown = render_requirement_spec_markdown(spec)
        self.assertIn("| 名称 | 说明 |", markdown)
        plan = create_project_plan(spec)
        self.assertEqual(plan_data_sources(plan), [])
        plan_entity = plan["entities"][0]
        self.assertIsInstance(plan_entity, dict)
        self.assertNotIn("data_source", plan_entity)
        self.assertTrue(plan_entity["fields"])
        plan_field = plan_entity["fields"][0]
        self.assertTrue(plan_field["name"])
        self.assertTrue(plan_field["type"])
        self.assertTrue(plan["api_contracts"])
        contract_schemas = plan["api_contracts"][0]["schemas"]
        entity_schema = contract_schemas[next(iter(contract_schemas))]
        self.assertIn("id", entity_schema["properties"])

    def test_default_data_origin_projects_entity_table_names(self) -> None:
        """已确认实体设计把实体对象投影为目标表名时使用 snake_case(id)。"""

        project_plan = {
            "entities": [
                {
                    "id": "Book",
                    "name": "书籍数据源",
                    "fields": [{"name": "title", "type": "text"}],
                }
            ]
        }
        detail = create_entity_detail_plan(
            project_plan,
            project_plan["entities"][0],
            default_datasource_type="database",
        )
        detail["status"] = "confirmed"
        project_plan = attach_entity_detail_plan(project_plan, detail)
        origin = _default_endpoint_data_origin(project_plan, "database")
        self.assertEqual(origin["effective_source"]["tables"], ["book"])


if __name__ == "__main__":
    unittest.main()
