from __future__ import annotations

import unittest

from app.services.build_unit_skeleton import (
    apply_target_unit_dependencies,
    ensure_build_unit_skeleton,
)


def _project_plan() -> dict:
    """构造包含两页和两个数据源的最小确认项目计划。"""

    return {
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
        "data_sources": [
            {"id": "orders", "type": "database"},
            {"id": "customers", "type": "database"},
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "data_source_id": "orders",
                "endpoints": [{"id": "orders.list"}],
            },
            {
                "id": "customers-api",
                "data_source_id": "customers",
                "endpoints": [{"id": "customers.list"}],
            },
        ],
    }


class BuildUnitSkeletonTests(unittest.TestCase):
    def test_builds_all_plan_units_and_endpoint_page_edges(self) -> None:
        plan = ensure_build_unit_skeleton(
            _project_plan(),
            {"workspace_revision": "workspace-v1", "tech_stack": ["React"]},
        )

        self.assertEqual(plan["schema_version"], "build-dag.v3")
        self.assertIn("page:orders", plan["build_units"])
        self.assertIn("page:customers", plan["build_units"])
        self.assertIn("database:orders", plan["build_units"])
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

    def test_adds_database_dependency_only_for_database_endpoint_detail(self) -> None:
        """database Unit 只通过数据库来源 EndpointDetail 接到 endpoint Unit。"""

        plan = ensure_build_unit_skeleton(_project_plan(), {})
        scoped = apply_target_unit_dependencies(
            plan,
            {
                "direct_endpoint_details": [
                    {
                        "api_contract_id": "orders-api",
                        "endpoint_id": "orders.list",
                        "data_source_id": "orders",
                        "data_origin": {
                            "source_type": "database",
                            "effective_source": {"kind": "mysql_existing"},
                        },
                    },
                    {
                        "api_contract_id": "customers-api",
                        "endpoint_id": "customers.list",
                        "data_source_id": "customers",
                        "data_origin": {
                            "source_type": "static",
                            "effective_source": {"kind": "frontend_mock"},
                        },
                    },
                ]
            },
        )

        self.assertIn(
            {"from": "database:orders", "to": "backend:endpoint:orders-api:orders.list", "type": "depends_on"},
            scoped["unit_graph"]["edges"],
        )
        self.assertNotIn(
            {"from": "database:customers", "to": "backend:endpoint:customers-api:customers.list", "type": "depends_on"},
            scoped["unit_graph"]["edges"],
        )

    def test_static_builds_frontend_data_units_without_backend_units(self) -> None:
        """Static 骨架只建立前端内存数据模块到页面的依赖。"""

        project_plan = _project_plan()
        project_plan["data_sources"] = [
            {**source, "type": "static"} for source in project_plan["data_sources"]
        ]
        plan = ensure_build_unit_skeleton(project_plan, {})

        self.assertIn("frontend:data:orders", plan["build_units"])
        self.assertNotIn("database:orders", plan["build_units"])
        self.assertNotIn("backend:bootstrap", plan["build_units"])
        self.assertNotIn("backend:endpoint:orders-api:orders.list", plan["build_units"])
        self.assertIn(
            {"from": "frontend:data:orders", "to": "page:orders", "type": "depends_on"},
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
