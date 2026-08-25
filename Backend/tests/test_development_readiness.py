from __future__ import annotations

import unittest

from app.graph.nodes.development_readiness import development_readiness_gate
from app.services.development_readiness import development_readiness
from tests.entity_design_test_utils import confirm_entity_designs


def _technical_plan() -> dict:
    """构造页面/API共享一个实体的最小 TechnicalPlan 运行时投影。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "entities": [{"id": "Order", "name": "订单", "fields": []}],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "endpoints": [
                    {
                        "id": "orders.list",
                        "method": "GET",
                        "path": "/api/orders",
                    }
                ],
            }
        ],
        "pages": [
            {
                "pageId": "orders_page",
                "references": {
                    "endpoint_dependencies": [{"endpoint_id": "orders.list"}]
                },
            }
        ],
    }


class DevelopmentReadinessTests(unittest.TestCase):
    def test_page_reports_missing_entity_source_binding(self) -> None:
        readiness = development_readiness(
            _technical_plan(),
            target_type="page",
            target_id="orders_page",
        )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["missing_entities"], [{"entity_id": "Order", "entity_name": "订单"}])

    def test_endpoint_is_ready_after_entity_source_binding(self) -> None:
        plan = confirm_entity_designs(_technical_plan(), source_type="database")
        readiness = development_readiness(
            plan,
            target_type="endpoint",
            target_id="orders.list",
            api_contract_id="orders-api",
        )

        self.assertTrue(readiness["ready"])

    def test_gate_requires_manual_entity_source_binding(self) -> None:
        result = development_readiness_gate(
            {"project_plan": _technical_plan(), "selectedPageId": "orders_page"}
        )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "entity_source_binding_required")
        self.assertEqual(result["clarification"]["missing_entities"][0]["entity_id"], "Order")


if __name__ == "__main__":
    unittest.main()
