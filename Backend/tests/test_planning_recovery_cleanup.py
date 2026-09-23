"""验证 Planning Recovery 在 Workflow、Session 和 Application 生命周期中的收口。"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    WorkbenchExecutionStatus,
)
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle, fail_workflow_lifecycle
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    end_workbench_execution,
    start_workbench_execution,
    update_workbench_execution,
    write_application_lifecycle,
)
from app.services.build_task_plan_lifecycle import release_session_owned_pending_build_task_plan
from app.services.planning_recovery_contracts import (
    build_planning_recovery_snapshot,
    planning_recovery_snapshot_digest,
    PlanningRecoverySnapshot,
)
from app.workspace.planning_recovery_documents import (
    load_planning_recovery,
    write_planning_recovery_atomic,
)
from tests.planning_run_fixtures import AT, ready, run
from tests.test_planning_recovery import _infrastructure_issue


def _ready_lifecycle() -> object:
    """构造允许启动工作台 execution 的最小 lifecycle。"""

    state = create_application_lifecycle(application_id="app-cleanup", application_name="Cleanup")
    return state.model_copy(update={
        "initialization": state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            "status": ApplicationLifecycleStatus.COMPLETED,
        })
    })


def _write_recovery(workspace: str, workflow_run_id: str) -> None:
    """写入与指定 Workflow execution 精确绑定的合法 Recovery。"""

    # 复用状态转换 fixture，确保 Snapshot 仍来自真实 candidate_ready Unit。
    from app.services import planning_run as transitions

    generating = run()
    failed = transitions.fail(
        ready(transitions.begin_generation(generating, at=AT)),
        _infrastructure_issue(),
        at=AT,
    )
    snapshot = build_planning_recovery_snapshot(failed, owner_session_id="session-owner")
    assert snapshot is not None
    payload = snapshot.model_dump(mode="json")
    payload["source_workflow_run_id"] = workflow_run_id
    payload["snapshot_digest"] = planning_recovery_snapshot_digest(payload)
    write_planning_recovery_atomic(
        {"workspace": workspace},
        PlanningRecoverySnapshot.model_validate(payload),
    )


class PlanningRecoveryLifecycleCleanupTests(unittest.TestCase):
    """验证所有 lifecycle cleanup 都按 exact execution/session 边界工作。"""

    def test_end_failed_execution_deletes_exact_recovery_and_keeps_end_success(self) -> None:
        """明确 End Plan 后 source execution 不再可 Retry，Recovery 随之删除。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_application_lifecycle(workspace, _ready_lifecycle())
            start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-end",
                run_id="run-end",
                phase="prepare_build_tasks",
                owner_session_id="session-owner",
            )
            update_workbench_execution(
                workspace,
                run_id="run-end",
                phase="prepare_build_tasks",
                status=WorkbenchExecutionStatus.FAILED,
            )
            _write_recovery(workspace, "run-end")

            ended = end_workbench_execution(workspace, run_id="run-end")

            self.assertEqual(ended.active_executions, {})
            self.assertIsNone(load_planning_recovery({"workspace": workspace}, "run-end"))

    def test_end_cleanup_failure_does_not_change_lifecycle_result(self) -> None:
        """Recovery delete IO 失败不能把已完成的 End Plan 改成失败。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_application_lifecycle(workspace, _ready_lifecycle())
            start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-end",
                run_id="run-end",
                phase="prepare_build_tasks",
            )
            update_workbench_execution(
                workspace,
                run_id="run-end",
                phase="prepare_build_tasks",
                status=WorkbenchExecutionStatus.FAILED,
            )
            with patch(
                "app.workspace.planning_recovery_documents.delete_planning_recovery",
                side_effect=OSError("read-only recovery directory"),
            ):
                ended = end_workbench_execution(workspace, run_id="run-end")

            self.assertEqual(ended.active_executions, {})

    def test_session_cleanup_only_deletes_failed_recovery_owned_by_session(self) -> None:
        """Session 删除不能扫描并删除其它 Session 的 Recovery。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_application_lifecycle(workspace, _ready_lifecycle())
            for run_id, owner in (("run-owner", "session-owner"), ("run-other", "session-other")):
                start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id=f"thread-{run_id}",
                    run_id=run_id,
                    phase="prepare_build_tasks",
                    owner_session_id=owner,
                )
                update_workbench_execution(
                    workspace,
                    run_id=run_id,
                    phase="prepare_build_tasks",
                    status=WorkbenchExecutionStatus.FAILED,
                )
                _write_recovery(workspace, run_id)

            released = release_session_owned_pending_build_task_plan(
                {"workspace": workspace},
                owner_session_id="session-owner",
            )

            self.assertFalse(released)
            self.assertIsNone(load_planning_recovery({"workspace": workspace}, "run-owner"))
            self.assertIsNotNone(load_planning_recovery({"workspace": workspace}, "run-other"))

    def test_duplicate_retry_is_rejected_by_existing_execution_replacement(self) -> None:
        """R1 被 R2 接管后，第二次仍以 R1 resume 必须 fail closed。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_application_lifecycle(workspace, _ready_lifecycle())
            inputs = {
                "workspace": workspace,
                "workflow_action": "retry_failed_tasks",
                "resume_values": {
                    "owner_session_id": "session-owner",
                    "build_execution_scope": {"type": "application", "targetId": "application"},
                },
            }
            begin_workflow_lifecycle(
                inputs,
                thread_id="thread-owner",
                run_id="run-r1",
                phase="prepare_build_tasks",
            )
            fail_workflow_lifecycle(
                workspace,
                run_id="run-r1",
                phase="prepare_build_tasks",
                error=RuntimeError("planning failed"),
            )
            retry_inputs = {
                **inputs,
                "resume_values": {
                    **inputs["resume_values"],
                    "resume_execution_run_id": "run-r1",
                },
            }
            begin_workflow_lifecycle(
                retry_inputs,
                thread_id="thread-owner",
                run_id="run-r2",
                phase="prepare_build_tasks",
            )

            with self.assertRaises(ApplicationLifecycleConflictError):
                begin_workflow_lifecycle(
                    retry_inputs,
                    thread_id="thread-owner",
                    run_id="run-r3",
                    phase="prepare_build_tasks",
                )


if __name__ == "__main__":
    unittest.main()
