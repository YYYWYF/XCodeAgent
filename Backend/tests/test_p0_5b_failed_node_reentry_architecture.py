"""P0.5-B FAILED Recovery 必须只依赖 Workflow Node Re-entry authority。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    RecoveryActionKind,
    RecoveryExecutionError,
    RecoveryIncidentStatus,
    WorkflowReentryContextAuthority,
    WorkflowReentryContextAuthorityKind,
    WorkflowReentryLifecycleAuthority,
    WorkflowReentryPlan,
    WorkflowReentryReason,
)
from app.protocols.execution_recovery import _resolve_action_source_run
from app.services.execution_recovery_action_planner import (
    plan_failed_node_reentry_action,
)
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_recovery_projection import _resolve_candidate


class FailedNodeReentryArchitectureTests(unittest.IsolatedAsyncioTestCase):
    """验证 FAILED discovery、projection 和 source lookup 不再依赖旧决策层。"""

    def setUp(self) -> None:
        """创建本组测试使用的隔离 workspace。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)
        self.source = self._source(
            run_id="failed-source",
            current_node="build",
            failure_code="MODEL_NOT_FOUND",
        )
        self.reentry_plan = self._reentry_plan()

    def tearDown(self) -> None:
        """释放隔离 workspace。"""

        self._temporary_workspace.cleanup()

    def _source(
        self,
        *,
        run_id: str,
        current_node: str,
        failure_code: str,
    ) -> DurableExecutionRecord:
        """构造包含异常证据但不包含恢复策略的 FAILED source。"""

        now = datetime.now(timezone.utc)
        return DurableExecutionRecord(
            run_id=run_id,
            thread_id="failed-thread",
            owner_session_id="failed-session",
            workspace=str(self.workspace),
            execution_kind="workbench",
            workflow_scope="application",
            first_node="build",
            current_node=current_node,
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.UNKNOWN,
                code=failure_code,
                operation=current_node,
                replay_compatible=False,
            ),
        )

    def _reentry_plan(
        self,
        source: DurableExecutionRecord | None = None,
    ) -> WorkflowReentryPlan:
        """构造由 FailureTargetResolver 产生的精确 root Node Entry authority。"""

        source = source or self.source
        return WorkflowReentryPlan(
            reason=WorkflowReentryReason.FAILURE_RETRY,
            execution_kind="workbench",
            target_node="build",
            thread_id=source.thread_id,
            source_run_id=source.run_id,
            lineage_parent_run_id=source.run_id,
            context_authority=WorkflowReentryContextAuthority(
                kind=WorkflowReentryContextAuthorityKind.CHECKPOINT,
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                target_node="build",
                checkpoint_id="checkpoint-build",
                checkpoint_ns="",
            ),
            lifecycle_authority=WorkflowReentryLifecycleAuthority(
                owner_run_id=self.source.run_id,
                revision=None,
            ),
        )

    async def test_failed_projection_uses_only_node_entry_authority(self) -> None:
        """FAILED projection 必须只调用 FailureTargetResolver 和薄 ActionPlan。"""

        with (
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.execution_recovery_projection.FailureTargetResolver.resolve",
                new=AsyncMock(return_value=self.reentry_plan),
            ),
        ):
            candidate = await _resolve_candidate(self.source)

        assert candidate is not None
        assert candidate.recovery_action_plan is not None
        self.assertEqual(candidate.availability, "ready")
        self.assertEqual(candidate.recovery_action_plan["reasonCode"], "FAILED_NODE_REENTRY_READY")
        self.assertEqual(
            candidate.recovery_action_plan["primaryAction"]["kind"],
            RecoveryActionKind.RETRY_FAILED_NODE.value,
        )

    async def test_missing_exact_node_entry_is_projected_as_needs_attention(self) -> None:
        """没有 exact Node Entry 时只能生成 NEEDS_ATTENTION，不能降级动作。"""

        with (
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.execution_recovery_projection.FailureTargetResolver.resolve",
                new=AsyncMock(
                    side_effect=RecoveryExecutionError(
                        "NODE_ENTRY_AUTHORITY_MISSING",
                        "missing exact predecessor",
                    )
                ),
            ),
        ):
            candidate = await _resolve_candidate(self.source)

        assert candidate is not None
        assert candidate.recovery_action_plan is not None
        self.assertEqual(candidate.availability, "blocked")
        self.assertFalse(candidate.can_continue)
        self.assertEqual(
            candidate.recovery_action_plan["status"],
            RecoveryIncidentStatus.NEEDS_ATTENTION.value,
        )
        self.assertIsNone(candidate.recovery_action_plan["primaryAction"])

    async def test_diagnostics_do_not_change_failed_node_eligibility(self) -> None:
        """不同 failure diagnostics 不能把 exact Node Entry 变成另一种恢复策略。"""

        for failure_code in ("MODEL_NOT_FOUND", "UPSTREAM_404", "UNKNOWN_FAILURE"):
            source = self._source(
                run_id=f"failed-{failure_code}",
                current_node="build",
                failure_code=failure_code,
            )
            action_plan = plan_failed_node_reentry_action(
                workspace=str(self.workspace),
                source=source,
                reentry_plan=self._reentry_plan(source),
            )
            self.assertEqual(action_plan.status, RecoveryIncidentStatus.RECOVERABLE)
            assert action_plan.primary_action is not None
            self.assertEqual(
                action_plan.primary_action.kind,
                RecoveryActionKind.RETRY_FAILED_NODE,
            )

    async def test_failed_execute_source_lookup_does_not_reenter_legacy_planner(self) -> None:
        """execute 的 Backend action lookup 对 FAILED 也必须复用 Node Re-entry。"""

        expected = plan_failed_node_reentry_action(
            workspace=str(self.workspace),
            source=self.source,
            reentry_plan=self.reentry_plan,
        )
        assert expected.primary_action is not None
        with (
            patch(
                "app.protocols.execution_recovery.list_recovery_projection_candidates",
                new=AsyncMock(return_value=[self.source]),
            ),
            patch(
                "app.protocols.execution_recovery.workflow_graph_for_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.protocols.execution_recovery.FailureTargetResolver.resolve",
                new=AsyncMock(return_value=self.reentry_plan),
            ),
        ):
            source_run_id = await _resolve_action_source_run(
                workspace=str(self.workspace),
                incident_id=expected.incident_id,
                action_id=expected.primary_action.action_id,
            )

        self.assertEqual(source_run_id, self.source.run_id)

    async def test_executor_rejects_non_actionable_source_statuses(self) -> None:
        """直接调用 checkpoint re-entry Executor 也必须拒绝任意非异常终态。"""

        for status in (
            DurableExecutionStatus.RUNNING,
            DurableExecutionStatus.COMPLETED,
            DurableExecutionStatus.AWAITING_USER,
        ):
            with self.subTest(status=status.value):
                source = self.source.model_copy(
                    update={"status": status, "failure": None}
                )
                with patch(
                    "app.services.execution_recovery_executor.get_execution",
                    new=AsyncMock(return_value=source),
                ):
                    with self.assertRaises(RecoveryExecutionError) as raised:
                        await prepare_native_recovery(
                            workspace=str(self.workspace),
                            source_run_id=source.run_id,
                            graph=object(),
                            reentry_plan=self._reentry_plan(source),
                        )

                self.assertEqual(
                    raised.exception.code,
                    "WORKFLOW_REENTRY_PLAN_INVALID",
                )


if __name__ == "__main__":
    unittest.main()
