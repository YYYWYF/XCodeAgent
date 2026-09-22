"""API 设计 LangGraph 节点测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.domain.api_design import EndpointApiDesign
from app.graph.nodes.api_design import api_design_readiness_gate
from app.services.api_design import ApiDesignError
from app.workspace.endpoint_design_documents import technical_plan_sha256, write_endpoint_design


class ApiDesignNodeTests(unittest.TestCase):
    """验证页面与接口开发映射门禁的等待、刷新和直接放行语义。"""

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

    def test_required_gate_projects_all_endpoint_states(self) -> None:
        """部分 Endpoint 已完成时，门禁澄清仍投影目标范围内的完整状态列表。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            result = api_design_readiness_gate(
                {
                    "workspace": workspace,
                    "project_plan": plan,
                    "selectedPageId": "orders",
                }
            )
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(
                [(item["endpoint_id"], item["status"]) for item in result["api_design_readiness"]["api_designs"]],
                [("orders.list", "confirmed"), ("orders.create", "pending")],
            )
            self.assertEqual(
                [item["endpoint_id"] for item in result["clarification"]["apiDesigns"]],
                ["orders.list", "orders.create"],
            )

    def test_endpoint_ready_shows_gate_before_refresh(self) -> None:
        """接口自身映射有效时首次仍展示门禁，用户刷新检测后才继续。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            waiting = api_design_readiness_gate(_endpoint_state(workspace, plan))
            self.assertEqual(waiting["status"], "requires_user_input")
            self.assertEqual(waiting["clarification"]["mode"], "api_design_required")
            self.assertEqual(waiting["clarification"]["missingApiDesigns"], [])
            self.assertEqual(waiting["clarification"]["apiDesigns"][0]["status"], "confirmed")
            completed = api_design_readiness_gate(
                {
                    **_endpoint_state(workspace, plan),
                    "api_design_gate_action": {
                        "action": "refresh",
                        "targetType": "endpoint",
                        "targetId": "orders.list",
                        "apiContractId": "orders-api",
                    },
                }
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["clarification"], {})
            self.assertNotIn("api_design_result", completed)

    def test_page_ready_shows_all_endpoint_states_before_refresh(self) -> None:
        """页面全部接口已完成时仍展示完整列表，便于查看或修改。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            for endpoint_id in ("orders.list", "orders.create"):
                write_endpoint_design(workspace, _empty_design(workspace, endpoint_id))
            result = api_design_readiness_gate(
                {
                    "workspace": workspace,
                    "project_plan": plan,
                    "selectedPageId": "orders",
                }
            )
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["missingApiDesigns"], [])
            self.assertEqual(
                [item["endpoint_id"] for item in result["clarification"]["apiDesigns"]],
                ["orders.list", "orders.create"],
            )

    def test_page_without_endpoints_continues_without_gate(self) -> None:
        """没有关联 Endpoint 的纯静态页面不展示无内容的门禁卡。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            plan["page_implementation_contracts"][0]["requiredEndpointIds"] = []
            _write_plan(workspace, plan)
            result = api_design_readiness_gate(
                {
                    "workspace": workspace,
                    "project_plan": plan,
                    "selectedPageId": "orders",
                }
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["clarification"], {})

    def test_page_refresh_continues_after_all_endpoints_are_ready(self) -> None:
        """页面门禁重新检测全部关联 Endpoint 有效后直接继续。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            for endpoint_id in ("orders.list", "orders.create"):
                write_endpoint_design(workspace, _empty_design(workspace, endpoint_id))
            state = {
                "workspace": workspace,
                "project_plan": plan,
                "selectedPageId": "orders",
            }
            completed = api_design_readiness_gate(
                {
                    **state,
                    "api_design_gate_action": {
                        "action": "refresh",
                        "targetType": "page",
                        "targetId": "orders",
                    },
                }
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["clarification"], {})
            self.assertEqual(len(completed["api_design_readiness"]["api_designs"]), 2)

    def test_removed_confirmation_action_is_rejected(self) -> None:
        """旧 confirm 动作不再属于当前门禁合同。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            with self.assertRaisesRegex(ApiDesignError, "仅支持重新检测"):
                api_design_readiness_gate(
                    {
                        **_endpoint_state(workspace, plan),
                        "api_design_gate_action": {
                            "action": "confirm",
                            "targetType": "endpoint",
                            "targetId": "orders.list",
                            "apiContractId": "orders-api",
                        },
                    },
                )


def _plan() -> dict:
    """返回页面依赖两个无业务叶子字段 Endpoint 的最小 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "entities": [],
        "pages": [{"pageId": "orders", "name": "订单"}],
        "page_implementation_contracts": [{
            "pageId": "orders",
            "requiredEndpointIds": ["orders.list", "orders.create"],
        }],
        "api_contracts": [
            {
                "id": "orders-api",
                "schemas": {},
                "endpoints": [
                    {"id": "orders.list", "method": "GET", "path": "/orders"},
                    {"id": "orders.create", "method": "POST", "path": "/orders"},
                ],
            }
        ],
    }


def _write_plan(workspace: str, plan: dict) -> None:
    """写入规范 TechnicalPlan 以供指纹计算。"""

    path = Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def _endpoint_state(workspace: str, plan: dict) -> dict:
    """构造只检查 orders.list 的接口开发门禁状态。"""

    return {
        "workspace": workspace,
        "project_plan": plan,
        "selected_api_contract_id": "orders-api",
        "selected_endpoint_id": "orders.list",
    }


def _empty_design(workspace: str, endpoint_id: str) -> EndpointApiDesign:
    """构造无映射字段但具有当前 TechnicalPlan 指纹的合法正式设计。"""

    method = "POST" if endpoint_id.endswith(".create") else "GET"
    return EndpointApiDesign.model_validate(
        {
            "apiContractId": "orders-api",
            "endpointId": endpoint_id,
            "endpointContract": {"id": endpoint_id, "method": method, "path": "/orders"},
            "artifactRevision": ("2" if method == "POST" else "1") * 32,
            "fieldMappings": [],
            "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_plan_sha256(workspace)}],
            "confirmedAt": datetime.now(UTC),
        }
    )


if __name__ == "__main__":
    unittest.main()
