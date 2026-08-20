from __future__ import annotations

import unittest

from app.agents.data_source.generator import (
    _data_source_execution_context,
    _data_source_generation_prompt,
    _execution_task_packet,
    _task_required_skill_paths,
)
from app.services.build_unit_compiler import apply_unit_compilation


def _task(*, entity_ids: list[str], designs: list[dict]) -> dict:
    """构造包含调度冗余字段的后端 Endpoint 测试任务。"""

    return {
        "id": "category.create.objects",
        "unit_id": "backend:endpoint:category_api:category.create",
        "title": "创建分类对象",
        "description": "实现当前分类接口的对象层。",
        "allowed_paths": ["backend/src/main/java/Category.java"],
        "target_files": ["backend/src/main/java/Category.java"],
        "change_scope": [
            {
                "operation": "add",
                "path": "backend/src/main/java/Category.java",
                "description": "新增分类对象。",
            }
        ],
        "source_refs": {
            "type": "endpoint_detail",
            "target": {
                "type": "endpoint",
                "id": "category.create",
                "api_contract_id": "category_api",
            },
            "endpoint_ids": ["category.create"],
            "entity_ids": entity_ids,
            "entity_designs": designs,
        },
        "acceptance_checks": [{"description": "UNRELATED_ACCEPTANCE_SENTINEL"}],
        "impact_scope": {"summary": "UNRELATED_IMPACT_SENTINEL"},
        "status": "running",
    }


def _project_plan() -> dict:
    """构造同时包含目标和无关正式产物的 TechnicalPlan。"""

    return {
        "pages": [{"id": "unrelated", "name": "UNRELATED_PAGE_SENTINEL"}],
        "api_contracts": [
            {
                "id": "category_api",
                "entity_ids": ["Category"],
                "base_path": "/api/categories",
                "schemas": {
                    "CategoryInput": {
                        "type": "object",
                        "properties": {
                            "category": {"$ref": "#/schemas/CategoryValue"}
                        },
                    },
                    "CategoryValue": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                    "UnusedSchema": {"description": "UNRELATED_SCHEMA_SENTINEL"},
                },
                "endpoints": [
                    {
                        "id": "category.create",
                        "method": "POST",
                        "path": "/api/categories",
                        "request_schema_ref": "CategoryInput",
                        "response_schema_ref": "CategoryValue",
                    },
                    {
                        "id": "category.delete",
                        "summary": "UNRELATED_ENDPOINT_SENTINEL",
                    },
                ],
            },
            {
                "id": "weather_api",
                "endpoints": [
                    {"id": "weather.get", "summary": "UNRELATED_CONTRACT_SENTINEL"}
                ],
            },
        ],
        "endpoint_detail_plans": [
            {
                "api_contract_id": "category_api",
                "endpoint_id": "category.create",
                "status": "confirmed",
                "processing_logic": ["创建分类。"],
            },
            {
                "api_contract_id": "weather_api",
                "endpoint_id": "weather.get",
                "summary": "UNRELATED_DETAIL_SENTINEL",
            },
        ],
        "entity_detail_plans": [
            {
                "entity_id": "Category",
                "status": "confirmed",
                "data_source_type": "database",
                "database_design": {
                    "matched_table": "category",
                    "bindings": [
                        {"entity_field": "name", "table_column": "category_name"}
                    ],
                },
            },
            {
                "entity_id": "Weather",
                "status": "confirmed",
                "data_source_type": "external_api",
                "summary": "UNRELATED_ENTITY_SENTINEL",
            },
        ],
    }


class DataSourceGenerationPromptTests(unittest.TestCase):
    """验证 DataSource 执行提示词的任务级 Skill 路由与最小上下文。"""

    def test_task_skill_paths_follow_exact_entity_source_types(self) -> None:
        """每个任务只声明自身实体来源对应的 Skill 路径。"""

        database = _task(
            entity_ids=["Category"],
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        external = _task(
            entity_ids=["Weather"],
            designs=[{"entity_id": "Weather", "data_source_type": "external_api"}],
        )
        mixed = _task(
            entity_ids=["Category", "Weather"],
            designs=[
                {"entity_id": "Category", "data_source_type": "database"},
                {"entity_id": "Weather", "data_source_type": "external_api"},
            ],
        )

        self.assertEqual(
            _task_required_skill_paths(database),
            ["/.xcodeagent/builtin-skills/springboot-mybatis-generate/SKILL.md"],
        )
        self.assertEqual(
            _task_required_skill_paths(external),
            ["/.xcodeagent/builtin-skills/springboot-external-api-generate/SKILL.md"],
        )
        self.assertEqual(len(_task_required_skill_paths(mixed)), 2)

    def test_static_backend_task_is_rejected(self) -> None:
        """static 实体若误入后端执行器，应在调用模型前失败。"""

        task = _task(
            entity_ids=["Notice"],
            designs=[{"entity_id": "Notice", "data_source_type": "static"}],
        )
        with self.assertRaisesRegex(ValueError, "不得处理 static"):
            _task_required_skill_paths(task)

    def test_prompt_contains_only_execution_fields_and_targeted_artifacts(self) -> None:
        """提示词排除全局计划与调度字段，同时保留目标正式设计。"""

        task = _task(
            entity_ids=["Category"],
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        prompt = _data_source_generation_prompt(
            project_plan=_project_plan(),
            build_task_plan={"summary": {"note": "UNRELATED_BUILD_SUMMARY"}},
            tasks=[task],
        )

        self.assertIn("springboot-mybatis-generate/SKILL.md", prompt)
        self.assertIn("CategoryInput", prompt)
        self.assertIn("CategoryValue", prompt)
        self.assertIn("matched_table", prompt)
        for sentinel in (
            "UNRELATED_PAGE_SENTINEL",
            "UNRELATED_SCHEMA_SENTINEL",
            "UNRELATED_ENDPOINT_SENTINEL",
            "UNRELATED_CONTRACT_SENTINEL",
            "UNRELATED_DETAIL_SENTINEL",
            "UNRELATED_ENTITY_SENTINEL",
            "UNRELATED_ACCEPTANCE_SENTINEL",
            "UNRELATED_IMPACT_SENTINEL",
            "UNRELATED_BUILD_SUMMARY",
        ):
            self.assertNotIn(sentinel, prompt)
        self.assertNotIn("ProjectPlan context:", prompt)
        self.assertNotIn("BuildTaskPlan summary:", prompt)
        self.assertNotIn("Code graph navigation contract", prompt)
        self.assertEqual(prompt.count("outer integration-test phase"), 1)

    def test_execution_context_keeps_only_current_contract_and_designs(self) -> None:
        """定向上下文按任务标识精确筛选三类正式输入。"""

        task = _task(
            entity_ids=["Category"],
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        context = _data_source_execution_context(_project_plan(), [task])

        self.assertEqual([item["id"] for item in context["api_contracts"]], ["category_api"])
        self.assertEqual(
            [item["id"] for item in context["api_contracts"][0]["endpoints"]],
            ["category.create"],
        )
        self.assertEqual(
            set(context["api_contracts"][0]["schemas"]),
            {"CategoryInput", "CategoryValue"},
        )
        self.assertEqual(
            [item["entity_id"] for item in context["entity_designs"]],
            ["Category"],
        )

    def test_execution_packet_drops_scheduler_only_fields(self) -> None:
        """最小任务包不携带验收、状态和影响分析等调度字段。"""

        task = _task(
            entity_ids=["Category"],
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        packet = _execution_task_packet(task)

        self.assertNotIn("acceptance_checks", packet)
        self.assertNotIn("impact_scope", packet)
        self.assertNotIn("status", packet)
        self.assertNotIn("entity_designs", packet["source_refs"])


class DataSourceTaskCompilationTests(unittest.TestCase):
    """验证 Unit 编译阶段按任务实体子集隔离后端来源。"""

    def test_endpoint_task_filters_designs_to_declared_entities(self) -> None:
        """混合 Endpoint 中的单实体任务不会继承其他来源实体。"""

        unit_id = "backend:endpoint:dashboard_api:dashboard.get"
        tasks = apply_unit_compilation(
            {"build_units": {unit_id: {"id": unit_id}}},
            [
                {
                    "id": "orders.repository",
                    "unit_id": unit_id,
                    "source_refs": {"entity_ids": ["Order"]},
                }
            ],
            {
                "target": {"type": "endpoint", "id": "dashboard.get"},
                "endpoint_ids": ["dashboard.get"],
                "entity_ids": ["Order", "Weather"],
                "entity_designs": [
                    {"entity_id": "Order", "data_source_type": "database"},
                    {"entity_id": "Weather", "data_source_type": "external_api"},
                ],
                "source_refs": {},
            },
        )

        self.assertEqual(tasks[0]["source_refs"]["entity_ids"], ["Order"])
        self.assertEqual(
            [item["entity_id"] for item in tasks[0]["source_refs"]["entity_designs"]],
            ["Order"],
        )

    def test_multi_entity_endpoint_task_requires_explicit_entity_subset(self) -> None:
        """多实体 Endpoint 任务缺少 entity_ids 时拒绝进入执行阶段。"""

        unit_id = "backend:endpoint:dashboard_api:dashboard.get"
        with self.assertRaisesRegex(ValueError, "source_refs.entity_ids"):
            apply_unit_compilation(
                {"build_units": {unit_id: {"id": unit_id}}},
                [{"id": "ambiguous", "unit_id": unit_id, "source_refs": {}}],
                {
                    "target": {"type": "endpoint", "id": "dashboard.get"},
                    "endpoint_ids": ["dashboard.get"],
                    "entity_ids": ["Order", "Weather"],
                    "entity_designs": [
                        {"entity_id": "Order", "data_source_type": "database"},
                        {"entity_id": "Weather", "data_source_type": "external_api"},
                    ],
                    "source_refs": {},
                },
            )

    def test_bootstrap_inherits_current_backend_entity_sources(self) -> None:
        """bootstrap 只继承当前目标中的 database/external_api 实体。"""

        tasks = apply_unit_compilation(
            {"build_units": {"backend:bootstrap": {"id": "backend:bootstrap"}}},
            [{"id": "bootstrap", "unit_id": "backend:bootstrap", "source_refs": {}}],
            {
                "target": {"type": "endpoint", "id": "dashboard.get"},
                "endpoint_ids": ["dashboard.get"],
                "entity_ids": ["Order", "Weather", "Notice"],
                "entity_designs": [
                    {"entity_id": "Order", "data_source_type": "database"},
                    {"entity_id": "Weather", "data_source_type": "external_api"},
                    {"entity_id": "Notice", "data_source_type": "static"},
                ],
                "source_refs": {},
            },
        )

        self.assertEqual(tasks[0]["source_refs"]["entity_ids"], ["Order", "Weather"])
        self.assertEqual(len(_task_required_skill_paths(tasks[0])), 2)


if __name__ == "__main__":
    unittest.main()
