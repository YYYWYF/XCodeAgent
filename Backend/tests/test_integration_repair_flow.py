from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agents.repair_planner.planner import (
    plan_repairs_with_repair_planner_agent,
)
from app.graph.subgraphs.testing import api_contract_check, repair_planning
from app.workspace.code_changes import CapturedWorkspaceChanges


class IntegrationRepairFlowTests(unittest.TestCase):
    def test_contract_mismatch_overrides_model_confirmation_decision(self) -> None:
        """验证模型建议确认时，契约错误仍确定性生成数据源修复任务。"""

        revision_request = {
            "id": "revision:api_contract",
            "owner": "data_source",
            "owners": ["data_source"],
            "reason": "API 契约有效",
            "evidence": "API contract api does not define data_source_id.",
            "failed_check": {
                "id": "api_contract",
                "name": "API 契约有效",
                "failure_category": "contract_mismatch",
            },
        }
        with patch(
            "app.agents.repair_planner.planner._invoke_repair_planner_agent",
            return_value=(
                '{"decision":"requires_user_confirmation",'
                '"reason":"change contract"}'
            ),
        ):
            result = plan_repairs_with_repair_planner_agent(
                test_report={"generated_at": "2026-07-20T00:00:00Z"},
                revision_requests=[revision_request],
                build_execution_scope={"type": "page", "targetId": "orders"},
                scoped_tasks=[
                    {
                        "id": "orders-api",
                        "owner": "data_source",
                        "unit_id": "data-source:orders",
                        "allowed_paths": ["apps/demo/backend/orders.py"],
                    }
                ],
            )

        self.assertEqual(result["decision"], "repair")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["owner"], "data_source")
        self.assertEqual(result["tasks"][0]["unit_id"], "data-source:orders")
        self.assertEqual(
            result["tasks"][0]["allowed_paths"],
            ["apps/demo/backend/orders.py"],
        )

    def test_dirty_build_is_not_classified_as_project_plan_mismatch(self) -> None:
        """验证仅构建未完成时不会误触发 ProjectPlan 契约修订。"""

        with patch(
            "app.graph.subgraphs.testing.validate_api_contract_consistency",
            return_value=[],
        ):
            result = api_contract_check(
                {"build_summary": {"failed": 1, "pending": 0}},
                {},
            )

        failed_check = result["test_results"][0]
        self.assertFalse(failed_check["passed"])
        self.assertEqual(failed_check["failure_category"], "build_incomplete")

    def test_budget_exhaustion_persists_terminal_plan(self) -> None:
        """验证修复预算耗尽时会持久化最新终止计划和计数。"""

        with patch(
            "app.graph.subgraphs.testing.write_repair_task_plan_json",
            return_value="/tmp/repair-task-plan.json",
        ) as writer, patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent"
        ) as planner:
            result = repair_planning(
                {
                    "quality_gate_passed": False,
                    "repair_iteration": 3,
                    "max_repair_iterations": 3,
                }
            )

        self.assertEqual(result["integration_next_action"], "handle_failure")
        self.assertEqual(result["repair_iteration"], 3)
        self.assertEqual(result["max_repair_iterations"], 3)
        self.assertEqual(
            result["repair_task_plan"]["reason"],
            "Integration repair iteration budget exhausted.",
        )
        self.assertEqual(result["repair_task_plan_path"], "/tmp/repair-task-plan.json")
        writer.assert_called_once()
        planner.assert_not_called()

    def test_contract_mismatch_generates_repair_task(self) -> None:
        """验证 API 契约错误交给 RepairPlanner 生成可执行修复任务。"""

        revision_request = {
            "id": "revision:api_contract",
            "evidence": "API contract api does not define data_source_id.",
            "failed_check": {"failure_category": "contract_mismatch"},
        }
        repair_task = {
            "id": "repair:api_contract:data_source",
            "owner": "data_source",
            "status": "pending",
        }
        repair_plan = {
            "status": "ready",
            "decision": "repair",
            "tasks": [repair_task],
        }
        with patch(
            "app.graph.subgraphs.testing.write_repair_task_plan_json",
            return_value="/tmp/repair-task-plan.json",
        ), patch(
            "app.graph.subgraphs.testing.capture_agent_file_changes",
            side_effect=lambda **kwargs: CapturedWorkspaceChanges(
                value=kwargs["action"](),
                code_change_set=None,
            ),
        ), patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent",
            return_value=repair_plan,
        ) as planner:
            result = repair_planning(
                {
                    "quality_gate_passed": False,
                    "repair_iteration": 0,
                    "max_repair_iterations": 3,
                    "revision_requests": [revision_request],
                }
            )

        self.assertEqual(result["integration_next_action"], "repair_build")
        self.assertEqual(result["repair_iteration"], 0)
        self.assertEqual(result["repair_task_plan"], repair_plan)
        self.assertEqual(result["repair_tasks"], [repair_task])
        self.assertEqual(
            planner.call_args.kwargs["revision_requests"],
            [revision_request],
        )

    def test_scope_confirmation_approval_dispatches_candidate_without_spending_budget(self) -> None:
        """批准稳定范围计划后复用受限候选任务，预算仍由 build 的真实派发计数。"""

        candidate = {
            "id": "repair:plan:frontend_build:frontend",
            "kind": "repair",
            "owner": "frontend",
            "unit_id": "page:orders",
            "allowed_paths": ["apps/demo/frontend/src/Orders.tsx"],
            "status": "pending",
        }
        result = repair_planning(
            {
                "quality_gate_passed": False,
                "request": "回答：批准修复范围",
                "repair_iteration": 0,
                "repair_task_plan": {
                    "decision": "requires_user_confirmation",
                    "status": "requires_user_confirmation",
                    "planId": "plan-1",
                    "candidateTasks": [candidate],
                    "tasks": [],
                },
            }
        )

        self.assertEqual(result["integration_next_action"], "repair_build")
        self.assertEqual(result["repair_tasks"], [candidate])
        self.assertEqual(result["repair_task_plan"]["approvedPlanId"], "plan-1")
        self.assertNotIn("repair_iteration", result)

    def test_same_failure_plan_uses_unique_task_id_per_real_repair_attempt(self) -> None:
        """同一失败证据保持稳定 planId，但每次真实修复任务拥有唯一 attempt ID。"""

        revision_request = {
            "id": "revision:frontend_build",
            "owner": "frontend",
            "reason": "前端构建失败",
            "evidence": "Type error",
            "failed_check": {"id": "frontend_build", "name": "前端构建", "failure_category": "type_error"},
        }
        kwargs = {
            "test_report": {"generated_at": "2026-07-22T00:00:00Z"},
            "revision_requests": [revision_request],
            "build_execution_scope": {"type": "page", "targetId": "orders"},
            "scoped_tasks": [
                {
                    "id": "orders-page",
                    "owner": "frontend",
                    "unit_id": "page:orders",
                    "allowed_paths": ["apps/demo/frontend/src/Orders.tsx"],
                }
            ],
        }
        with patch(
            "app.agents.repair_planner.planner._invoke_repair_planner_agent",
            return_value='{"decision":"repair","strategy":"fix type error"}',
        ):
            first = plan_repairs_with_repair_planner_agent(**kwargs, repair_attempt=1)
            second = plan_repairs_with_repair_planner_agent(**kwargs, repair_attempt=2)

        self.assertEqual(first["planId"], second["planId"])
        self.assertNotEqual(first["tasks"][0]["id"], second["tasks"][0]["id"])
        self.assertEqual(second["tasks"][0]["repair_attempt"], 2)


if __name__ == "__main__":
    unittest.main()
