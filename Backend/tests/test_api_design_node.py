"""API 设计 LangGraph 节点测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.domain.api_design import EndpointApiDesign
from app.graph.nodes.api_design import _missing_mapping_copy, api_design_readiness_gate
from app.workspace.endpoint_design_documents import technical_plan_sha256, write_endpoint_design


class ApiDesignNodeTests(unittest.TestCase):
    """验证页面与接口开发映射门禁的等待、刷新和版本确认语义。"""

    def test_direct_runtime_bypasses_endpoint_mapping_gate(self) -> None:
        """Direct Runtime 由正式 API Schema 生成 DTO，不要求字段来源映射。"""

        plan = {
            **_plan(),
            "topology": {
                "type": "agent_runtime_direct",
                "publicEdgeServiceId": "agent-runtime",
                "serviceIds": ["agent-runtime"],
            },
        }
        result = api_design_readiness_gate(
            {
                "workspace": "/direct-runtime-workspace",
                "project_plan": plan,
                "selected_api_contract_id": "orders-api",
                "selected_endpoint_id": "orders.list",
            }
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["api_design_readiness"]["bypassed_by_topology"],
            "agent_runtime_direct",
        )
        self.assertEqual(result["clarification"], {})

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
            self.assertEqual(
                result["clarification"]["message"],
                "当前开发目标还缺字段映射，开发已暂停。",
            )
            question = result["clarification"]["questions"][0]["question"]
            self.assertIn("尚未完成字段映射", question)
            self.assertNotIn("尚未完成设计", question)
            self.assertNotIn("已失效", result["clarification"]["message"])

    def test_missing_mapping_copy_distinguishes_pending_and_stale(self) -> None:
        """未配置不得写成已失效，契约变更才提示重新配置。"""

        pending, pending_question = _missing_mapping_copy(
            [{"status": "pending", "method": "GET", "path": "/orders"}]
        )
        self.assertEqual(pending, "当前开发目标还缺字段映射，开发已暂停。")
        self.assertIn("尚未完成字段映射", pending_question)
        stale, stale_question = _missing_mapping_copy(
            [{"status": "stale", "method": "GET", "path": "/orders"}]
        )
        self.assertEqual(stale, "当前开发目标的字段映射已失效，需要重新配置后继续开发。")
        self.assertIn("已失效", stale_question)

    def test_endpoint_ready_waits_for_version_confirmation(self) -> None:
        """接口自身映射有效后仍需用户确认当前版本。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            result = api_design_readiness_gate(_endpoint_state(workspace, plan))
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "api_design_confirmation")
            self.assertEqual(len(result["api_design_result"]["designs"]), 1)

    def test_page_confirmation_binds_all_endpoint_versions(self) -> None:
        """页面门禁必须回显并确认其全部关联 Endpoint 版本。"""

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
            waiting = api_design_readiness_gate(state)
            designs = waiting["api_design_result"]["designs"]
            self.assertEqual(len(designs), 2)
            confirmed = api_design_readiness_gate(
                {
                    **state,
                    "api_design_gate_action": {
                        "action": "confirm",
                        "targetType": "page",
                        "targetId": "orders",
                        "versions": [
                            {
                                "apiContractId": item["apiContractId"],
                                "endpointId": item["endpointId"],
                                "artifactRevision": item["artifactRevision"],
                            }
                            for item in designs
                        ],
                    },
                }
            )
            self.assertEqual(confirmed["status"], "completed")
            self.assertTrue(confirmed["api_design_result"]["confirmedForDevelopment"])

    def test_changed_revision_returns_to_confirmation(self) -> None:
        """确认时任一映射版本变化都必须回到当前结果确认态。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            result = api_design_readiness_gate(
                {
                    **_endpoint_state(workspace, plan),
                    "api_design_gate_action": {
                        "action": "confirm",
                        "targetType": "endpoint",
                        "targetId": "orders.list",
                        "apiContractId": "orders-api",
                        "versions": [{
                            "apiContractId": "orders-api",
                            "endpointId": "orders.list",
                            "artifactRevision": "f" * 32,
                        }],
                    },
                }
            )
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "api_design_confirmation")


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
