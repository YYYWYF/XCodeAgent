from __future__ import annotations

import unittest

from app.services.build_context_resolver import resolve_target_build_context


def _detail_ref(identifier: str) -> dict:
    """构造已确认详情 artifact 的轻量引用。"""

    return {
        "status": "confirmed",
        "json_path": f".xcodeagent/plans/{identifier}.json",
        "sha256": f"sha-{identifier}",
    }


def _project_plan() -> dict:
    """构造页面按 endpoint 直接关联数据源详情的测试计划。"""

    return {
        "frontend_pages": [
            {
                "pageId": "orders",
                "detail_design": _detail_ref("page-orders"),
                "references": {"permissions": ["admin"]},
            },
            {
                "pageId": "customers",
                "detail_design": _detail_ref("page-customers"),
                "references": {"permissions": ["admin"]},
            },
        ],
        "data_sources": [
            {"id": "orders", "detail_design": _detail_ref("data-source-orders")},
            {"id": "customers", "detail_design": _detail_ref("data-source-customers")},
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
        "page_detail_plans": [
            {
                "pageId": "orders",
                "references": {"endpoint_dependencies": [{"endpoint_id": "orders.list"}]},
            },
            {
                "pageId": "customers",
                "references": {"endpoint_dependencies": [{"endpoint_id": "customers.list"}]},
            },
        ],
        "data_source_detail_plans": [
            {"data_source_id": "orders", "entities": [{"name": "Order"}]},
            {"data_source_id": "customers", "entities": [{"name": "Customer"}]},
        ],
    }


class PageBuildContextResolverTests(unittest.TestCase):
    def test_page_context_loads_only_direct_data_source_details(self) -> None:
        context = resolve_target_build_context(
            _project_plan(), target_type="page", target_id="orders"
        )

        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(context["data_source_ids"], ["orders"])
        self.assertEqual(
            [detail["data_source_id"] for detail in context["direct_data_source_details"]],
            ["orders"],
        )
        self.assertIn("data-source:orders", context["required_unit_ids"])
        self.assertNotIn("data-source:customers", context["required_unit_ids"])
        self.assertIn("app:auth-guard", context["required_unit_ids"])

    def test_data_source_context_does_not_load_page_details(self) -> None:
        context = resolve_target_build_context(
            _project_plan(), target_type="data_source", target_id="orders"
        )

        self.assertIsNone(context["page_detail"])
        self.assertEqual(context["data_source_detail"]["data_source_id"], "orders")
        self.assertEqual(context["required_unit_ids"], ["app:backend-bootstrap", "data-source:orders"])

    def test_page_context_rejects_unknown_endpoint(self) -> None:
        plan = _project_plan()
        plan["page_detail_plans"][0]["references"]["endpoint_dependencies"] = [
            {"endpoint_id": "orders.unknown"}
        ]

        with self.assertRaisesRegex(ValueError, "unknown endpoint orders.unknown"):
            resolve_target_build_context(plan, target_type="page", target_id="orders")


if __name__ == "__main__":
    unittest.main()
