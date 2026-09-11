from __future__ import annotations

from datetime import UTC, datetime
import tempfile
import unittest

from app.domain.application_lifecycle import (
    PendingInteraction,
    PendingInteractionType,
    WorkbenchExecution,
    WorkbenchExecutionStatus,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.workspace.planning_run_documents import write_planning_run_atomic
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    write_build_task_plan_json,
    write_pending_build_task_plan_atomic,
)
from tests.planning_run_fixtures import run
from tests.test_pending_build_task_plan_documents import _validated_plan


class PlanningRefreshRecoveryTests(unittest.TestCase):
    """验证刷新只恢复尚未被终态事实压制的唯一 PendingPlan。"""

    def setUp(self) -> None:
        """为每例创建独立工作区和一致的 PlanningRun 身份。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        self.state = {"workspace": self.workspace}
        self.planning = run().model_copy(
            update={
                "planning_run_id": "planning-refresh-run",
                "workflow_run_id": "workflow-refresh-run",
                "thread_id": "thread-refresh-run",
                "input_fingerprint": "b" * 64,
                "base_confirmed_plan_digest": None,
            }
        )

    def _write_planning(self) -> None:
        """写入当前 active PlanningRun 的严格轻量快照。"""

        write_planning_run_atomic(self.state, self.planning)

    def _write_pending(self) -> dict:
        """写入与当前 PlanningRun 身份一致的合法 PendingPlan。"""

        write_pending_build_task_plan_atomic(
            self.state,
            _validated_plan(),
            owner_session_id="session-refresh-owner",
            planning_run_id=self.planning.planning_run_id,
            workflow_run_id=self.planning.workflow_run_id,
            base_confirmed_plan_digest=None,
            input_fingerprint=self.planning.input_fingerprint,
            build_execution_scope=self.planning.build_execution_scope,
            created_at=self.planning.updated_at,
        )
        pending = load_pending_build_task_plan(self.state)
        assert pending is not None
        return pending

    def _resolve(self) -> dict:
        """调用按 Pending 与终态身份解析的公共刷新解析器。"""

        return resolve_planning_refresh_state(self.workspace)

    def _write_confirmed_formal(self, planning_run_id: str, draft_digest: str) -> None:
        """写入带精确 confirmed_from 的 Formal residue。"""

        write_build_task_plan_json(
            self.state,
            {
                "schema_version": "build-dag.v3",
                "status": "ready",
                "confirmation_status": "confirmed",
                "confirmed_from": {
                    "planning_run_id": planning_run_id,
                    "draft_digest": draft_digest,
                },
                "task_graph": {"validation": {"is_valid": True, "errors": []}},
            },
        )

    def _write_abandoned_marker(self, planning_run_id: str, draft_digest: str) -> None:
        """写入与 Pending 身份精确匹配的 authoritative Abandon residue。"""

        lifecycle = create_application_lifecycle(
            application_id="app-refresh",
            application_name="Planning refresh",
        ).model_copy(
            update={
                "extensions": {
                    "planningResultLifecycle": {
                        "schemaVersion": "planning-result-lifecycle.v1",
                        "status": "abandoned",
                        "planningRunId": planning_run_id,
                        "draftDigest": draft_digest,
                    }
                }
            }
        )
        write_application_lifecycle(self.workspace, lifecycle)

    def test_case_a_pending_plan_is_authoritative(self) -> None:
        """存在 PendingPlan 时返回确认态，并直接使用其中冻结的身份。"""

        pending = self._write_pending()

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "pending_plan")
        self.assertEqual(recovered["status"], "awaiting_confirmation")
        self.assertEqual(recovered["planningRunId"], self.planning.planning_run_id)
        self.assertEqual(recovered["workflowRunId"], self.planning.workflow_run_id)
        self.assertNotIn("threadId", recovered)
        self.assertEqual(recovered["ownerSessionId"], "session-refresh-owner")
        self.assertEqual(
            recovered["buildExecutionScope"],
            self.planning.build_execution_scope,
        )
        self.assertEqual(
            recovered["confirmation"]["draftIdentity"]["ownerSessionId"],
            "session-refresh-owner",
        )
        self.assertEqual(
            recovered["draftDigest"],
            pending["draft_identity"]["draft_digest"],
        )
        self.assertEqual(
            recovered["confirmation"]["taskPlan"]["confirmationStatus"],
            "pending",
        )

    def test_case_b_missing_pending_is_idle_even_with_active_planning_run(self) -> None:
        """没有 PendingPlan 时，旧 PlanningRun 不能伪造待确认状态。"""

        self._write_planning()

        recovered = resolve_planning_refresh_state(
            self.workspace,
        )

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")

    def test_case_c_legacy_task_plan_confirmation_is_ignored(self) -> None:
        """没有 PendingPlan 时，application-lifecycle 的旧确认交互也必须返回 idle。"""

        timestamp = datetime(2026, 9, 11, tzinfo=UTC)
        lifecycle = create_application_lifecycle(
            application_id="app-refresh",
            application_name="Planning refresh",
        )
        execution = WorkbenchExecution(
            scope="page",
            targetId="orders",
            threadId="legacy-thread",
            runId="legacy-workflow-run",
            phase="prepare_build_tasks",
            status=WorkbenchExecutionStatus.AWAITING_USER,
            pendingInteraction=PendingInteraction(
                id="legacy-task-plan-confirmation",
                type=PendingInteractionType.TASK_PLAN_CONFIRMATION,
                basedOnRevision=lifecycle.revision,
                payload={"mode": "build_task_plan_confirmation"},
                createdAt=timestamp,
            ),
            startedAt=timestamp,
            updatedAt=timestamp,
        )
        lifecycle = lifecycle.model_copy(
            update={
                "active_run_id": execution.run_id,
                "active_executions": {execution.run_id: execution},
            }
        )
        write_application_lifecycle(self.workspace, lifecycle)
        persisted = load_application_lifecycle(self.workspace)
        self.assertIsNotNone(persisted)

        recovered = resolve_planning_refresh_state(
            self.workspace,
        )

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")

    def test_case_b_confirmed_pending_residue_is_not_actionable(self) -> None:
        """Formal 已精确确认当前 Pending 时，重启恢复不得再次展示确认。"""

        pending = self._write_pending()
        identity = pending["draft_identity"]
        self._write_confirmed_formal(
            identity["planning_run_id"],
            identity["draft_digest"],
        )

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")
        self.assertNotIn("confirmation", recovered)

    def test_case_c_abandoned_pending_residue_is_not_actionable(self) -> None:
        """Abandon tombstone 已精确终结当前 Pending 时，重启恢复不得复活它。"""

        pending = self._write_pending()
        identity = pending["draft_identity"]
        self._write_abandoned_marker(
            identity["planning_run_id"],
            identity["draft_digest"],
        )

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")
        self.assertNotIn("confirmation", recovered)

    def test_case_d_different_pending_identity_survives_formal_residue(self) -> None:
        """Formal 终结 A 不能使精确身份不同的当前 Pending B 失效。"""

        pending = self._write_pending()
        identity = pending["draft_identity"]
        self._write_confirmed_formal("planning-formal-a", "a" * 64)
        self._write_abandoned_marker("planning-abandoned-a", "b" * 64)

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "pending_plan")
        self.assertEqual(recovered["status"], "awaiting_confirmation")
        self.assertEqual(recovered["planningRunId"], identity["planning_run_id"])
        self.assertEqual(recovered["draftDigest"], identity["draft_digest"])


if __name__ == "__main__":
    unittest.main()
