"""API 设计 LangGraph 节点测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.graph.nodes.api_design import api_design, api_design_readiness_gate


class ApiDesignNodeTests(unittest.TestCase):
    """验证 API 设计等待、确认续接和开发阻断语义。"""

    def test_initial_node_waits_with_api_design_mode(self) -> None:
        """独立设计入口必须投影 api_design 交互而不继续 Build。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            result = api_design(
                {
                    "workspace": workspace,
                    "project_plan": plan,
                    "selected_api_contract_id": "orders-api",
                    "selected_endpoint_id": "orders.list",
                }
            )
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "api_design")
            payload = result["clarification"]["apiDesign"]
            self.assertNotIn("sources", payload)
            self.assertNotIn("databaseMetadata", payload)
            self.assertNotIn("externalOperation", payload)

    def test_confirmed_empty_endpoint_finishes_design_run(self) -> None:
        """无可绑定叶子字段的 Endpoint 确认后直接完成本次运行。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            result = api_design(
                {
                    "workspace": workspace,
                    "project_plan": plan,
                    "selected_api_contract_id": "orders-api",
                    "selected_endpoint_id": "orders.list",
                    "api_design_action": {
                        "action": "confirm",
                        "apiContractId": "orders-api",
                        "endpointId": "orders.list",
                        "draft": {"sceneEntities": [], "fieldMappings": []},
                    },
                }
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["api_design_result"]["status"], "confirmed")
            self.assertIn("继续当前 Endpoint", result["message"])

    def test_readiness_node_blocks_without_design(self) -> None:
        """开发前置缺失时返回结构化列表且不会自动进入 API 设计。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            result = api_design_readiness_gate(
                {
                    "workspace": workspace,
                    "project_plan": plan,
                    "selected_api_contract_id": "orders-api",
                    "selected_endpoint_id": "orders.list",
                }
            )
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "api_design_required")


def _plan() -> dict:
    """返回无业务叶子字段的最小 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "entities": [],
        "api_contracts": [
            {
                "id": "orders-api",
                "schemas": {},
                "endpoints": [{"id": "orders.list", "method": "GET", "path": "/orders"}],
            }
        ],
    }


def _write_plan(workspace: str, plan: dict) -> None:
    """写入规范 TechnicalPlan 以供指纹计算。"""

    path = Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
