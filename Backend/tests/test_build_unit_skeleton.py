from __future__ import annotations

import unittest

from app.services.build_unit_skeleton import ensure_build_unit_skeleton


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
        "data_sources": [{"id": "orders"}, {"id": "customers"}],
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
    def test_builds_all_plan_units_and_page_data_source_edges(self) -> None:
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
            {"from": "database:orders", "to": "backend:endpoint:orders-api:orders.list", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertIn(
            {"from": "database:orders", "to": "backend:bootstrap", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertIn(
            {"from": "backend:endpoint:orders-api:orders.list", "to": "page:orders", "type": "depends_on"},
            plan["unit_graph"]["edges"],
        )
        self.assertIn(
            {"from": "database:orders", "to": "page:orders", "type": "depends_on"},
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
