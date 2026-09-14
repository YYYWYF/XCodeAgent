"""验证 Application owner admission 与 Pending interaction 的后端边界。"""

from __future__ import annotations

import tempfile
import unittest

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    PendingInteractionType,
    WorkbenchExecutionStatus,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    end_workbench_execution,
    start_workbench_execution,
    update_workbench_execution,
    write_application_lifecycle,
)
from app.workspace.task_documents import write_pending_build_task_plan_atomic


def write_ready_lifecycle(workspace: str) -> None:
    """写入可启动工作台 execution 的最小生命周期。"""

    state = create_application_lifecycle(application_id="app-owner", application_name="Owner")
    state = state.model_copy(
        update={
            "initialization": state.initialization.model_copy(
                update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }
            )
        }
    )
    write_application_lifecycle(workspace, state)


class ApplicationOwnerAdmissionTests(unittest.TestCase):
    """验证带 session 身份的 Workflow 入口不会绕过应用级 owner。"""

    def test_active_owner_rejects_conflicting_session_until_terminal(self) -> None:
        """活动 execution 期间拒绝其它会话和同会话的无令牌新 mutation。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            start_workbench_execution(
                workspace,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-owner",
                run_id="run-owner",
                phase="build",
                owner_session_id="session-owner",
            )

            with self.assertRaises(ApplicationLifecycleConflictError):
                start_workbench_execution(
                    workspace,
                    scope="page",
                    target_id="inventory",
                    page_id="inventory",
                    thread_id="thread-other",
                    run_id="run-other",
                    phase="build",
                    owner_session_id="session-other",
                )
            with self.assertRaises(ApplicationLifecycleConflictError):
                start_workbench_execution(
                    workspace,
                    scope="page",
                    target_id="orders",
                    page_id="orders",
                    thread_id="thread-owner",
                    run_id="run-owner-retry",
                    phase="build",
                    owner_session_id="session-owner",
                )

            end_workbench_execution(workspace, run_id="run-owner")
            released = start_workbench_execution(
                workspace,
                scope="page",
                target_id="inventory",
                page_id="inventory",
                thread_id="thread-other",
                run_id="run-other",
                phase="build",
                owner_session_id="session-other",
            )

            self.assertIn("run-other", released.active_executions)

    def test_terminal_execution_does_not_keep_application_locked(self) -> None:
        """failed、stopped、completed execution 都不能继续形成 Application lock。"""

        terminal_statuses = (
            WorkbenchExecutionStatus.FAILED,
            WorkbenchExecutionStatus.STOPPED,
            WorkbenchExecutionStatus.COMPLETED,
        )
        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            for index, status in enumerate(terminal_statuses):
                owner_run_id = f"run-terminal-{index}"
                next_run_id = f"run-next-{index}"
                start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-owner",
                    run_id=owner_run_id,
                    phase="unit_test",
                    owner_session_id="session-owner",
                )
                update_workbench_execution(
                    workspace,
                    run_id=owner_run_id,
                    phase="unit_test",
                    status=status,
                )

                next_state = start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-next",
                    run_id=next_run_id,
                    phase="unit_test",
                    owner_session_id="session-next",
                )
                self.assertIn(next_run_id, next_state.active_executions)
                end_workbench_execution(workspace, run_id=next_run_id)

    def test_pending_plan_requires_owner_and_exact_replacement(self) -> None:
        """Pending interaction 只允许 owner 携带原 Workflow run 精确接续。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-owner",
                run_id="run-pending",
                phase="prepare_build_tasks",
                owner_session_id="session-owner",
            )
            waiting = update_workbench_execution(
                workspace,
                run_id="run-pending",
                phase="prepare_build_tasks",
                status=WorkbenchExecutionStatus.AWAITING_USER,
                pending_type=PendingInteractionType.TASK_PLAN_CONFIRMATION,
                pending_payload={"mode": "build_task_plan_confirmation"},
            )
            pending = waiting.active_executions["run-pending"].pending_interaction
            self.assertIsNotNone(pending)
            write_pending_build_task_plan_atomic(
                {"workspace": workspace},
                {
                    "schema_version": "build-dag.v3",
                    "status": "ready",
                    "task_graph": {"validation": {"is_valid": True, "errors": []}},
                },
                owner_session_id="session-owner",
                planning_run_id="planning-pending",
                workflow_run_id="run-pending",
                base_confirmed_plan_digest=None,
                input_fingerprint="a" * 64,
                build_execution_scope={"type": "application", "targetId": "application"},
                created_at="2026-09-14T00:00:00Z",
            )

            with self.assertRaises(ApplicationLifecycleConflictError):
                start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-other",
                    run_id="run-other",
                    phase="build",
                    owner_session_id="session-other",
                )
            with self.assertRaises(ApplicationLifecycleConflictError):
                start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-owner",
                    run_id="run-owner-without-replacement",
                    phase="build",
                    owner_session_id="session-owner",
                )

            replaced = start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-owner",
                run_id="run-confirmed",
                phase="build",
                owner_session_id="session-owner",
                replaces_run_id="run-pending",
            )
            self.assertNotIn("run-pending", replaced.active_executions)
            self.assertIn("run-confirmed", replaced.active_executions)

