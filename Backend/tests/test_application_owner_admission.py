"""验证 DAG Planning owner admission 与 Pending interaction 的后端边界。"""

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
from app.workspace.task_documents import (
    write_build_task_plan_json,
    write_pending_build_task_plan_atomic,
)


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
    """验证带 session 身份的 Workflow 入口不会绕过 DAG Planning owner。"""

    def test_active_dag_owner_rejects_conflicting_session_until_terminal(self) -> None:
        """DAG generation 期间拒绝其它会话和同会话的无令牌新 mutation。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            start_workbench_execution(
                workspace,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-owner",
                run_id="run-owner",
                phase="prepare_build_tasks",
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
                    phase="prepare_build_tasks",
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
                    phase="prepare_build_tasks",
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
                phase="prepare_build_tasks",
                owner_session_id="session-other",
            )

            self.assertIn("run-other", released.active_executions)

    def test_non_dag_execution_does_not_reject_dag_generation(self) -> None:
        """API Design、Unit Test、Review、Acceptance 和普通 Build 不形成 DAG lock。"""

        scenarios = (
            (
                "api_design_readiness_gate",
                WorkbenchExecutionStatus.AWAITING_USER,
                PendingInteractionType.API_DESIGN,
            ),
            ("unit_test", WorkbenchExecutionStatus.RUNNING, None),
            (
                "unit_test",
                WorkbenchExecutionStatus.AWAITING_USER,
                PendingInteractionType.UNIT_TEST_CONFIRMATION,
            ),
            (
                "prepare_build_tasks",
                WorkbenchExecutionStatus.AWAITING_USER,
                PendingInteractionType.TASK_PLAN_CONFIRMATION,
            ),
            (
                "review_phase_confirmation",
                WorkbenchExecutionStatus.AWAITING_USER,
                PendingInteractionType.REVIEW_PHASE_CONFIRMATION,
            ),
            (
                "acceptance_phase_confirmation",
                WorkbenchExecutionStatus.AWAITING_USER,
                PendingInteractionType.ACCEPTANCE_PHASE_CONFIRMATION,
            ),
            ("build", WorkbenchExecutionStatus.RUNNING, None),
        )
        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            for index, (phase, status, pending_type) in enumerate(scenarios):
                source_run_id = f"run-non-dag-{index}"
                candidate_run_id = f"run-dag-{index}"
                start_workbench_execution(
                    workspace,
                    scope="page",
                    target_id=f"source-{index}",
                    page_id=f"source-{index}",
                    thread_id="thread-source",
                    run_id=source_run_id,
                    phase=phase,
                    owner_session_id="session-source",
                )
                if status != WorkbenchExecutionStatus.RUNNING:
                    update_workbench_execution(
                        workspace,
                        run_id=source_run_id,
                        phase=phase,
                        status=status,
                        pending_type=pending_type,
                        pending_payload={"mode": phase},
                    )

                admitted = start_workbench_execution(
                    workspace,
                    scope="page",
                    target_id=f"candidate-{index}",
                    page_id=f"candidate-{index}",
                    thread_id="thread-candidate",
                    run_id=candidate_run_id,
                    phase="prepare_build_tasks",
                    owner_session_id="session-candidate",
                )
                self.assertIn(candidate_run_id, admitted.active_executions)
                end_workbench_execution(workspace, run_id=candidate_run_id)
                end_workbench_execution(workspace, run_id=source_run_id)

    def test_regenerate_window_keeps_dag_lock_without_pending_file(self) -> None:
        """Regenerate 消费旧 Pending 后到新 Pending 写入前仍由 active DAG execution 持锁。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-owner",
                run_id="run-old",
                phase="prepare_build_tasks",
                owner_session_id="session-owner",
            )
            regenerated = start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-owner",
                run_id="run-regenerate",
                phase="prepare_build_tasks",
                owner_session_id="session-owner",
                replaces_run_id="run-old",
            )
            self.assertNotIn("run-old", regenerated.active_executions)
            self.assertIn("run-regenerate", regenerated.active_executions)

            with self.assertRaises(ApplicationLifecycleConflictError):
                start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-other",
                    run_id="run-other",
                    phase="prepare_build_tasks",
                    owner_session_id="session-other",
                )

    def test_formal_plan_without_pending_or_active_dag_allows_new_generation(self) -> None:
        """仅存在正式 build-task-plan 时，不应凭历史 Formal 产生 DAG lock。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_ready_lifecycle(workspace)
            write_build_task_plan_json(
                {"workspace": workspace},
                {
                    "version": "1.0.0",
                    "schema_version": "build-dag.v4",
                    "status": "ready",
                    "unit_graph": {
                        "schema_version": "build-unit-graph.v3",
                        "nodes": [],
                        "edges": [],
                        "validation": {"is_valid": True, "errors": []},
                    },
                    "confirmation_status": "confirmed",
                    "confirmed_at": "2026-09-14T00:00:00Z",
                    "task_registry": {},
                    "task_graph": {
                        "schema_version": "build-task-graph.v3",
                        "nodes": [],
                        "edges": [],
                        "topological_order": [],
                        "validation": {"is_valid": True, "errors": []},
                    },
                },
            )
            admitted = start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-new",
                run_id="run-new",
                phase="prepare_build_tasks",
                owner_session_id="session-new",
            )
            self.assertIn("run-new", admitted.active_executions)

    def test_terminal_dag_execution_does_not_keep_dag_locked(self) -> None:
        """prepare_build_tasks 的 failed、stopped、completed 都不能继续形成 DAG lock。"""

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
                    phase="prepare_build_tasks",
                    owner_session_id="session-owner",
                )
                update_workbench_execution(
                    workspace,
                    run_id=owner_run_id,
                    phase="prepare_build_tasks",
                    status=status,
                )

                next_state = start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-next",
                    run_id=next_run_id,
                    phase="prepare_build_tasks",
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
                    "schema_version": "build-dag.v4",
                    "status": "ready",
                    "unit_graph": {
                        "schema_version": "build-unit-graph.v3",
                        "nodes": [],
                        "edges": [],
                        "validation": {"is_valid": True, "errors": []},
                    },
                    "task_graph": {"validation": {"is_valid": True, "errors": []}},
                },
                owner_session_id="session-owner",
                planning_run_id="planning-pending",
                workflow_run_id="run-pending",
                base_confirmed_plan_digest=None,
                input_fingerprint="a" * 64,
                build_execution_scope={"type": "application", "targetId": "application"},
                created_at="2026-09-14T00:00:00Z",
                planning_provenance={
                    "schema_version": "planning-provenance.v2",
                    "review_task_ids": [],
                    "platform_task_ids": [],
                    "new_task_ids": [],
                    "reused_task_ids": [],
                },
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
