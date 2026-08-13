from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agents.repair_planner.planner import (
    plan_repairs_with_repair_planner_agent,
)
from app.graph.subgraphs.testing import repair_planning
from app.workspace.code_changes import CapturedWorkspaceChanges


class IntegrationRepairFlowTests(unittest.TestCase):
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

    def test_failed_command_generates_repair_task(self) -> None:
        """验证真实命令失败会交给 RepairPlanner 生成可执行修复任务。"""

        revision_request = {
            "id": "revision:frontend_build",
            "evidence": "TypeScript compilation failed.",
            "failed_check": {"id": "frontend_build", "failure_category": "compile_error"},
        }
        repair_task = {
            "id": "repair:frontend_build:frontend",
            "owner": "frontend",
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

        self.assertEqual(result["integration_next_action"], "small_task_repair")
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

        self.assertEqual(result["integration_next_action"], "small_task_repair")
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
