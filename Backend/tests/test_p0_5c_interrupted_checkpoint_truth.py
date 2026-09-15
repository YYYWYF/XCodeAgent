"""P0.5-C INTERRUPTED Recovery 的 latest checkpoint authority 架构测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    NodeEntryBoundary,
    RecoveryActionKind,
    WorkflowReentryContextAuthority,
    WorkflowReentryContextAuthorityKind,
    WorkflowReentryLifecycleAuthority,
    WorkflowReentryPlan,
    WorkflowReentryReason,
)
from app.services.execution_recovery_projection import _resolve_candidate
from app.services.workflow_reentry import (
    InterruptedTargetResolution,
    InterruptedTargetResolver,
)


class _HistoryGraph:
    """提供有序 committed checkpoint history 的最小 Graph double。"""

    def __init__(self, snapshots: list[object]) -> None:
        """保存从最新到最旧的 checkpoint snapshots。"""

        self.snapshots = snapshots

    async def aget_state_history(self, _config: dict[str, object]):
        """按 Graph history 的 newest-first 顺序返回 snapshots。"""

        for snapshot in self.snapshots:
            yield snapshot


class InterruptedCheckpointTruthTests(unittest.IsolatedAsyncioTestCase):
    """验证 INTERRUPTED 不跳过最新 checkpoint，也不调用旧恢复策略。"""

    def setUp(self) -> None:
        """创建隔离 source execution 与 workspace。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)
        now = datetime.now(timezone.utc)
        self.source = DurableExecutionRecord(
            run_id="interrupted-source",
            thread_id="interrupted-thread",
            owner_session_id="interrupted-session",
            workspace=str(self.workspace),
            execution_kind="workbench",
            workflow_scope="application",
            first_node="inspect_workspace",
            current_node="inspect_workspace",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )

    def tearDown(self) -> None:
        """释放隔离 workspace。"""

        self._temporary_workspace.cleanup()

    def _snapshot(
        self,
        *,
        checkpoint_id: str,
        next_nodes: tuple[str, ...],
        tasks: tuple[object, ...] = (),
    ) -> SimpleNamespace:
        """构造带 source ownership 与 root checkpoint identity 的 snapshot。"""

        return SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": self.source.thread_id,
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint_id,
                }
            },
            values={"active_run_id": self.source.run_id},
            next=next_nodes,
            tasks=tasks,
        )

    def _lineage(self) -> SimpleNamespace:
        """构造唯一 canonical lineage head 解析结果。"""

        return SimpleNamespace(
            head=self.source,
            state=SimpleNamespace(value="RECOVERABLE_HEAD"),
        )

    async def test_latest_terminal_does_not_fallback_to_older_pending(self) -> None:
        """最新 terminal checkpoint 必须完成对账解释，不能回退旧 pending。"""

        graph = _HistoryGraph(
            [
                self._snapshot(checkpoint_id="latest-end", next_nodes=()),
                self._snapshot(checkpoint_id="older-node", next_nodes=("build",)),
            ]
        )
        with patch(
            "app.services.workflow_reentry.resolve_recovery_lineage_head",
            new=AsyncMock(return_value=self._lineage()),
        ):
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=graph,
            )

        self.assertEqual(resolution.kind, "completed")
        self.assertEqual(resolution.snapshot.config["configurable"]["checkpoint_id"], "latest-end")

    async def test_latest_pending_creates_interrupted_continue_plan(self) -> None:
        """最新唯一 successor 必须成为 INTERRUPTED_CONTINUE target。"""

        boundary = NodeEntryBoundary(
            boundary_id="boundary-build",
            source_run_id=self.source.run_id,
            thread_id=self.source.thread_id,
            target_node="build",
            checkpoint_id="latest-build",
            checkpoint_ns="",
            captured_at=datetime.now(timezone.utc),
        )
        graph = _HistoryGraph(
            [self._snapshot(checkpoint_id="latest-build", next_nodes=("build",))]
        )
        with (
            patch(
                "app.services.workflow_reentry.resolve_recovery_lineage_head",
                new=AsyncMock(return_value=self._lineage()),
            ),
            patch(
                "app.services.workflow_reentry._persist_boundary",
                new=AsyncMock(return_value=boundary),
            ),
        ):
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=graph,
            )

        self.assertEqual(resolution.kind, "continue")
        assert resolution.reentry_plan is not None
        self.assertEqual(
            resolution.reentry_plan.reason,
            WorkflowReentryReason.INTERRUPTED_CONTINUE,
        )
        self.assertEqual(resolution.reentry_plan.target_node, "build")
        self.assertEqual(
            resolution.reentry_plan.context_authority.kind,
            WorkflowReentryContextAuthorityKind.CHECKPOINT,
        )

    async def test_latest_native_interrupt_reconciles_to_awaiting_user(self) -> None:
        """最新 native interrupt 必须阻止重跑产生交互的 Node。"""

        graph = _HistoryGraph(
            [
                self._snapshot(
                    checkpoint_id="latest-interrupt",
                    next_nodes=("confirm",),
                    tasks=(SimpleNamespace(interrupts=("approval",)),),
                )
            ]
        )
        with patch(
            "app.services.workflow_reentry.resolve_recovery_lineage_head",
            new=AsyncMock(return_value=self._lineage()),
        ):
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=graph,
            )

        self.assertEqual(resolution.kind, "awaiting_user")
        self.assertIsNone(resolution.reentry_plan)

    async def test_latest_ambiguous_successors_fail_closed(self) -> None:
        """最新并行 successor 在当前 runtime 不可唯一重入时必须阻断。"""

        graph = _HistoryGraph(
            [self._snapshot(checkpoint_id="latest-parallel", next_nodes=("a", "b"))]
        )
        with patch(
            "app.services.workflow_reentry.resolve_recovery_lineage_head",
            new=AsyncMock(return_value=self._lineage()),
        ):
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=graph,
            )

        self.assertEqual(resolution.kind, "needs_attention")
        self.assertEqual(resolution.reason_code, "INTERRUPTED_CHECKPOINT_AMBIGUOUS")

    async def test_interrupted_projection_skips_legacy_replay_policy(self) -> None:
        """INTERRUPTED projection 只能使用 resolver 与 continue action。"""

        plan = WorkflowReentryPlan(
            reason=WorkflowReentryReason.INTERRUPTED_CONTINUE,
            execution_kind="workbench",
            target_node="build",
            thread_id=self.source.thread_id,
            source_run_id=self.source.run_id,
            lineage_parent_run_id=self.source.run_id,
            context_authority=WorkflowReentryContextAuthority(
                kind=WorkflowReentryContextAuthorityKind.CHECKPOINT,
                boundary_id="boundary-build",
                source_run_id=self.source.run_id,
                thread_id=self.source.thread_id,
                target_node="build",
                checkpoint_id="latest-build",
                checkpoint_ns="",
            ),
            lifecycle_authority=WorkflowReentryLifecycleAuthority(
                owner_run_id=self.source.run_id,
                revision=None,
            ),
        )
        result = InterruptedTargetResolution(
            kind="continue",
            snapshot=None,
            reentry_plan=plan,
            reason_code="INTERRUPTED_CONTINUE_READY",
            reason="ready",
        )
        with (
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.execution_recovery_projection.InterruptedTargetResolver.resolve",
                new=AsyncMock(return_value=result),
            ),
            patch(
                "app.services.execution_recovery_projection.prepare_continue",
                new=AsyncMock(side_effect=AssertionError("INTERRUPTED used legacy replay")),
            ),
        ):
            candidate = await _resolve_candidate(self.source)

        assert candidate is not None
        assert candidate.recovery_action_plan is not None
        assert candidate.recovery_action_plan.primary_action is not None
        self.assertEqual(
            candidate.recovery_action_plan.primary_action.kind,
            RecoveryActionKind.CONTINUE_CHECKPOINT,
        )


if __name__ == "__main__":
    unittest.main()
