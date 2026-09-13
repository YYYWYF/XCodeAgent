from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.application_lifecycle import ExecutionResourceLocks
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    RecoveryAttemptStatus,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    claim_operation_retry_attempt,
    get_execution,
    get_recovery_attempt,
    insert_execution,
    insert_recovery_point,
    update_recovery_attempt,
)
from app.services.execution_recovery_lineage import reconcile_recovery_attempt


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


__all__ = ["OperationRetryRecoveryTests"]
