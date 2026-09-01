from __future__ import annotations

import unittest

from app.services.build_unit_skeleton import ensure_build_unit_skeleton
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
        self.assertNotIn("database:database", units)
        self.assertIn("frontend:data:static", units)
        self.assertIn("backend:endpoint:orders-api:orders.list", units)
        self.assertIn("backend:endpoint:weather-api:weather.get", units)
        self.assertIn("backend:bootstrap", units)
        self.assertIn("frontend:api-client", units)
        self.assertNotIn("database:external_api", units)
        self.assertEqual(plan["unit_graph"]["validation"]["is_valid"], True)
        self.assertIn(
            {
                "from": "backend:bootstrap",
                "to": "backend:endpoint:orders-api:orders.list",
                "type": "depends_on",
            },
            plan["unit_graph"]["edges"],
        )
        self.assertIn(
            {
                "from": "backend:bootstrap",
                "to": "backend:endpoint:weather-api:weather.get",
                "type": "depends_on",
            },
            plan["unit_graph"]["edges"],
        )

    def test_external_api_only_skeleton_requires_openfeign_bootstrap(self) -> None:
        """纯外部 API 工程创建 OpenFeign bootstrap Unit 和依赖边。"""

        project_plan = confirm_entity_designs(
            _project_plan(),
            source_type="external_api",
        )
        plan = ensure_build_unit_skeleton(project_plan, {})

        self.assertIn("backend:bootstrap", plan["build_units"])
        self.assertIn("backend:endpoint:orders-api:orders.list", plan["build_units"])
        self.assertIn(
            {
                "from": "backend:bootstrap",
                "to": "backend:endpoint:orders-api:orders.list",
                "type": "depends_on",
            },
            plan["unit_graph"]["edges"],
        )

    def test_builds_all_plan_units_and_endpoint_page_edges(self) -> None:
        plan = ensure_build_unit_skeleton(
            _project_plan(),
            {"workspace_revision": "workspace-v1", "tech_stack": ["React"]},
        )

        self.assertEqual(plan["schema_version"], "build-dag.v3")
        self.assertIn("page:orders", plan["build_units"])
        self.assertIn("page:customers", plan["build_units"])
        self.assertNotIn("database:database", plan["build_units"])
        self.assertIn("backend:endpoint:orders-api:orders.list", plan["build_units"])
        self.assertIn("frontend:api-client", plan["build_units"])
        self.assertNotIn("frontend:route-registry", plan["build_units"])
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
        self.assertFalse(
            any(
                edge.get("from") == "frontend:route-registry"
                or edge.get("to") == "frontend:route-registry"
                for edge in plan["unit_graph"]["edges"]
            )
        )
        self.assertEqual(plan["build_units"]["page:orders"]["status"], "not_prepared")

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
