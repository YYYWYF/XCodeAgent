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
    RecoveryExecutionError,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import (
    get_execution,
    insert_execution,
    insert_recovery_point,
)
from app.services.execution_retry_dispatcher import prepare_retry_current_failure


class _RetrySnapshot:
    """提供 Dispatcher 校验和旧 Workflow parser 所需的最小 checkpoint。"""

    def __init__(self, thread_id: str, checkpoint_id: str, values: dict[str, object]) -> None:
        """保存精确 checkpoint identity 与可供 Adapter 读取的 Graph State。"""

        self.config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        self.values = values
        self.next = ("build",)
        self.tasks: tuple[object, ...] = ()


class _RetryGraph:
    """模拟 LangGraph 对历史 checkpoint 与当前 thread head 的不同读取。"""

    def __init__(self, exact_snapshot: _RetrySnapshot, latest_snapshot: _RetrySnapshot) -> None:
        """保存精确失败现场与当前 thread head。"""

        self.exact_snapshot = exact_snapshot
        self.latest_snapshot = latest_snapshot
        self.configs: list[dict[str, object]] = []

    async def aget_state(self, config: dict[str, object]) -> _RetrySnapshot:
        """根据是否带 checkpoint_id 返回历史现场或当前 head。"""

        self.configs.append(config)
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict) and configurable.get("checkpoint_id"):
            return self.exact_snapshot
        return self.latest_snapshot


class ExecutionRetryDispatcherTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 Generic Retry 的 source、checkpoint identity 与两个旧 Adapter。"""

    def setUp(self) -> None:
        """为每条 Dispatcher 测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放 Dispatcher 测试工作区。"""

        self._temporary_workspace.cleanup()

    async def _insert_source(
        self,
        *,
        run_id: str,
        status: DurableExecutionStatus = DurableExecutionStatus.FAILED,
        execution_kind: str = "workbench",
        values: dict[str, object] | None = None,
    ) -> tuple[DurableExecutionRecord, _RetrySnapshot]:
        """写入带精确失败 RecoveryPoint 的 source 与对应 Graph snapshot。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id=run_id,
            thread_id="execution-thread",
            owner_session_id="session-owner",
            workspace=str(self.workspace),
            project_id="project-001",
            execution_kind=execution_kind,  # type: ignore[arg-type]
            workflow_scope=None,
            first_node="build",
            current_node="build",
            status=status,
            last_recovery_point_id=f"point-{run_id}",
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        await insert_execution(source)
        checkpoint_id = f"checkpoint-{run_id}"
        await insert_recovery_point(
            workspace=self.workspace,
            point=RecoveryPoint(
                recovery_point_id=source.last_recovery_point_id or "",
                run_id=run_id,
                thread_id=source.thread_id,
                kind=RecoveryPointKind.CHECKPOINT,
                checkpoint_id=checkpoint_id,
                checkpoint_ns="",
                graph_node="build",
                next_nodes=["build"],
                captured_at=now,
            ),
        )
        snapshot_values: dict[str, object] = {
            "active_run_id": run_id,
            "phase": "build",
            "status": "failed",
            "build_execution_scope": {"type": "page", "targetId": "page-1"},
            **(values or {}),
        }
        return source, _RetrySnapshot(source.thread_id, checkpoint_id, snapshot_values)

    async def test_build_adapter_uses_source_identity(self) -> None:
        """Build 失败由 Backend 选择旧 handler，并保留 source thread/session。"""

        source, snapshot = await self._insert_source(
            run_id="run-build",
            values={
                "build_summary": {
                    "recovery_available": True,
                    "recovery_task_ids": ["task-1"],
                },
            },
        )
        graph = _RetryGraph(snapshot, snapshot)

        plan = await prepare_retry_current_failure(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.handler, "retry_failed_tasks")
        self.assertEqual(plan.source_run_id, source.run_id)
        self.assertEqual(plan.thread_id, source.thread_id)
        self.assertEqual(plan.owner_session_id, source.owner_session_id)
        self.assertNotEqual(plan.internal_payload["runId"], source.run_id)
        self.assertEqual(plan.internal_payload["threadId"], source.thread_id)
        forwarded = plan.internal_payload["forwardedProps"]
        self.assertEqual(forwarded["sessionId"], source.owner_session_id)
        self.assertEqual(forwarded["workflowAction"], "retry_failed_tasks")
        self.assertEqual(forwarded["resumeExecutionRunId"], source.run_id)
        self.assertEqual(
            forwarded["resumeState"]["state"]["active_run_id"],
            source.run_id,
        )
        self.assertEqual(
            graph.configs,
            [
                {
                    "configurable": {
                        "thread_id": source.thread_id,
                        "checkpoint_ns": "",
                        "checkpoint_id": "checkpoint-run-build",
                    }
                },
                {
                    "configurable": {
                        "thread_id": source.thread_id,
                        "checkpoint_ns": "",
                    }
                },
            ],
        )

    async def test_code_review_adapter_precedes_build_adapter(self) -> None:
        """带审查模型重试能力的失败现场必须选择更具体的审查 handler。"""

        source, snapshot = await self._insert_source(
            run_id="run-review",
            values={
                "phase": "code_review",
                "code_review_retry": {"available": True, "target": "scan"},
                "build_summary": {"recovery_available": True},
            },
        )
        graph = _RetryGraph(snapshot, snapshot)

        plan = await prepare_retry_current_failure(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.handler, "retry_code_review")
        self.assertEqual(
            plan.internal_payload["forwardedProps"]["workflowAction"],
            "retry_code_review",
        )

    async def test_build_adapter_accepts_legacy_retry_available(self) -> None:
        """旧 checkpoint 的 retry_available 证据仍可进入 Build handler。"""

        source, snapshot = await self._insert_source(
            run_id="run-build-legacy",
            values={"build_summary": {"retry_available": True}},
        )
        graph = _RetryGraph(snapshot, snapshot)

        plan = await prepare_retry_current_failure(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.handler, "retry_failed_tasks")

    async def test_integration_test_failure_without_build_recovery_evidence_is_unsupported(
        self,
    ) -> None:
        """没有 Build 恢复证据的 Integration Test 失败不能误匹配 Build。"""

        source, snapshot = await self._insert_source(
            run_id="run-integration",
            values={
                "phase": "integration_test",
                "status": "failed",
            },
        )
        graph = _RetryGraph(snapshot, snapshot)

        with self.assertRaises(RecoveryExecutionError) as raised:
            await prepare_retry_current_failure(
                workspace=str(self.workspace),
                source_run_id=source.run_id,
                graph=graph,
            )

        self.assertEqual(raised.exception.code, "RETRY_HANDLER_NOT_AVAILABLE")
        persisted = await get_execution(str(self.workspace), source.run_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status, DurableExecutionStatus.FAILED)

    async def test_non_failed_source_is_rejected(self) -> None:
        """running、interrupted、stopped 和 completed source 都不能启动 Generic Retry。"""

        for status in (
            DurableExecutionStatus.RUNNING,
            DurableExecutionStatus.INTERRUPTED,
            DurableExecutionStatus.STOPPED,
            DurableExecutionStatus.COMPLETED,
        ):
            with self.subTest(status=status.value):
                source, _snapshot = await self._insert_source(
                    run_id=f"run-{status.value}",
                    status=status,
                )
                with self.assertRaises(RecoveryExecutionError) as raised:
                    await prepare_retry_current_failure(
                        workspace=str(self.workspace),
                        source_run_id=source.run_id,
                        graph=None,
                    )
                self.assertEqual(raised.exception.code, "RETRY_SOURCE_NOT_FAILED")

    async def test_historical_checkpoint_is_rejected_when_current_head_is_newer(self) -> None:
        """历史 checkpoint 仍属 source 但当前 thread head 已属于新 run 时拒绝重试。"""

        source, exact_snapshot = await self._insert_source(
            run_id="run-stale",
            values={
                "build_summary": {"recovery_available": True},
            },
        )
        latest_snapshot = _RetrySnapshot(
            source.thread_id,
            "checkpoint-run-newer",
            {"active_run_id": "run-newer", "phase": "build", "status": "failed"},
        )
        graph = _RetryGraph(exact_snapshot, latest_snapshot)

        with self.assertRaises(RecoveryExecutionError) as raised:
            await prepare_retry_current_failure(
                workspace=str(self.workspace),
                source_run_id=source.run_id,
                graph=graph,
            )
        self.assertEqual(raised.exception.code, "RETRY_SOURCE_STALE")

    async def test_application_planning_is_not_supported_in_p1(self) -> None:
        """P1.0 不接 Planning Adapter，失败时保留原有 Planning 入口。"""

        source, _snapshot = await self._insert_source(
            run_id="run-planning",
            execution_kind="application_planning",
        )

        with self.assertRaises(RecoveryExecutionError) as raised:
            await prepare_retry_current_failure(
                workspace=str(self.workspace),
                source_run_id=source.run_id,
                graph=None,
            )
        self.assertEqual(raised.exception.code, "RETRY_HANDLER_NOT_AVAILABLE")

    async def test_no_adapter_returns_unsupported_without_execution_write(self) -> None:
        """没有 Adapter 时只返回明确拒绝，不创建新 execution 或修改 source。"""

        source, snapshot = await self._insert_source(run_id="run-unsupported")
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))
        with patch(
            "app.services.execution_retry_dispatcher.RETRY_ADAPTERS",
            (),
        ):
            with self.assertRaises(RecoveryExecutionError) as raised:
                await prepare_retry_current_failure(
                    workspace=str(self.workspace),
                    source_run_id=source.run_id,
                    graph=graph,
                )
        self.assertEqual(raised.exception.code, "RETRY_HANDLER_NOT_AVAILABLE")


__all__ = ["ExecutionRetryDispatcherTests"]
