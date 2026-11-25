from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.development_readiness import development_readiness_gate


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
    def test_gate_uses_endpoint_mapping_without_entity_binding(self) -> None:
        """显式旧入口只走 API 映射门禁，不再要求实体绑定。"""

        plan = _technical_plan()
        for ready, action in ((False, {}), (True, {"action": "refresh"})):
            with self.subTest(ready=ready), patch(
                "app.graph.nodes.api_design.api_design_readiness",
                return_value={
                    "ready": ready,
                    "api_designs": [{"endpointId": "orders.list"}],
                    "missing_api_designs": [] if ready else [{"method": "GET", "path": "/api/orders"}],
                },
            ):
                result = development_readiness_gate({
                    "workspace": "/tmp/endpoint-mapping", "project_plan": plan,
                    "selectedPageId": "orders_page", "api_design_gate_action": action,
                })
                self.assertEqual(result["phase"], "api_design_readiness_gate")
                self.assertEqual(result["status"], "completed" if ready else "requires_user_input")
                if not ready:
                    self.assertEqual(result["clarification"]["mode"], "api_design_required")
                    self.assertNotIn("missing_entities", result["clarification"])
                self.assertNotIn("entity_detail_plans", plan)


if __name__ == "__main__":
    unittest.main()
