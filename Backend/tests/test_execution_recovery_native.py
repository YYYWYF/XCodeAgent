from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionLease,
    ExecutionLeaseStatus,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    get_execution,
    get_execution_lease,
    insert_execution,
    insert_execution_with_lease,
    insert_recovery_point,
    list_recovery_points,
)
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.backend_instance import current_backend_instance
from app.services.execution_recovery_executor import NativeRecoveryRuntimeContext


class _Snapshot:
    """提供 Runtime 需要的最小 LangGraph StateSnapshot。"""

    def __init__(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
        next_nodes: list[str],
        values: dict[str, object],
    ) -> None:
        """保存 checkpoint identity、后继节点和 reducer 状态。"""

        self.config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        self.next = tuple(next_nodes)
        self.values = values
        self.tasks: tuple[object, ...] = ()


class _NativeGraph:
    """记录 Native Runtime input 和 snapshot 查询的最小 Graph double。"""

    def __init__(
        self,
        *,
        snapshot: _Snapshot,
        fail: bool = False,
        block: bool = False,
    ) -> None:
        """初始化可完成、失败或阻塞的 Graph 行为。"""

        self.snapshot = snapshot
        self.fail = fail
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.inputs: list[object] = []
        self.state_configs: list[dict[str, object]] = []
        self.registry_active_during_stream = False

    async def astream(
        self,
        graph_input: object,
        *,
        config: dict[str, object],
        stream_mode: list[str],
        **kwargs: object,
    ):
        """记录调用参数，并模拟 Native child 执行一个后继节点。"""

        del config, stream_mode, kwargs
        self.inputs.append(graph_input)
        self.started.set()
        self.registry_active_during_stream = workflow_run_registry.is_active(
            str(self.snapshot.values["active_run_id"]),
            workspace=str(self.snapshot.values["workspace"]),
        )
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("native node failed")
        yield ("updates", {"requirements": {"status": "completed"}})

    async def aget_state(self, config: dict[str, object]) -> _Snapshot:
        """返回固定 fork/live snapshot，并记录 Runtime 的读取配置。"""

        self.state_configs.append(config)
        return self.snapshot


class NativeRecoveryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 P0.3B Native child 接管、Planning fork 和 lifecycle 收口。"""

    def setUp(self) -> None:
        """为每个 Native Runtime 测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放隔离工作区。"""

        self._temporary_workspace.cleanup()

    async def test_native_runtime_skips_durable_preflight_but_claims_registry(self) -> None:
        """Native child 不检查重复 durable runId，但仍独占当前进程 Registry。"""

        context, graph = await self._context(workflow_scope=None)
        with patch(
            "app.protocols.workflow.runtime.assert_run_id_available",
            new_callable=AsyncMock,
        ) as preflight:
            frames = [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={},
                    native_recovery_context=context,
                )
            ]

        preflight.assert_not_awaited()
        self.assertEqual(graph.inputs, [None])
        self.assertTrue(graph.registry_active_during_stream)
        self.assertFalse(
            workflow_run_registry.is_active(
                context.new_run_id,
                workspace=str(self.workspace),
            )
        )
        child = await get_execution(self.workspace, context.new_run_id)
        self.assertIsNotNone(child)
        assert child is not None
        self.assertEqual(child.status, DurableExecutionStatus.COMPLETED)
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))

    async def test_native_application_planning_uses_fork_snapshot_and_none_input(self) -> None:
        """Application Planning Native 不进入 snapshot-only，也不读取无 namespace 的普通快照。"""

        context, graph = await self._context(workflow_scope="application_planning")
        frames = [
            frame
            async for frame in build_workflow_ag_ui_stream(
                graph=graph,
                payload={},
                native_recovery_context=context,
            )
        ]

        self.assertEqual(graph.inputs, [None])
        self.assertTrue(graph.state_configs)
        self.assertTrue(all(
            config.get("configurable", {}).get("checkpoint_ns") == ""
            for config in graph.state_configs
        ))
        self.assertNotIn('"snapshotOnly":true', "".join(frames))
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))

    async def test_native_exception_fails_workbench_lifecycle_and_releases_child(self) -> None:
        """Native Workbench 异常必须同时失败 lifecycle 与 child durable execution。"""

        context, graph = await self._context(
            workflow_scope=None,
            lifecycle_payload={"activeExecutions": {"recovery-child": {}}},
            fail=True,
        )
        with patch(
            "app.protocols.workflow.runtime.fail_workflow_lifecycle",
            return_value={"status": "failed"},
        ) as fail_lifecycle:
            frames = [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={},
                    native_recovery_context=context,
                )
            ]

        fail_lifecycle.assert_called_once()
        self.assertEqual(fail_lifecycle.call_args.kwargs["run_id"], context.new_run_id)
        child = await get_execution(self.workspace, context.new_run_id)
        lease = await get_execution_lease(self.workspace, context.new_run_id)
        self.assertIsNotNone(child)
        self.assertIsNotNone(lease)
        assert child is not None
        assert lease is not None
        self.assertEqual(child.status, DurableExecutionStatus.FAILED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)
        self.assertIn('"type":"RUN_ERROR"', "".join(frames))

    async def test_native_external_cancel_stops_workbench_lifecycle(self) -> None:
        """Native Graph 被外部取消时必须停止 lifecycle 并将 child 标记为 interrupted。"""

        context, graph = await self._context(
            workflow_scope=None,
            lifecycle_payload={"activeExecutions": {"recovery-child": {}}},
            block=True,
        )

        async def collect() -> list[str]:
            """消费被测试取消的 Native Runtime 流。"""

            return [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={},
                    native_recovery_context=context,
                )
            ]

        runtime_task = asyncio.create_task(collect())
        await asyncio.wait_for(graph.started.wait(), timeout=5)
        with patch(
            "app.protocols.workflow.runtime.stop_workflow_lifecycle",
            return_value={"status": "stopped"},
        ) as stop_lifecycle:
            runtime_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await runtime_task

        stop_lifecycle.assert_called_once()
        self.assertEqual(stop_lifecycle.call_args.kwargs["run_id"], context.new_run_id)
        child = await get_execution(self.workspace, context.new_run_id)
        lease = await get_execution_lease(self.workspace, context.new_run_id)
        self.assertIsNotNone(child)
        self.assertIsNotNone(lease)
        assert child is not None
        assert lease is not None
        self.assertEqual(child.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)

    async def _context(
        self,
        *,
        workflow_scope: str | None,
        lifecycle_payload: dict[str, object] | None = None,
        fail: bool = False,
        block: bool = False,
    ) -> tuple[NativeRecoveryRuntimeContext, _NativeGraph]:
        """创建已 claim child 的 Native Context 与可观测 Graph。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="native-thread",
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench" if workflow_scope is None else "application_planning",
            workflow_scope=workflow_scope,
            first_node="requirements",
            current_node="requirements",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        child_run_id = "recovery-child"
        child = source.model_copy(
            update={
                "run_id": child_run_id,
                "status": DurableExecutionStatus.RUNNING,
                "started_at": now,
                "updated_at": now,
                "ended_at": None,
            }
        )
        lease = ExecutionLease(
            run_id=child_run_id,
            owner_backend_instance_id=current_backend_instance().instance_id,
            owner_pid=123,
            status=ExecutionLeaseStatus.ACTIVE,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now,
        )
        await insert_execution(source)
        await insert_execution_with_lease(record=child, lease=lease)
        source_point = RecoveryPoint(
            recovery_point_id="source-point",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id="source-checkpoint",
            checkpoint_ns="",
            graph_node="requirements",
            next_nodes=["requirements"],
            captured_at=now,
        )
        await insert_recovery_point(workspace=self.workspace, point=source_point)
        values: dict[str, object] = {
            "active_run_id": child_run_id,
            "active_thread_id": source.thread_id,
            "phase": "requirements",
            "status": "running",
            "workspace": str(self.workspace),
            "workflow_scope": workflow_scope or "",
        }
        snapshot = _Snapshot(
            thread_id=source.thread_id,
            checkpoint_id="fork-checkpoint",
            next_nodes=[],
            values=values,
        )
        graph = _NativeGraph(snapshot=snapshot, fail=fail, block=block)
        context = NativeRecoveryRuntimeContext(
            source_execution=source,
            child_execution=child,
            recovery_plan=RecoveryPlan(
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                decision="ready_native",
                strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
                recovery_point_id=source_point.recovery_point_id,
                checkpoint_id=source_point.checkpoint_id,
                checkpoint_ns="",
                next_nodes=["requirements"],
                reason_code="TEST",
                reason="test",
            ),
            source_recovery_point=source_point,
            new_run_id=child_run_id,
            thread_id=source.thread_id,
            project_id=None,
            workspace=str(self.workspace),
            workflow_scope=workflow_scope,
            graph=graph,
            fork_config={"configurable": {"thread_id": source.thread_id, "checkpoint_id": "fork-checkpoint"}},
            observation_config={"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}},
            fork_snapshot=snapshot,
            lifecycle_payload=lifecycle_payload,
            workspace_lease=None,
            observability={"langsmith": {"enabled": False}},
            heartbeat_task=None,
        )
        return context, graph


__all__ = ["NativeRecoveryRuntimeTests"]
