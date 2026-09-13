from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.application_lifecycle import ExecutionResourceLocks
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    RecoveryActionKind,
    RecoveryAttemptStatus,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryExecutionError,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    claim_recovery_finalization,
    claim_operation_retry_attempt,
    finish_execution_and_release_lease,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    insert_execution,
    insert_recovery_point,
    list_executions_for_thread,
    list_recovery_attempts_from_source,
    list_recovery_points,
    update_recovery_attempt,
)
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
from app.services.execution_recovery_action_planner import plan_recovery_action
from app.services.execution_recovery_executor import prepare_operation_retry
from app.services.execution_recovery_lineage import (
    RecoveryLineageState,
    reconcile_recovery_attempt,
    resolve_recovery_lineage_head,
)


class _RetrySnapshot:
    """提供 Operation Retry capability 读取所需的精确与当前 Graph 快照。"""

    def __init__(self, thread_id: str, checkpoint_id: str, values: dict[str, object]) -> None:
        """保存 checkpoint identity、next 节点和失败操作证据。"""

        self.config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        self.next = ("code_review",)
        self.tasks: tuple[object, ...] = ()
        self.values = values


class _RetryGraph:
    """按 checkpoint identity 返回 source 失败现场或当前 thread head。"""

    def __init__(self, snapshot: _RetrySnapshot) -> None:
        """初始化可供 capability revalidation 使用的 Graph double。"""

        self.snapshot = snapshot

    async def aget_state(self, config: dict[str, object]) -> _RetrySnapshot:
        """对精确 checkpoint 与当前 head 返回同一份稳定 source 事实。"""

        return self.snapshot


class OperationRetryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 Operation Retry 的 durable HANDED_OFF crash reconcile 路径。"""

    def setUp(self) -> None:
        """为每条 Operation Retry 回归创建隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放隔离工作区。"""

        self._temporary_workspace.cleanup()

    async def _create_operation_retry_source(
        self,
        *,
        run_id: str,
        thread_id: str | None = None,
        values: dict[str, object] | None = None,
        checkpoint_id: str | None = None,
    ) -> tuple[DurableExecutionRecord, RecoveryPoint, RecoveryPlan, _RetrySnapshot]:
        """写入一个带 Code Review 能力证据的失败 source 与 checkpoint。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id=run_id,
            thread_id=thread_id or f"thread-{run_id}",
            owner_session_id="session-owner",
            workspace=str(self.workspace),
            project_id="project-review",
            execution_kind="workbench",
            workflow_scope=None,
            first_node="code_review",
            current_node="code_review",
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.MODEL_CALL,
                code="MODEL_ERROR",
                operation="code_review",
                diagnostic_message="model not found",
            ),
        )
        await insert_execution(source)
        point = RecoveryPoint(
            recovery_point_id=f"point-{run_id}",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id=checkpoint_id or f"checkpoint-{run_id}",
            checkpoint_ns="",
            graph_node="code_review",
            next_nodes=["code_review"],
            captured_at=now,
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        persisted_source = await get_execution(self.workspace, source.run_id)
        assert persisted_source is not None
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            decision="requires_handler",
            strategy=RecoveryStrategy.OPERATION_RETRY,
            recovery_point_id=point.recovery_point_id,
            checkpoint_id=point.checkpoint_id,
            checkpoint_ns=point.checkpoint_ns,
            next_nodes=list(point.next_nodes),
            reason_code="RETRY_OPERATION_AVAILABLE",
            reason="test operation retry",
        )
        snapshot = _RetrySnapshot(
            source.thread_id,
            point.checkpoint_id or "",
            {
                "active_run_id": source.run_id,
                "phase": "code_review",
                "status": "failed",
                "code_review_retry": {"available": True, "target": "scan"},
                **(values or {}),
            },
        )
        return persisted_source, point, plan, snapshot

    @staticmethod
    def _child_lifecycle(new_run_id: str, thread_id: str) -> SimpleNamespace:
        """构造只包含当前 Workbench child ownership 的生命周期 double。"""

        return SimpleNamespace(
            revision=1,
            active_run_id=new_run_id,
            active_executions={
                new_run_id: SimpleNamespace(
                    thread_id=thread_id,
                    status=SimpleNamespace(value="running"),
                    pending_interaction=None,
                    resource_keys=[],
                )
            },
            resource_locks=ExecutionResourceLocks(),
        )

    async def test_handed_off_crash_reconciles_operation_retry_without_native_planner(self) -> None:
        """HANDED_OFF crash 必须直接继续 durable Operation Retry，不得进入 Native capability。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="review-source",
            thread_id="review-thread",
            owner_session_id="session-owner",
            workspace=str(self.workspace),
            project_id="project-review",
            execution_kind="workbench",
            workflow_scope=None,
            first_node="code_review",
            current_node="code_review",
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.MODEL_CALL,
                code="MODEL_ERROR",
                operation="code_review",
                diagnostic_message="model not found",
            ),
        )
        await insert_execution(source)
        point = RecoveryPoint(
            recovery_point_id="review-source-point",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id="review-source-checkpoint",
            checkpoint_ns="",
            graph_node="code_review",
            next_nodes=["code_review"],
            captured_at=now,
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        source = await get_execution(self.workspace, source.run_id)
        assert source is not None
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            decision="requires_handler",
            strategy=RecoveryStrategy.OPERATION_RETRY,
            recovery_point_id=point.recovery_point_id,
            checkpoint_id=point.checkpoint_id,
            checkpoint_ns="",
            next_nodes=["code_review"],
            reason_code="RETRY_OPERATION_AVAILABLE",
            reason="test operation retry",
        )
        child, _lease, _attempt = await claim_operation_retry_attempt(
            source=source,
            plan=plan,
            new_run_id="review-child",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        snapshot = _RetrySnapshot(
            source.thread_id,
            point.checkpoint_id or "",
            {
                "active_run_id": source.run_id,
                "phase": "code_review",
                "status": "failed",
                "code_review_retry": {"available": True, "target": "scan"},
            },
        )
        lifecycle = SimpleNamespace(
            active_run_id=child.run_id,
            active_executions={
                child.run_id: SimpleNamespace(
                    thread_id=source.thread_id,
                    status=SimpleNamespace(value="running"),
                    pending_interaction=None,
                    resource_keys=[],
                )
            },
            resource_locks=ExecutionResourceLocks(),
        )
        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.05,
            execution_recovery_lease_ttl_seconds=30,
        )
        with (
            patch(
                "app.services.execution_recovery_executor.load_application_lifecycle",
                return_value=lifecycle,
            ),
            patch(
                "app.services.execution_recovery_executor.application_lifecycle_payload",
                return_value={},
            ),
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-new", pid=202),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor.workspace_run_leases.acquire",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor._require_native_plan",
                side_effect=AssertionError("operation retry must not require native plan"),
            ),
        ):
            context = await reconcile_recovery_attempt(
                workspace=self.workspace,
                new_run_id=child.run_id,
                graph=_RetryGraph(snapshot),
            )

        persisted_attempt = await get_recovery_attempt(self.workspace, child.run_id)
        persisted_child = await get_execution(self.workspace, child.run_id)
        assert persisted_attempt is not None
        assert persisted_child is not None
        self.assertEqual(persisted_attempt.status, RecoveryAttemptStatus.STARTED)
        self.assertEqual(persisted_attempt.strategy, RecoveryStrategy.OPERATION_RETRY)
        self.assertEqual(persisted_attempt.source_run_id, source.run_id)
        self.assertEqual(persisted_child.last_recovery_point_id, "recovery-point-review-child-entry")
        self.assertEqual(context.recovery_action_kind, "retry_operation")
        self.assertEqual(context.new_run_id, child.run_id)

    async def test_preparing_crash_fails_prestart_and_keeps_source_as_lineage_head(self) -> None:
        """PREPARING crash 不能让尚未 handoff 的 child 污染 canonical lineage head。"""

        source, point, plan, _snapshot = await self._create_operation_retry_source(
            run_id="preparing-source",
        )
        child, _lease, attempt = await claim_operation_retry_attempt(
            source=source,
            plan=plan,
            new_run_id="preparing-child",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        self.assertEqual(attempt.status, RecoveryAttemptStatus.PREPARING)
        source_lifecycle = SimpleNamespace(
            active_run_id=source.run_id,
            active_executions={source.run_id: object()},
        )
        with patch(
            "app.services.execution_recovery_lineage.load_application_lifecycle",
            return_value=source_lifecycle,
        ):
            reconciled = await reconcile_recovery_attempt(
                workspace=self.workspace,
                new_run_id=child.run_id,
            )

        self.assertIsNotNone(reconciled)
        persisted_attempt = await get_recovery_attempt(self.workspace, child.run_id)
        persisted_source = await get_execution(self.workspace, source.run_id)
        self.assertIsNotNone(persisted_attempt)
        self.assertIsNotNone(persisted_source)
        assert persisted_attempt is not None
        assert persisted_source is not None
        self.assertEqual(persisted_attempt.status, RecoveryAttemptStatus.FAILED_PRESTART)
        self.assertEqual(persisted_source.status, DurableExecutionStatus.FAILED)
        self.assertEqual(persisted_source.last_recovery_point_id, point.recovery_point_id)

        resolution = await resolve_recovery_lineage_head(
            self.workspace,
            thread_id=source.thread_id,
            execution_kind="workbench",
        )
        self.assertEqual(resolution.state, RecoveryLineageState.RECOVERABLE_HEAD)
        self.assertIsNotNone(resolution.head)
        assert resolution.head is not None
        self.assertEqual(resolution.head.run_id, source.run_id)

    async def test_finalizing_crash_takeover_starts_operation_retry_once(self) -> None:
        """FINALIZING crash 必须允许新 Backend 接管，并且 ENTRY boundary 幂等。"""

        source, _point, plan, snapshot = await self._create_operation_retry_source(
            run_id="finalizing-source",
        )
        child, _lease, _attempt = await claim_operation_retry_attempt(
            source=source,
            plan=plan,
            new_run_id="finalizing-child",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-old",
            new_owner_pid=101,
            lease_ttl_seconds=1,
            claim_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        lifecycle = self._child_lifecycle(child.run_id, source.thread_id)
        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.05,
            execution_recovery_lease_ttl_seconds=30,
        )
        graph = _RetryGraph(snapshot)
        with (
            patch(
                "app.services.execution_recovery_executor.load_application_lifecycle",
                return_value=lifecycle,
            ),
            patch(
                "app.services.execution_recovery_executor.application_lifecycle_payload",
                return_value={},
            ),
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-new", pid=202),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor.workspace_run_leases.acquire",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor._require_native_plan",
                side_effect=AssertionError("operation retry finalization must not re-plan native recovery"),
            ),
        ):
            context = await reconcile_recovery_attempt(
                workspace=self.workspace,
                new_run_id=child.run_id,
                graph=graph,
            )
            second_reconcile = await reconcile_recovery_attempt(
                workspace=self.workspace,
                new_run_id=child.run_id,
                graph=graph,
            )

        persisted_attempt = await get_recovery_attempt(self.workspace, child.run_id)
        persisted_child = await get_execution(self.workspace, child.run_id)
        entry_points = [
            point
            for point in await list_recovery_points(self.workspace, child.run_id)
            if point.kind is RecoveryPointKind.ENTRY
        ]
        self.assertEqual(getattr(context, "recovery_action_kind", None), "retry_operation")
        self.assertIsNotNone(second_reconcile)
        self.assertIsNotNone(persisted_attempt)
        self.assertIsNotNone(persisted_child)
        assert persisted_attempt is not None
        assert persisted_child is not None
        self.assertEqual(persisted_attempt.status, RecoveryAttemptStatus.STARTED)
        self.assertEqual(persisted_attempt.strategy, RecoveryStrategy.OPERATION_RETRY)
        self.assertEqual(
            persisted_child.last_recovery_point_id,
            f"recovery-point-{child.run_id}-entry",
        )
        self.assertEqual(len(entry_points), 1)

    async def test_public_execute_double_click_creates_one_operation_retry_child(self) -> None:
        """公共 execute journey 的同一 incident/action 并发请求只能 claim 一个 child。"""

        source, point, plan, snapshot = await self._create_operation_retry_source(
            run_id="public-source",
        )
        graph = _RetryGraph(snapshot)
        action_plan, _stage = await plan_recovery_action(
            workspace=str(self.workspace),
            source=source,
            recovery_plan=plan,
            point=point,
            snapshot=snapshot,
            lifecycle=None,
            graph=graph,
        )
        primary_action = action_plan.primary_action
        self.assertIsNotNone(primary_action)
        assert primary_action is not None
        self.assertEqual(primary_action.kind, RecoveryActionKind.RETRY_OPERATION)
        latest_lifecycle: dict[str, SimpleNamespace] = {}
        runtime_contexts: list[object] = []

        def handoff_lifecycle(*_args: object, **kwargs: object) -> SimpleNamespace:
            """为每个真实 claim 返回对应 child ownership 的外部 lifecycle。"""

            child_lifecycle = self._child_lifecycle(
                str(kwargs["new_run_id"]),
                str(kwargs["thread_id"]),
            )
            latest_lifecycle["value"] = child_lifecycle
            return child_lifecycle

        async def runtime_stream(**kwargs: object):
            """只模拟 Runtime 接管边界，保留 public protocol 的 action dispatch。"""

            runtime_contexts.append(kwargs["native_recovery_context"])
            yield "runtime-started"

        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.05,
            execution_recovery_lease_ttl_seconds=30,
        )
        payload = {
            "forwardedProps": {
                "workspaceRoot": str(self.workspace),
                "executionRecovery": {
                    "action": "execute",
                    "incidentId": action_plan.incident_id,
                    "actionId": primary_action.action_id,
                },
            }
        }
        with (
            patch(
                "app.protocols.execution_recovery.workflow_graph_for_request",
                new=AsyncMock(return_value=graph),
            ),
            patch(
                "app.protocols.execution_recovery._prepare_recovery_plan",
                new=AsyncMock(return_value=plan),
            ),
            patch(
                "app.protocols.execution_recovery.build_workflow_ag_ui_stream",
                side_effect=runtime_stream,
            ),
            patch(
                "app.services.execution_recovery_executor._handoff_lifecycle",
                side_effect=handoff_lifecycle,
            ),
            patch(
                "app.services.execution_recovery_executor.load_application_lifecycle",
                side_effect=lambda _workspace: latest_lifecycle.get("value"),
            ),
            patch(
                "app.services.execution_recovery_executor.application_lifecycle_payload",
                return_value={},
            ),
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-public", pid=303),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor.workspace_run_leases.acquire",
                return_value=None,
            ),
        ):
            async def collect() -> list[str]:
                """收集一次 public execute AG-UI stream 的所有 frame。"""

                return [
                    frame
                    async for frame in build_execution_recovery_ag_ui_stream(
                        payload=payload,
                    )
                ]

            first, second = await asyncio.gather(collect(), collect())

        outputs = ["".join(first), "".join(second)]
        self.assertEqual(sum("runtime-started" in output for output in outputs), 1)
        self.assertEqual(
            sum(
                code in output
                for output in outputs
                for code in ("RECOVERY_ALREADY_CLAIMED", "STALE_RECOVERY_ACTION")
            ),
            1,
        )
        attempts = await list_recovery_attempts_from_source(self.workspace, source.run_id)
        executions = await list_executions_for_thread(
            self.workspace,
            thread_id=source.thread_id,
            execution_kind="workbench",
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(executions), 2)
        self.assertEqual(len(runtime_contexts), 1)

    async def test_retry_child_failure_advances_canonical_source_to_child(self) -> None:
        """A→B 失败后，第二次 planner 与 retry 必须从 B 继续生成 C。"""

        source_a, _point_a, plan_a, snapshot_a = await self._create_operation_retry_source(
            run_id="chain-a",
        )
        lifecycle_box: dict[str, SimpleNamespace] = {}

        def handoff_lifecycle(*_args: object, **kwargs: object) -> SimpleNamespace:
            """为真实 operation retry handoff 生成当前 child ownership。"""

            child_lifecycle = self._child_lifecycle(
                str(kwargs["new_run_id"]),
                str(kwargs["thread_id"]),
            )
            lifecycle_box["value"] = child_lifecycle
            return child_lifecycle

        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.05,
            execution_recovery_lease_ttl_seconds=30,
        )
        with (
            patch(
                "app.services.execution_recovery_executor._handoff_lifecycle",
                side_effect=handoff_lifecycle,
            ),
            patch(
                "app.services.execution_recovery_executor.load_application_lifecycle",
                side_effect=lambda _workspace: lifecycle_box.get("value"),
            ),
            patch(
                "app.services.execution_recovery_executor.application_lifecycle_payload",
                return_value={},
            ),
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-chain", pid=404),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor.workspace_run_leases.acquire",
                return_value=None,
            ),
        ):
            context_ab = await prepare_operation_retry(
                workspace=str(self.workspace),
                source_run_id=source_a.run_id,
                graph=_RetryGraph(snapshot_a),
                recovery_plan=plan_a,
            )

        child_b = await get_execution(self.workspace, context_ab.new_run_id)
        child_b_lease = await get_execution_lease(self.workspace, context_ab.new_run_id)
        self.assertIsNotNone(child_b)
        self.assertIsNotNone(child_b_lease)
        assert child_b is not None
        assert child_b_lease is not None
        await finish_execution_and_release_lease(
            workspace=self.workspace,
            run_id=child_b.run_id,
            status=DurableExecutionStatus.FAILED,
            owner_backend_instance_id=child_b_lease.owner_backend_instance_id,
            ended_at=datetime.now(timezone.utc),
            failure=ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.MODEL_CALL,
                code="MODEL_ERROR",
                operation="code_review",
                diagnostic_message="second attempt failed",
            ),
        )
        now = datetime.now(timezone.utc)
        point_b = RecoveryPoint(
            recovery_point_id=f"point-{child_b.run_id}-failed",
            run_id=child_b.run_id,
            thread_id=source_a.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id=f"checkpoint-{child_b.run_id}-failed",
            checkpoint_ns="",
            graph_node="code_review",
            next_nodes=["code_review"],
            captured_at=now,
        )
        await insert_recovery_point(workspace=self.workspace, point=point_b)
        source_b = await get_execution(self.workspace, child_b.run_id)
        assert source_b is not None
        resolution_b = await resolve_recovery_lineage_head(
            self.workspace,
            thread_id=source_a.thread_id,
            execution_kind="workbench",
        )
        self.assertIsNotNone(resolution_b.head)
        assert resolution_b.head is not None
        self.assertEqual(resolution_b.head.run_id, child_b.run_id)

        snapshot_b = _RetrySnapshot(
            source_a.thread_id,
            point_b.checkpoint_id or "",
            {
                "active_run_id": child_b.run_id,
                "phase": "code_review",
                "status": "failed",
                "code_review_retry": {"available": True, "target": "scan"},
            },
        )
        plan_b = plan_a.model_copy(
            update={
                "source_run_id": child_b.run_id,
                "recovery_point_id": point_b.recovery_point_id,
                "checkpoint_id": point_b.checkpoint_id,
                "thread_id": child_b.thread_id,
            }
        )
        action_b, _stage = await plan_recovery_action(
            workspace=str(self.workspace),
            source=source_b,
            recovery_plan=plan_b,
            point=point_b,
            snapshot=snapshot_b,
            lifecycle=None,
            graph=_RetryGraph(snapshot_b),
        )
        self.assertEqual(action_b.source_run_id, child_b.run_id)
        self.assertIsNotNone(action_b.primary_action)
        assert action_b.primary_action is not None
        self.assertEqual(action_b.primary_action.kind, RecoveryActionKind.RETRY_OPERATION)

        with (
            patch(
                "app.services.execution_recovery_executor._handoff_lifecycle",
                side_effect=handoff_lifecycle,
            ),
            patch(
                "app.services.execution_recovery_executor.load_application_lifecycle",
                side_effect=lambda _workspace: lifecycle_box.get("value"),
            ),
            patch(
                "app.services.execution_recovery_executor.application_lifecycle_payload",
                return_value={},
            ),
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-chain", pid=404),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor.workspace_run_leases.acquire",
                return_value=None,
            ),
        ):
            context_bc = await prepare_operation_retry(
                workspace=str(self.workspace),
                source_run_id=source_b.run_id,
                graph=_RetryGraph(snapshot_b),
                recovery_plan=plan_b,
            )

        attempts_ab = await list_recovery_attempts_from_source(self.workspace, source_a.run_id)
        attempts_bc = await list_recovery_attempts_from_source(self.workspace, source_b.run_id)
        final_resolution = await resolve_recovery_lineage_head(
            self.workspace,
            thread_id=source_a.thread_id,
            execution_kind="workbench",
        )
        self.assertEqual(len(attempts_ab), 1)
        self.assertEqual(len(attempts_bc), 1)
        self.assertEqual(attempts_ab[0].source_run_id, source_a.run_id)
        self.assertEqual(attempts_ab[0].new_run_id, child_b.run_id)
        self.assertEqual(attempts_bc[0].source_run_id, child_b.run_id)
        self.assertEqual(attempts_bc[0].new_run_id, context_bc.new_run_id)
        self.assertNotEqual(source_a.run_id, child_b.run_id)
        self.assertNotEqual(child_b.run_id, context_bc.new_run_id)
        self.assertIsNotNone(final_resolution.head)
        assert final_resolution.head is not None
        self.assertEqual(final_resolution.head.run_id, context_bc.new_run_id)

    async def test_operation_retry_finalization_drift_marks_child_head(self) -> None:
        """handoff 后 source RecoveryPoint 漂移必须失败在 child，并保留 child 为 head。"""

        source, point, plan, snapshot = await self._create_operation_retry_source(
            run_id="drift-source",
        )
        child, _lease, _attempt = await claim_operation_retry_attempt(
            source=source,
            plan=plan,
            new_run_id="drift-child",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-old",
            new_owner_pid=101,
            lease_ttl_seconds=1,
            claim_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        drift_point = point.model_copy(
            update={
                "recovery_point_id": "drift-source-point-new",
                "checkpoint_id": "drift-source-checkpoint-new",
                "captured_at": datetime.now(timezone.utc),
            }
        )
        await insert_recovery_point(workspace=self.workspace, point=drift_point)
        lifecycle = self._child_lifecycle(child.run_id, source.thread_id)
        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.05,
            execution_recovery_lease_ttl_seconds=30,
        )
        with (
            patch(
                "app.services.execution_recovery_executor.load_application_lifecycle",
                return_value=lifecycle,
            ),
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-new", pid=202),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor._require_native_plan",
                side_effect=AssertionError("operation retry drift must not invoke the Native planner"),
            ),
        ):
            with self.assertRaises(RecoveryExecutionError) as raised:
                await reconcile_recovery_attempt(
                    workspace=self.workspace,
                    new_run_id=child.run_id,
                    graph=_RetryGraph(snapshot),
                )

        self.assertEqual(getattr(raised.exception, "code", None), "RECOVERY_STATE_DRIFT")
        persisted_attempt = await get_recovery_attempt(self.workspace, child.run_id)
        persisted_child = await get_execution(self.workspace, child.run_id)
        self.assertIsNotNone(persisted_attempt)
        self.assertIsNotNone(persisted_child)
        assert persisted_attempt is not None
        assert persisted_child is not None
        self.assertEqual(persisted_attempt.status, RecoveryAttemptStatus.FINALIZATION_FAILED)
        self.assertEqual(persisted_child.status, DurableExecutionStatus.INTERRUPTED)
        resolution = await resolve_recovery_lineage_head(
            self.workspace,
            thread_id=source.thread_id,
            execution_kind="workbench",
        )
        self.assertIsNotNone(resolution.head)
        assert resolution.head is not None
        self.assertEqual(resolution.head.run_id, child.run_id)


__all__ = ["OperationRetryRecoveryTests"]
