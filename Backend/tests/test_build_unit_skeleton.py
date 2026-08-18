from __future__ import annotations

import unittest

from app.services.build_unit_skeleton import (
    apply_target_unit_dependencies,
    ensure_build_unit_skeleton,
)
from tests.entity_design_test_utils import confirm_entity_designs


def _project_plan() -> dict:
    """构造包含两页和实体设计已确认为数据库的最小确认项目计划。"""

    plan = {
        "version": "plan-v1",
        "architecture": {"frontend": "React", "backend": "FastAPI"},
        "permission_model": {},
        "frontend_pages": [
            {
                "pageId": "orders",
                "references": {
                    "permissions": ["admin"],
                    "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                },
            },
            {
                "pageId": "customers",
                "references": {
                    "permissions": ["anonymous"],
                    "endpoint_dependencies": [{"endpoint_id": "customers.list"}],
                },
            },
        ],
        "entities": [
            {
                "id": "Order",
                "name": "Order",
                "fields": [],
            },
            {
                "id": "Customer",
                "name": "Customer",
                "fields": [],
            },
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "endpoints": [{"id": "orders.list"}],
            },
            {
                "id": "customers-api",
                "entity_ids": ["Customer"],
                "endpoints": [{"id": "customers.list"}],
            },
        ],
    }
    return confirm_entity_designs(plan, source_type="database")


class BuildUnitSkeletonTests(unittest.TestCase):
    def test_mixed_source_types_build_backend_and_static_units(self) -> None:
        """混合数据库与静态数据源时同时生成后端、数据库与前端 Mock 数据 Unit。"""

        project_plan = {
            **_project_plan(),
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                },
                {
                    "id": "Customer",
                    "name": "Customer",
                    "fields": [],
                },
                {
                    "id": "Weather",
                    "name": "Weather",
                    "fields": [],
                },
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "endpoints": [{"id": "orders.list"}],
                },
                {
                    "id": "customers-api",
                    "entity_ids": ["Customer"],
                    "endpoints": [{"id": "customers.list"}],
                },
                {
                    "id": "weather-api",
                    "entity_ids": ["Weather"],
                    "endpoints": [{"id": "weather.get"}],
                },
            ],
            "frontend_pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "permissions": ["admin"],
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                    },
                },
                {
                    "pageId": "customers",
                    "references": {
                        "permissions": ["anonymous"],
                        "endpoint_dependencies": [{"endpoint_id": "customers.list"}],
                    },
                },
                {
                    "pageId": "weather",
                    "references": {
                        "permissions": ["admin"],
                        "endpoint_dependencies": [{"endpoint_id": "weather.get"}],
                    },
                },
            ],
        }
        project_plan = confirm_entity_designs(
            project_plan,
            source_type="database",
            entity_ids=["Order"],
        )
        project_plan = confirm_entity_designs(
            project_plan,
            source_type="static",
            entity_ids=["Customer"],
        )
        project_plan = confirm_entity_designs(
            project_plan,
            source_type="external_api",
            entity_ids=["Weather"],
        )

        plan = ensure_build_unit_skeleton(project_plan, {})
        units = plan["build_units"]
        self.assertIn("database:database", units)
        self.assertIn("frontend:data:static", units)
        self.assertIn("backend:endpoint:orders-api:orders.list", units)
        self.assertIn("backend:endpoint:weather-api:weather.get", units)
        self.assertIn("backend:bootstrap", units)
        self.assertIn("frontend:api-client", units)
        self.assertNotIn("database:external_api", units)
        self.assertEqual(plan["unit_graph"]["validation"]["is_valid"], True)

    def test_builds_all_plan_units_and_endpoint_page_edges(self) -> None:
        plan = ensure_build_unit_skeleton(
            _project_plan(),
            {"workspace_revision": "workspace-v1", "tech_stack": ["React"]},
        )

        self.assertEqual(plan["schema_version"], "build-dag.v3")
        self.assertIn("page:orders", plan["build_units"])
        self.assertIn("page:customers", plan["build_units"])
        self.assertIn("database:database", plan["build_units"])
        self.assertIn("backend:endpoint:orders-api:orders.list", plan["build_units"])
        self.assertIn("frontend:api-client", plan["build_units"])
        self.assertEqual(
            plan["build_units"]["backend:endpoint:orders-api:orders.list"]["kind"],
            "backend",
        )
        self.assertIn(
            {"from": "backend:endpoint:orders-api:orders.list", "to": "page:orders", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertNotIn(
            {"from": "database:orders", "to": "backend:endpoint:orders-api:orders.list", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertIn(
            {"from": "frontend:auth-guard", "to": "page:orders", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertNotIn(
            {"from": "frontend:auth-guard", "to": "page:customers", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertEqual(plan["build_units"]["page:orders"]["status"], "not_prepared")

    def test_skips_database_dependency_for_scoped_build_without_database_units(self) -> None:
        """endpoint/page 范围无 database:* 单元时，不接入数据库依赖边且不报缺失。"""

        plan = ensure_build_unit_skeleton(_project_plan(), {})
        scoped = apply_target_unit_dependencies(
            plan,
            {
                "required_unit_ids": [
                    "backend:bootstrap",
                    "backend:endpoint:orders-api:orders.list",
                ],
                "database_endpoint_refs": [
                    ("database", "backend:endpoint:orders-api:orders.list"),
                ]
            },
        )

        self.assertNotIn(
            {"from": "database:database", "to": "backend:endpoint:orders-api:orders.list", "type": "depends_on"},
            scoped["unit_graph"]["edges"],
        )
        self.assertTrue(scoped["unit_graph"]["validation"]["is_valid"])

    def test_adds_database_dependency_for_application_scope(self) -> None:
        """全量构建范围含 database:* 单元时，数据库依赖边保持接入。"""

        plan = ensure_build_unit_skeleton(_project_plan(), {})
        scoped = apply_target_unit_dependencies(
            plan,
            {
                "required_unit_ids": list(plan["build_units"].keys()),
                "database_endpoint_refs": [
                    ("database", "backend:endpoint:orders-api:orders.list"),
                ]
            },
        )
        edges = scoped["unit_graph"]["edges"]
        self.assertIn(
            {"from": "database:database", "to": "backend:endpoint:orders-api:orders.list", "type": "depends_on"},
            edges,
        )
        self.assertTrue(scoped["unit_graph"]["validation"]["is_valid"])

    def test_static_builds_frontend_data_units_without_backend_units(self) -> None:
        """Static 骨架只建立前端内存数据模块到页面的依赖。"""

        project_plan = _project_plan()
        project_plan = confirm_entity_designs(project_plan, source_type="static")
        plan = ensure_build_unit_skeleton(project_plan, {})

        self.assertIn("frontend:data:static", plan["build_units"])
        self.assertNotIn("database:database", plan["build_units"])
        self.assertNotIn("backend:bootstrap", plan["build_units"])
        self.assertNotIn("backend:endpoint:orders-api:orders.list", plan["build_units"])
        self.assertIn(
            {"from": "frontend:data:static", "to": "page:orders", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )

    def test_reuses_unchanged_skeleton_without_rebuilding_units(self) -> None:
        snapshot = {"workspace_revision": "workspace-v1", "tech_stack": ["React"]}
        initial = ensure_build_unit_skeleton(_project_plan(), snapshot)
        initial["build_units"]["page:orders"]["task_ids"] = ["page:orders:ui"]

        reused = ensure_build_unit_skeleton(_project_plan(), snapshot, initial)

        self.assertTrue(reused["unit_skeleton"]["reused"])
        self.assertEqual(reused["build_units"]["page:orders"]["task_ids"], ["page:orders:ui"])

    def test_workspace_change_rebuilds_skeleton_metadata(self) -> None:
        initial = ensure_build_unit_skeleton(
            _project_plan(), {"workspace_revision": "workspace-v1"}
        )

        rebuilt = ensure_build_unit_skeleton(
            _project_plan(),
            {"workspace_revision": "workspace-v2"},
            initial,
        )

        self.assertFalse(rebuilt["unit_skeleton"]["reused"])
        self.assertEqual(rebuilt["unit_skeleton"]["workspace_revision"], "workspace-v2")


if __name__ == "__main__":
    unittest.main()
