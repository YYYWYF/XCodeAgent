from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    insert_execution,
    insert_recovery_point,
)
from app.services.execution_recovery_coordinator import (
    RecoveryCoordinator,
    prepare_continue,
)
from app.services.execution_recovery_strategy import AllowNodePolicy
from app.services.workspace_inspector import (
    INSPECTOR_SCHEMA_VERSION,
    snapshot_hash,
    workspace_inventory,
)


class _GraphDouble:
    """按 checkpointId 返回固定 StateSnapshot 的 Graph double。"""

    def __init__(self, snapshots: dict[str, object]) -> None:
        """保存 checkpoint 查询结果并记录每次查询配置。"""

        self.snapshots = snapshots
        self.configs: list[dict[str, object]] = []

    async def aget_state(self, config: dict[str, object]) -> object:
        """按精确 checkpointId 模拟 LangGraph 的异步 StateSnapshot 查询。"""

        self.configs.append(config)
        configurable = config["configurable"]
        assert isinstance(configurable, dict)
        checkpoint_id = str(configurable["checkpoint_id"])
        result = self.snapshots[checkpoint_id]
        if isinstance(result, BaseException):
            raise result
        return result


class ExecutionRecoveryCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 P0.3A 只读恢复计划、历史选择和严格校验合同。"""

    def setUp(self) -> None:
        """为每个测试准备隔离的工作区和可排序的时间基准。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)
        self._captured_at = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        """释放测试工作区。"""

        self._temporary_workspace.cleanup()

    async def test_interrupted_checkpoint_is_selected_but_default_is_not_safe(self) -> None:
        """Interrupted 应选择历史 checkpoint，但默认策略必须要求 Handler。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        point = await self._insert_point("rp-1", "cp-1", "A", ["B"])
        graph = _GraphDouble({"cp-1": self._snapshot("cp-1", ["B"])})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.strategy, RecoveryStrategy.HANDLER)
        self.assertEqual(plan.reason_code, "REPLAY_SAFETY_UNASSESSED")
        self.assertEqual(plan.recovery_point_id, point.recovery_point_id)
        self.assertEqual(plan.checkpoint_id, "cp-1")
        self.assertEqual(plan.next_nodes, ["B"])

    async def test_duplicate_observation_keeps_completed_boundary(self) -> None:
        """同一 checkpoint 的异常 observation 不能覆盖 completed 边界。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        first = await self._insert_point("rp-completed", "cp-1", "A", ["B"])
        await self._insert_point(
            "rp-failure-observation",
            "cp-1",
            None,
            ["B"],
            captured_offset=timedelta(seconds=1),
        )
        graph = _GraphDouble({"cp-1": self._snapshot("cp-1", ["B"])})

        plan = await RecoveryCoordinator().prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.recovery_point_id, first.recovery_point_id)
        self.assertEqual(plan.reason_code, "REPLAY_SAFETY_UNASSESSED")

    async def test_latest_failure_observation_is_skipped_for_earlier_boundary(self) -> None:
        """最新失败和终点观察应被跳过，继续寻找较早的成功边界。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        first = await self._insert_point("rp-a", "cp-a", "A", ["B"])
        await self._insert_point(
            "rp-b-failed",
            "cp-b",
            None,
            ["B"],
            state_status="failed",
            captured_offset=timedelta(seconds=1),
        )
        await self._insert_point(
            "rp-end",
            "cp-end",
            "handle_failure",
            ["END"],
            state_status="failed",
            captured_offset=timedelta(seconds=2),
        )
        graph = _GraphDouble({"cp-a": self._snapshot("cp-a", ["B"])})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.recovery_point_id, first.recovery_point_id)

    async def test_checkpoint_gc_is_invalid_and_does_not_use_latest_thread_checkpoint(self) -> None:
        """RecoveryPoint 指向已 GC checkpoint 时必须返回明确无效结果。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point("rp-gc", "cp-gc", "A", ["B"])
        graph = _GraphDouble({"cp-gc": KeyError("checkpoint missing")})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.INVALID_RECOVERY_POINT)
        self.assertEqual(plan.reason_code, "CHECKPOINT_NOT_FOUND")
        self.assertEqual(len(graph.configs), 1)

    async def test_next_nodes_mismatch_is_invalid(self) -> None:
        """真实 snapshot.next 与索引不一致时不得继续 replay。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point("rp-next", "cp-next", "A", ["B"])
        graph = _GraphDouble({"cp-next": self._snapshot("cp-next", ["C"])})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.INVALID_RECOVERY_POINT)
        self.assertEqual(plan.reason_code, "CHECKPOINT_NEXT_MISMATCH")

    async def test_thread_mismatch_is_rejected_before_graph_lookup(self) -> None:
        """RecoveryPoint threadId 不一致时即使 checkpoint 存在也必须拒绝。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point(
            "rp-thread",
            "cp-thread",
            "A",
            ["B"],
            thread_id="other-thread",
        )
        graph = _GraphDouble({"cp-thread": self._snapshot("cp-thread", ["B"])})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.INVALID_RECOVERY_POINT)
        self.assertEqual(plan.reason_code, "CHECKPOINT_THREAD_MISMATCH")
        self.assertEqual(graph.configs, [])

    async def test_completed_source_is_not_recoverable_without_checkpoint_lookup(self) -> None:
        """COMPLETED source 不得读取历史 checkpoint。"""

        source = await self._insert_source(status=DurableExecutionStatus.COMPLETED)
        graph = _GraphDouble({})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.NOT_RECOVERABLE)
        self.assertEqual(plan.reason_code, "SOURCE_EXECUTION_COMPLETED")
        self.assertEqual(graph.configs, [])

    async def test_running_source_is_not_converged_by_coordinator(self) -> None:
        """RUNNING source 交给 P0.2 liveness 收敛，Coordinator 不自行改状态。"""

        source = await self._insert_source(status=DurableExecutionStatus.RUNNING)
        graph = _GraphDouble({})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.reason_code, "SOURCE_EXECUTION_STILL_RUNNING")
        self.assertEqual(plan.decision, RecoveryDecision.NOT_RECOVERABLE)
        self.assertEqual(graph.configs, [])

    async def test_awaiting_user_source_does_not_create_replay_plan(self) -> None:
        """AWAITING_USER source 必须保留结构化 interaction 语义。"""

        source = await self._insert_source(status=DurableExecutionStatus.AWAITING_USER)
        graph = _GraphDouble({})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.AWAITING_USER)
        self.assertEqual(plan.reason_code, "SOURCE_AWAITING_USER")
        self.assertEqual(graph.configs, [])

    async def test_checkpoint_interrupt_is_awaiting_user(self) -> None:
        """真实 checkpoint 的 interrupt 必须阻止通用 checkpoint replay。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point("rp-interrupt", "cp-interrupt", "A", ["B"])
        graph = _GraphDouble({
            "cp-interrupt": self._snapshot(
                "cp-interrupt",
                ["B"],
                tasks=(SimpleNamespace(interrupts=(SimpleNamespace(id="i-1"),)),),
            )
        })

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.AWAITING_USER)
        self.assertEqual(plan.reason_code, "CHECKPOINT_REQUIRES_USER_INPUT")

    async def test_application_planning_stale_status_without_interrupt_is_not_awaiting(self) -> None:
        """Application Planning 历史 status 不能代替真实 Native Interrupt。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point("rp-stale", "cp-stale", "A", ["B"])
        snapshot = self._snapshot("cp-stale", ["B"])
        snapshot.values = {"status": "requires_user_input"}

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=_GraphDouble({"cp-stale": snapshot}),
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.reason_code, "REPLAY_SAFETY_UNASSESSED")

    async def test_lifecycle_drift_is_state_drift(self) -> None:
        """Workbench 的 lifecycle revision 变化必须保守阻止恢复。"""

        source = await self._insert_source(
            status=DurableExecutionStatus.INTERRUPTED,
            execution_kind="workbench",
        )
        await self._insert_point(
            "rp-life",
            "cp-life",
            "A",
            ["B"],
            lifecycle_revision=10,
        )
        graph = _GraphDouble({"cp-life": self._snapshot("cp-life", ["B"])})
        with patch(
            "app.services.execution_recovery_coordinator.load_application_lifecycle",
            return_value=SimpleNamespace(revision=12),
        ):
            plan = await prepare_continue(
                workspace=str(self.workspace),
                source_run_id=source.run_id,
                graph=graph,
            )

        self.assertEqual(plan.decision, RecoveryDecision.STATE_DRIFT)
        self.assertEqual(plan.reason_code, "LIFECYCLE_DRIFT")

    async def test_application_planning_can_validate_without_lifecycle(self) -> None:
        """Application Planning 缺少 lifecycle 时仍可进入恢复安全评估。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point("rp-planning", "cp-planning", "A", ["B"])
        graph = _GraphDouble({"cp-planning": self._snapshot("cp-planning", ["B"])})
        with patch(
            "app.services.execution_recovery_coordinator.load_application_lifecycle",
            return_value=None,
        ):
            plan = await prepare_continue(
                workspace=str(self.workspace),
                source_run_id=source.run_id,
                graph=graph,
            )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.reason_code, "REPLAY_SAFETY_UNASSESSED")

    async def test_workspace_file_change_without_new_snapshot_is_detected(self) -> None:
        """文件未重新 inspect 时，当前磁盘 revision 变化也必须被发现。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        source_file = self.workspace / "a.py"
        source_file.write_text("print('initial')\n", encoding="utf-8")
        _files, revision = workspace_inventory(self.workspace)
        snapshot = {"workspace_revision": revision, "files": ["a.py"]}
        self._write_snapshot(revision, snapshot)
        await self._insert_point(
            "rp-workspace",
            "cp-workspace",
            "A",
            ["B"],
            workspace_revision=revision,
            workspace_snapshot_hash=snapshot_hash(snapshot),
        )
        source_file.write_text("print('changed-content')\n", encoding="utf-8")
        graph = _GraphDouble({"cp-workspace": self._snapshot("cp-workspace", ["B"])})
        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.STATE_DRIFT)
        self.assertEqual(plan.reason_code, "WORKSPACE_DRIFT")

    async def test_unchanged_workspace_revision_passes_drift_validation(self) -> None:
        """磁盘 revision 和精确 snapshot 均一致时才进入 replay safety 评估。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        source_file = self.workspace / "a.py"
        source_file.write_text("print('stable')\n", encoding="utf-8")
        _files, revision = workspace_inventory(self.workspace)
        snapshot = {"workspace_revision": revision, "files": ["a.py"]}
        self._write_snapshot(revision, snapshot)
        await self._insert_point(
            "rp-stable-workspace",
            "cp-stable-workspace",
            "A",
            ["B"],
            workspace_revision=revision,
            workspace_snapshot_hash=snapshot_hash(snapshot),
        )
        graph = _GraphDouble({
            "cp-stable-workspace": self._snapshot("cp-stable-workspace", ["B"]),
        })

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.reason_code, "REPLAY_SAFETY_UNASSESSED")
        self.assertEqual(plan.workspace_revision, revision)

    async def test_untracked_file_change_is_workspace_drift(self) -> None:
        """新增未跟踪文件时，当前 workspace revision 必须发生漂移。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        (self.workspace / "a.py").write_text("print('stable')\n", encoding="utf-8")
        _files, revision = workspace_inventory(self.workspace)
        snapshot = {"workspace_revision": revision, "files": ["a.py"]}
        self._write_snapshot(revision, snapshot)
        await self._insert_point(
            "rp-untracked",
            "cp-untracked",
            "A",
            ["B"],
            workspace_revision=revision,
            workspace_snapshot_hash=snapshot_hash(snapshot),
        )
        (self.workspace / "new-file.ts").write_text("export const added = true;\n", encoding="utf-8")
        graph = _GraphDouble({"cp-untracked": self._snapshot("cp-untracked", ["B"])})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.STATE_DRIFT)
        self.assertEqual(plan.reason_code, "WORKSPACE_DRIFT")

    async def test_missing_workspace_snapshot_requires_handler(self) -> None:
        """revision 一致但 snapshot 证据缺失时必须要求专用 Handler。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        (self.workspace / "a.py").write_text("print('stable')\n", encoding="utf-8")
        _files, revision = workspace_inventory(self.workspace)
        await self._insert_point(
            "rp-missing-snapshot",
            "cp-missing-snapshot",
            "A",
            ["B"],
            workspace_revision=revision,
            workspace_snapshot_hash="missing-hash",
        )
        graph = _GraphDouble({
            "cp-missing-snapshot": self._snapshot("cp-missing-snapshot", ["B"]),
        })

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.strategy, RecoveryStrategy.HANDLER)
        self.assertEqual(plan.reason_code, "WORKSPACE_SNAPSHOT_UNAVAILABLE")

    async def test_workspace_hash_without_revision_is_unverifiable(self) -> None:
        """只有 snapshot hash 而没有 revision 时不得猜测当前 cache。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point(
            "rp-hash-only",
            "cp-hash-only",
            "A",
            ["B"],
            workspace_snapshot_hash="hash-only",
        )
        graph = _GraphDouble({"cp-hash-only": self._snapshot("cp-hash-only", ["B"])})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.strategy, RecoveryStrategy.HANDLER)
        self.assertEqual(plan.reason_code, "WORKSPACE_STATE_UNVERIFIABLE")

    async def test_injected_safe_policy_produces_native_ready_plan(self) -> None:
        """注入明确的安全节点策略后才允许 READY_NATIVE。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point("rp-safe", "cp-safe", "A", ["B"])
        graph = _GraphDouble({"cp-safe": self._snapshot("cp-safe", ["B"])})

        coordinator = RecoveryCoordinator(
            replay_policies=[AllowNodePolicy({"B"})],
        )
        plan = await coordinator.prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.READY_NATIVE)
        self.assertEqual(plan.strategy, RecoveryStrategy.NATIVE_CHECKPOINT)
        self.assertEqual(plan.reason_code, "READY_FOR_NATIVE_REPLAY")

    async def test_missing_source_is_not_recoverable(self) -> None:
        """不存在的 sourceRunId 必须返回明确的不可恢复结果。"""

        graph = _GraphDouble({})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id="missing-run",
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.NOT_RECOVERABLE)
        self.assertEqual(plan.reason_code, "SOURCE_EXECUTION_NOT_FOUND")
        self.assertEqual(graph.configs, [])

    async def test_no_checkpoint_requires_handler_without_resume_fallback(self) -> None:
        """只有 ENTRY 或空历史时必须要求 Handler，不能猜测 resumeFrom。"""

        source = await self._insert_source(status=DurableExecutionStatus.INTERRUPTED)
        await self._insert_point(
            "rp-entry",
            None,
            None,
            ["A"],
            kind=RecoveryPointKind.ENTRY,
        )
        graph = _GraphDouble({})

        plan = await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=graph,
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.strategy, RecoveryStrategy.HANDLER)
        self.assertEqual(plan.reason_code, "NO_RECOVERY_POINT")
        self.assertIsNone(plan.checkpoint_id)
        self.assertEqual(graph.configs, [])

    async def _insert_source(
        self,
        *,
        status: DurableExecutionStatus,
        execution_kind: str = "application_planning",
    ) -> DurableExecutionRecord:
        """创建测试使用的单次 Durable Execution source 记录。"""

        now = self._captured_at
        source = DurableExecutionRecord(
            run_id="run-1",
            thread_id="thread-1",
            workspace=str(self.workspace),
            project_id="project-1",
            execution_kind=execution_kind,  # type: ignore[arg-type]
            workflow_scope=(
                "application_planning"
                if execution_kind == "application_planning"
                else "page"
            ),
            first_node="A",
            current_node="A",
            status=status,
            started_at=now,
            updated_at=now,
        )
        await insert_execution(source)
        return source

    def _write_snapshot(self, revision: str, snapshot: dict[str, object]) -> None:
        """把测试用的 revision snapshot 写入当前 inspector schema 路径。"""

        cache = self.workspace / ".xcodeagent" / "cache" / "workspace-snapshots"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"{revision}.{INSPECTOR_SCHEMA_VERSION}.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")

    async def _insert_point(
        self,
        point_id: str,
        checkpoint_id: str | None,
        completed_node: str | None,
        next_nodes: list[str],
        *,
        thread_id: str = "thread-1",
        kind: RecoveryPointKind | None = None,
        state_status: str = "running",
        lifecycle_revision: int | None = None,
        workspace_revision: str | None = None,
        workspace_snapshot_hash: str | None = None,
        captured_offset: timedelta = timedelta(0),
    ) -> RecoveryPoint:
        """写入测试需要的 RecoveryPoint，并用时间偏移控制历史顺序。"""

        point = RecoveryPoint(
            recovery_point_id=point_id,
            run_id="run-1",
            thread_id=thread_id,
            kind=kind or (
                RecoveryPointKind.CHECKPOINT
                if checkpoint_id
                else RecoveryPointKind.ENTRY
            ),
            checkpoint_id=checkpoint_id,
            checkpoint_ns="",
            graph_node=completed_node or (next_nodes[0] if next_nodes else None),
            completed_node=completed_node,
            next_nodes=next_nodes,
            phase=completed_node,
            state_status=state_status,
            lifecycle_revision=lifecycle_revision,
            workspace_revision=workspace_revision,
            workspace_snapshot_hash=workspace_snapshot_hash,
            captured_at=self._captured_at + captured_offset,
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        return point

    def _snapshot(
        self,
        checkpoint_id: str,
        next_nodes: list[str],
        *,
        tasks: tuple[object, ...] = (),
    ) -> SimpleNamespace:
        """构造包含精确 checkpoint 身份的最小 StateSnapshot double。"""

        return SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint_id,
                }
            },
            next=tuple(next_nodes),
            values={},
            tasks=tasks,
            metadata={},
        )


class ExecutionRecoveryWorkspaceHashTests(unittest.IsolatedAsyncioTestCase):
    """补充现有 snapshot hash 体系的最小回归覆盖。"""

    async def test_snapshot_hash_is_stable_for_same_payload(self) -> None:
        """相同 snapshot payload 必须产生相同 hash，供 drift 比较复用。"""

        snapshot = {"workspace_revision": "revision-1", "files": ["a.py"]}

        self.assertEqual(snapshot_hash(snapshot), snapshot_hash(dict(snapshot)))
