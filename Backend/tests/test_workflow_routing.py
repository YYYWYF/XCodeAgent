from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.workflow import (
    route_acceptance,
    route_build_result,
    route_database_context_inspection,
    route_detail_confirmation,
    route_prepare_build_tasks,
    route_project_planning,
    route_test_validation,
    route_workspace_inspection,
    route_workflow_start,
)


class WorkflowRoutingTests(unittest.TestCase):
    def test_workflow_start_defaults_to_detail_confirmation(self) -> None:
        self.assertEqual(
            route_workflow_start({}),
            "detail_confirmation",
        )

    def test_workflow_start_can_resume_from_detail_confirmation(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "detail_confirmation"}),
            "detail_confirmation",
        )

    def test_workflow_start_can_resume_from_project_planning(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "project_planning"}),
            "project_planning",
        )

    def test_project_planning_waits_for_confirmation(self) -> None:
        self.assertEqual(
            route_project_planning({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_project_planning_continues_to_detail_confirmation(self) -> None:
        self.assertEqual(
            route_project_planning({"status": "completed"}),
            "detail_confirmation",
        )

    def test_workflow_start_can_resume_from_prepare_build_tasks(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "prepare_build_tasks"}),
            "prepare_build_tasks",
        )

    def test_workflow_start_can_resume_from_database_context(self) -> None:
        """支持从数据库上下文检查节点恢复调试或用户重试。"""

        self.assertEqual(
            route_workflow_start({"resume_from": "inspect_database_context"}),
            "inspect_database_context",
        )

    def test_detail_confirmation_waits_for_user_input(self) -> None:
        self.assertEqual(
            route_detail_confirmation({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_detail_confirmation_continues_when_page_spec_is_confirmed(self) -> None:
        self.assertEqual(
            route_detail_confirmation({"status": "completed"}),
            "inspect_workspace",
        )

    def test_workspace_inspection_routes_to_database_context_when_required(self) -> None:
        """接口 data_origin 来源于数据库时，工作区检查后进入数据库上下文节点。"""

        with patch("app.graph.workflow._latest_compact_project_plan", return_value={}), patch(
            "app.graph.workflow._workspace_snapshot_from_state",
            return_value={},
        ), patch("app.graph.workflow._build_task_plan_for_context", return_value={}), patch(
            "app.graph.workflow._resolve_build_context",
            return_value={},
        ), patch(
            "app.graph.workflow.database_context_requirement",
            return_value={"required": True, "status": "required"},
        ):
            self.assertEqual(route_workspace_inspection({}), "inspect_database_context")

    def test_workspace_inspection_skips_database_context_for_external_api(self) -> None:
        """外部 API 来源不展示数据库上下文检查节点。"""

        with patch("app.graph.workflow._latest_compact_project_plan", return_value={}), patch(
            "app.graph.workflow._workspace_snapshot_from_state",
            return_value={},
        ), patch("app.graph.workflow._build_task_plan_for_context", return_value={}), patch(
            "app.graph.workflow._resolve_build_context",
            return_value={},
        ), patch(
            "app.graph.workflow.database_context_requirement",
            return_value={"required": False, "status": "not_required"},
        ):
            self.assertEqual(route_workspace_inspection({}), "prepare_build_tasks")

    def test_database_context_inspection_waits_for_user_input(self) -> None:
        """数据库上下文检查阻断时不会进入任务 DAG 生成。"""

        self.assertEqual(
            route_database_context_inspection({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_prepare_build_tasks_waits_for_project_plan_confirmation(self) -> None:
        self.assertEqual(
            route_prepare_build_tasks({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_prepare_build_tasks_continues_to_build_after_confirmation(self) -> None:
        self.assertEqual(
            route_prepare_build_tasks({"status": "completed"}),
            "build",
        )

    def test_integration_test_passes_to_launch_project(self) -> None:
        self.assertEqual(
            route_test_validation({"quality_gate_passed": True}),
            "launch_project",
        )

    def test_build_only_enters_testing_after_complete_summary(self) -> None:
        """构建切片完整成功后才允许进入集成测试。"""

        self.assertEqual(
            route_build_result({"build_summary": {"status": "completed"}}),
            "integration_test",
        )

    def test_build_confirmation_stops_for_user_input(self) -> None:
        """构建需要扩大范围时必须停留等待用户确认。"""

        self.assertEqual(
            route_build_result({"build_summary": {"status": "requires_confirmation"}}),
            "await_user_input",
        )

    def test_incomplete_build_routes_to_failure_instead_of_testing(self) -> None:
        """待执行、阻塞或终止失败的构建不得伪装成可测试状态。"""

        for status in ("in_progress", "needs_repair", "failed"):
            with self.subTest(status=status):
                self.assertEqual(
                    route_build_result({"build_summary": {"status": status}}),
                    "handle_failure",
                )

    def test_integration_test_repair_plan_enters_small_task_agent(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "small_task_repair",
                }
            ),
            "small_task_repair",
        )

    def test_integration_test_confirmation_waits_for_user(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "await_user_input",
                }
            ),
            "await_user_input",
        )

    def test_integration_test_terminal_failure_routes_to_failure(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "handle_failure",
                }
            ),
            "handle_failure",
        )

    def test_acceptance_requires_structured_accepted_state(self) -> None:
        """最终完成节点只能由结构化验收通过状态解锁。"""

        self.assertEqual(route_acceptance({"accepted": True}), "finalize_project")
        self.assertEqual(route_acceptance({"accepted": False}), "await_user_input")


if __name__ == "__main__":
    unittest.main()
