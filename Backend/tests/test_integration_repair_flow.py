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
            )

        self.assertEqual(result["decision"], "repair")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["owner"], "data_source")

    def test_dirty_build_is_not_classified_as_project_plan_mismatch(self) -> None:
        """验证仅构建未完成时不会误触发 ProjectPlan 契约修订。"""

        with patch(
            "app.graph.subgraphs.testing.validate_api_contract_consistency",
            return_value=[],
        ):
            result = api_contract_check(
                {"build_summary": {"failed": 1, "pending": 0}}
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
        self.assertEqual(result["repair_iteration"], 1)
        self.assertEqual(result["repair_task_plan"], repair_plan)
        self.assertEqual(result["repair_tasks"], [repair_task])
        self.assertEqual(
            planner.call_args.kwargs["revision_requests"],
            [revision_request],
        )


if __name__ == "__main__":
    unittest.main()
