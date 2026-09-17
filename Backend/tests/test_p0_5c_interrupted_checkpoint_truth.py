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
    ExecutionLeaseStatus,
    NodeEntryBoundary,
    RecoveryActionKind,
    WorkflowReentryContextAuthority,
    WorkflowReentryContextAuthorityKind,
    WorkflowReentryLifecycleAuthority,
    WorkflowReentryPlan,
    WorkflowReentryReason,
)
from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    WorkbenchExecutionStatus,
)
from app.persistence.execution_recovery import (
    get_execution,
    get_execution_lease,
    list_recovery_attempts_from_source,
    mark_execution_interrupted,
)
from app.services.execution_recovery_projection import _resolve_candidate
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)
from app.services.execution_recovery import observe_execution_started
from app.services.execution_recovery_reconciliation import (
    reconcile_interrupted_execution_state,
)
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
        state_status: str | None = None,
    ) -> SimpleNamespace:
        """构造带 source ownership 与 root checkpoint identity 的 snapshot。"""

        values = {"active_run_id": self.source.run_id}
        if state_status is not None:
            values["status"] = state_status
        return SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": self.source.thread_id,
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint_id,
                }
            },
            values=values,
            next=next_nodes,
            tasks=tasks,
        )

    def _lineage(self) -> SimpleNamespace:
        """构造唯一 canonical lineage head 解析结果。"""

        return SimpleNamespace(
            head=self.source,
            state=SimpleNamespace(value="RECOVERABLE_HEAD"),
        )

    def _write_workbench_lifecycle(self, run_id: str) -> None:
        """创建可进入 Workbench 的 lifecycle，供 crash reconciliation 旅程使用。"""

        lifecycle = create_application_lifecycle(
            application_id="interrupted-app",
            application_name="Interrupted App",
            initialization_thread_id=self.source.thread_id,
            active_run_id=run_id,
        ).model_copy(
            update={
                "initialization": ApplicationInitialization(
                    stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    status=ApplicationLifecycleStatus.COMPLETED,
                    threadId=self.source.thread_id,
                )
            }
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)

    async def test_c1_latest_terminal_does_not_fallback_to_older_pending(self) -> None:
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

        self.assertEqual(resolution.kind, "terminal")
        self.assertEqual(resolution.terminal_status, DurableExecutionStatus.COMPLETED)
        self.assertEqual(resolution.snapshot.config["configurable"]["checkpoint_id"], "latest-end")

    async def test_c1_terminal_state_status_is_preserved_for_durable_reconciliation(self) -> None:
        """Graph terminal 只能说明没有 successor，最终 Durable status 仍取 committed State。"""

        expected = {
            "failed": DurableExecutionStatus.FAILED,
            "requires_user_input": DurableExecutionStatus.AWAITING_USER,
            "cancelled": DurableExecutionStatus.CANCELLED,
            "stopped": DurableExecutionStatus.STOPPED,
        }
        for state_status, durable_status in expected.items():
            with self.subTest(state_status=state_status):
                graph = _HistoryGraph(
                    [
                        self._snapshot(
                            checkpoint_id=f"terminal-{state_status}",
                            next_nodes=(),
                            state_status=state_status,
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
                self.assertEqual(resolution.kind, "terminal")
                self.assertEqual(resolution.terminal_status, durable_status)
                self.assertNotEqual(resolution.terminal_status, DurableExecutionStatus.COMPLETED)

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
        self.assertEqual(
            resolution.terminal_status,
            DurableExecutionStatus.AWAITING_USER,
        )

    async def test_c4_terminal_reconciliation_closes_workbench_mirror_and_allows_new_run(
        self,
    ) -> None:
        """Graph terminal 提交后重启对账必须释放旧锁，并允许下一次 Workbench 运行。"""

        run_id = "terminal-workbench-source"
        next_run_id = "terminal-workbench-next"
        self._write_workbench_lifecycle(run_id)
        start_workbench_execution(
            self.workspace,
            scope="application",
            target_id="application",
            page_id=None,
            thread_id=self.source.thread_id,
            run_id=run_id,
            phase="finalize_project",
        )
        await observe_execution_started(
            workspace=str(self.workspace),
            project_id="interrupted-app",
            thread_id=self.source.thread_id,
            run_id=run_id,
            workflow_scope="application",
            first_node="inspect_workspace",
        )
        await mark_execution_interrupted(
            workspace=self.workspace,
            run_id=run_id,
            interrupted_at=datetime.now(timezone.utc),
        )
        source = await get_execution(self.workspace, run_id)
        self.assertIsNotNone(source)
        assert source is not None
        snapshot = SimpleNamespace(
            values={
                "active_run_id": run_id,
                "phase": "finalize_project",
                "status": "completed",
            },
            next=(),
            tasks=(),
        )
        reconciled = await reconcile_interrupted_execution_state(
            workspace=self.workspace,
            source=source,
            status=DurableExecutionStatus.COMPLETED,
            snapshot=snapshot,
        )

        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled.status, DurableExecutionStatus.COMPLETED)
        lifecycle = load_application_lifecycle(self.workspace)
        self.assertIsNotNone(lifecycle)
        assert lifecycle is not None
        self.assertNotIn(run_id, lifecycle.active_executions)
        self.assertIsNone(lifecycle.active_run_id)
        self.assertIsNone(lifecycle.resource_locks.application)
        lease = await get_execution_lease(self.workspace, run_id)
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)
        self.assertEqual(await list_recovery_attempts_from_source(self.workspace, run_id), [])

        started = start_workbench_execution(
            self.workspace,
            scope="application",
            target_id="application",
            page_id=None,
            thread_id=self.source.thread_id,
            run_id=next_run_id,
            phase="inspect_workspace",
        )
        self.assertIn(next_run_id, started.active_executions)

    async def test_c5_native_interrupt_reconciliation_preserves_interaction_and_lock(
        self,
    ) -> None:
        """原生 interrupt 已提交时只收敛等待态，保留 interaction 与资源 owner。"""

        run_id = "native-workbench-source"
        self._write_workbench_lifecycle(run_id)
        start_workbench_execution(
            self.workspace,
            scope="application",
            target_id="application",
            page_id=None,
            thread_id=self.source.thread_id,
            run_id=run_id,
            phase="prepare_build_tasks",
        )
        await observe_execution_started(
            workspace=str(self.workspace),
            project_id="interrupted-app",
            thread_id=self.source.thread_id,
            run_id=run_id,
            workflow_scope="application",
            first_node="prepare_build_tasks",
        )
        await mark_execution_interrupted(
            workspace=self.workspace,
            run_id=run_id,
            interrupted_at=datetime.now(timezone.utc),
        )
        source = await get_execution(self.workspace, run_id)
        self.assertIsNotNone(source)
        assert source is not None
        snapshot = SimpleNamespace(
            values={
                "active_run_id": run_id,
                "phase": "prepare_build_tasks",
                "status": "running",
                "clarification": {
                    "mode": "build_task_plan_confirmation",
                    "status": "requires_user_input",
                    "message": "确认任务计划",
                },
            },
            next=("prepare_build_tasks",),
            tasks=(SimpleNamespace(interrupts=("task-plan-confirmation",)),),
        )
        reconciled = await reconcile_interrupted_execution_state(
            workspace=self.workspace,
            source=source,
            status=DurableExecutionStatus.AWAITING_USER,
            snapshot=snapshot,
        )

        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled.status, DurableExecutionStatus.AWAITING_USER)
        self.assertIsNone(reconciled.ended_at)
        lifecycle = load_application_lifecycle(self.workspace)
        self.assertIsNotNone(lifecycle)
        assert lifecycle is not None
        active = lifecycle.active_executions[run_id]
        self.assertEqual(active.status, WorkbenchExecutionStatus.AWAITING_USER)
        self.assertIsNotNone(active.pending_interaction)
        self.assertIsNotNone(lifecycle.resource_locks.application)
        assert lifecycle.resource_locks.application is not None
        self.assertEqual(lifecycle.resource_locks.application.run_id, run_id)
        self.assertEqual(await list_recovery_attempts_from_source(self.workspace, run_id), [])

    async def test_c1_terminal_failed_reconciliation_has_no_failed_node_retry_evidence(
        self,
    ) -> None:
        """Graph terminal 且 State.status=failed 时只收口 FAILED，不凭空生成异常重试证据。"""

        run_id = "terminal-failed-workbench-source"
        self._write_workbench_lifecycle(run_id)
        start_workbench_execution(
            self.workspace,
            scope="application",
            target_id="application",
            page_id=None,
            thread_id=self.source.thread_id,
            run_id=run_id,
            phase="handle_failure",
        )
        await observe_execution_started(
            workspace=str(self.workspace),
            project_id="interrupted-app",
            thread_id=self.source.thread_id,
            run_id=run_id,
            workflow_scope="application",
            first_node="handle_failure",
        )
        await mark_execution_interrupted(
            workspace=self.workspace,
            run_id=run_id,
            interrupted_at=datetime.now(timezone.utc),
        )
        source = await get_execution(self.workspace, run_id)
        self.assertIsNotNone(source)
        assert source is not None
        reconciled = await reconcile_interrupted_execution_state(
            workspace=self.workspace,
            source=source,
            status=DurableExecutionStatus.FAILED,
            snapshot=SimpleNamespace(
                values={
                    "active_run_id": run_id,
                    "phase": "handle_failure",
                    "status": "failed",
                    "message": "业务失败已由 Graph 提交。",
                },
                next=(),
                tasks=(),
            ),
        )

        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled.status, DurableExecutionStatus.FAILED)
        self.assertIsNone(reconciled.failure)
        lifecycle = load_application_lifecycle(self.workspace)
        self.assertIsNotNone(lifecycle)
        assert lifecycle is not None
        self.assertEqual(
            lifecycle.active_executions[run_id].status,
            WorkbenchExecutionStatus.FAILED,
        )
        self.assertEqual(await list_recovery_attempts_from_source(self.workspace, run_id), [])

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

    async def test_latest_checkpoint_owner_does_not_fall_back_to_older_source(self) -> None:
        """最新 active_run_id 已切换时不得扫描旧 checkpoint 证明 stale source。"""

        latest = self._snapshot(
            checkpoint_id="latest-child",
            next_nodes=("build",),
        )
        latest.values["active_run_id"] = "recovery-child"
        older = self._snapshot(
            checkpoint_id="older-source",
            next_nodes=("build",),
        )
        child = self.source.model_copy(
            update={
                "run_id": "recovery-child",
                "status": DurableExecutionStatus.RUNNING,
                "ended_at": None,
            }
        )
        lineage = SimpleNamespace(
            head=child,
            state=SimpleNamespace(value="RUNNING_HEAD"),
            reason_code="RECOVERY_LINEAGE_HEAD_RUNNING",
        )
        resolver = AsyncMock(return_value=lineage)

        with patch(
            "app.services.workflow_reentry.resolve_recovery_lineage_head",
            new=resolver,
        ):
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=_HistoryGraph([latest, older]),
            )

        self.assertEqual(resolution.kind, "needs_attention")
        self.assertEqual(resolution.reason_code, "RECOVERY_SOURCE_NOT_CURRENT")
        resolver.assert_awaited_once_with(
            str(self.workspace),
            thread_id=self.source.thread_id,
            execution_kind=self.source.execution_kind,
            authoritative_run_id="recovery-child",
        )

    async def test_interrupted_projection_uses_resolver_action_only(self) -> None:
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
        ):
            candidate = await _resolve_candidate(self.source)

        assert candidate is not None
        assert candidate.recovery_action_plan is not None
        assert candidate.recovery_action_plan.primary_action is not None
        self.assertEqual(
            candidate.recovery_action_plan.primary_action.kind,
            RecoveryActionKind.CONTINUE_CHECKPOINT,
        )

    async def test_business_failed_without_exception_evidence_has_no_retry_action(self) -> None:
        """业务 FAILED 没有 escaped exception evidence 时不得产生失败节点重试动作。"""

        source = self.source.model_copy(
            update={
                "status": DurableExecutionStatus.FAILED,
                "failure": None,
            }
        )
        with (
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.execution_recovery_projection.FailureTargetResolver.resolve",
                new=AsyncMock(side_effect=AssertionError("business FAILED entered node retry")),
            ),
        ):
            candidate = await _resolve_candidate(source)

        assert candidate is not None
        assert candidate.recovery_action_plan is not None
        self.assertIsNone(candidate.recovery_action_plan.primary_action)
        self.assertEqual(
            candidate.recovery_action_plan.reason_code,
            "FAILED_EXCEPTION_EVIDENCE_MISSING",
        )


if __name__ == "__main__":
    unittest.main()
