"""真实 LangGraph A→B→C Native Recovery replay 回归测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionLeaseStatus,
    RecoveryPlan,
    RecoveryExecutionError,
    RecoveryAttemptStatus,
    RecoveryLifecycleOwnershipMode,
)
from app.persistence.execution_recovery import (
    claim_native_recovery_attempt,
    execution_recovery_db_path,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    initialize_execution_recovery_store,
    insert_execution,
    update_recovery_attempt,
)
from app.services.backend_instance import current_backend_instance
from app.services.application_lifecycle import (
    create_application_lifecycle,
    handoff_application_planning_run_for_recovery,
    write_application_lifecycle,
)
from app.services.execution_recovery_executor import (
    finalize_handed_off_recovery_attempt,
)
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.workspace_inspector import workspace_inventory


class ReplayState(TypedDict, total=False):
    """声明真实测试 Graph 使用的最小状态。"""

    active_run_id: str
    active_thread_id: str
    execution_log: list[str]
    observability: dict[str, Any]
    resume_from: str


def _build_replay_graph(counters: dict[str, int]) -> tuple[Any, InMemorySaver]:
    """构造带真实 checkpoint 的 A→B→C 图，并在 A 后停止首轮执行。"""

    builder = StateGraph(ReplayState)

    def node(name: str):
        """创建一个只追加执行日志的异步 Graph 节点。"""

        async def run(state: ReplayState) -> dict[str, Any]:
            """记录当前节点执行次数并返回完整日志。"""

            counters[name] += 1
            return {
                "execution_log": [*state.get("execution_log", []), name],
            }

        return run

    for name in ("A", "B", "C"):
        builder.add_node(name, node(name))
    builder.add_edge(START, "A")
    builder.add_edge("A", "B")
    builder.add_edge("B", "C")
    builder.add_edge("C", END)
    checkpointer = InMemorySaver()
    return builder.compile(checkpointer=checkpointer, interrupt_after=["A"]), checkpointer


@dataclass(slots=True)
class ReplayRecoveryWorld:
    """保存 fresh v9 与 v8 migration 共用的真实 LangGraph durable 现场。"""

    workspace: Path
    counters: dict[str, int]
    graph: Any
    thread_id: str
    source_run_id: str
    child_run_id: str
    source_checkpoint_id: str
    source_checkpoint_ns: str
    source_lifecycle_revision: int


async def _prepare_handed_off_replay_world(
    workspace: Path,
    *,
    identity: str,
) -> ReplayRecoveryWorld:
    """创建停在 B entry 且 lifecycle 已 handoff 给 child 的真实 durable world。"""

    counters = {"A": 0, "B": 0, "C": 0}
    graph, _checkpointer = _build_replay_graph(counters)
    thread_id = f"{identity}-thread"
    source_run_id = f"{identity}-source"
    child_run_id = f"{identity}-child"
    source_config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(
        {
            "active_run_id": source_run_id,
            "active_thread_id": thread_id,
            "execution_log": [],
        },
        config=source_config,
    )
    source_snapshot = await graph.aget_state(source_config)
    source_identity = source_snapshot.config["configurable"]
    if tuple(source_snapshot.next) != ("B",):
        raise AssertionError("source checkpoint 必须精确停在 B entry。")
    if source_snapshot.values.get("active_run_id") != source_run_id:
        raise AssertionError("source checkpoint 必须属于 source DurableExecution。")
    source_checkpoint_id = str(source_identity["checkpoint_id"])
    source_checkpoint_ns = str(source_identity.get("checkpoint_ns") or "")
    lifecycle = create_application_lifecycle(
        application_id=f"{identity}-app",
        application_name="LangGraph Native Replay",
        initialization_thread_id=thread_id,
        active_run_id=source_run_id,
    ).model_copy(
        update={
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                threadId=thread_id,
            )
        }
    )
    lifecycle = write_application_lifecycle(
        workspace,
        lifecycle,
        expected_revision=0,
    )
    captured_at = datetime.now(timezone.utc)
    source = DurableExecutionRecord(
        run_id=source_run_id,
        thread_id=thread_id,
        workspace=str(workspace),
        project_id=None,
        execution_kind="application_planning",
        workflow_scope="application_planning",
        first_node="A",
        current_node="B",
        status=DurableExecutionStatus.INTERRUPTED,
        started_at=captured_at,
        updated_at=captured_at,
        ended_at=captured_at,
    )
    await insert_execution(source)
    plan = RecoveryPlan(
        source_run_id=source_run_id,
        thread_id=thread_id,
        target_node="B",
        checkpoint_id=source_checkpoint_id,
        checkpoint_ns=source_checkpoint_ns,
        lifecycle_revision=lifecycle.revision,
    )
    await claim_native_recovery_attempt(
        source=source,
        plan=plan,
        new_run_id=child_run_id,
        owner_backend_instance_id="backend-before-restart",
        owner_pid=101,
        lease_ttl_seconds=60,
    )
    handoff_application_planning_run_for_recovery(
        workspace,
        source_run_id=source_run_id,
        new_run_id=child_run_id,
        thread_id=thread_id,
        expected_lifecycle_revision=lifecycle.revision,
    )
    await update_recovery_attempt(
        workspace=workspace,
        new_run_id=child_run_id,
        status=RecoveryAttemptStatus.HANDED_OFF,
    )
    return ReplayRecoveryWorld(
        workspace=workspace,
        counters=counters,
        graph=graph,
        thread_id=thread_id,
        source_run_id=source_run_id,
        child_run_id=child_run_id,
        source_checkpoint_id=source_checkpoint_id,
        source_checkpoint_ns=source_checkpoint_ns,
        source_lifecycle_revision=lifecycle.revision,
    )


def _rewrite_attempt_as_v8(world: ReplayRecoveryWorld) -> None:
    """把已 handoff 的 canonical row 原样写回真实 v8 表供 migration 使用。"""

    connection = sqlite3.connect(execution_recovery_db_path(world.workspace))
    try:
        connection.executescript(
            """
            DROP INDEX idx_recovery_attempts_source;
            DROP INDEX idx_recovery_attempts_status;
            DROP INDEX idx_recovery_attempts_active_source;
            ALTER TABLE recovery_attempts RENAME TO recovery_attempts_v9_seed;
            CREATE TABLE recovery_attempts (
                new_run_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                source_authority_kind TEXT NOT NULL,
                source_authority_sha256 TEXT,
                source_stage TEXT,
                source_lifecycle_revision INTEGER,
                source_recovery_point_id TEXT,
                source_checkpoint_id TEXT,
                source_checkpoint_ns TEXT NOT NULL DEFAULT '',
                replay_checkpoint_id TEXT,
                replay_checkpoint_ns TEXT NOT NULL DEFAULT '',
                strategy TEXT NOT NULL,
                lifecycle_ownership_mode TEXT NOT NULL DEFAULT 'source_owned',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                handed_off_at TEXT,
                started_at TEXT,
                failed_at TEXT,
                failure_code TEXT,
                source_status TEXT,
                source_failure_sha256 TEXT
            );
            """
        )
        seed = connection.execute(
            "SELECT * FROM recovery_attempts_v9_seed WHERE new_run_id = ?",
            (world.child_run_id,),
        ).fetchone()
        if seed is None:
            raise AssertionError("缺少用于构造 v8 migration 的 HANDED_OFF seed row。")
        connection.execute(
            """
            INSERT INTO recovery_attempts VALUES (
                ?, ?, ?, 'checkpoint', NULL, NULL, ?, NULL, ?, ?, NULL, '',
                'native_checkpoint', ?, 'handed_off', ?, ?, NULL, NULL, NULL,
                ?, ?
            )
            """,
            (
                seed[0], seed[1], seed[2], seed[5], seed[3], seed[4],
                seed[6], seed[8], seed[9], seed[13], seed[14],
            ),
        )
        connection.execute("DROP TABLE recovery_attempts_v9_seed")
        connection.execute(
            "UPDATE recovery_meta SET value = '8' WHERE key = 'schema_version'"
        )
        connection.commit()
    finally:
        connection.close()


async def _finalize_and_run_replay(
    world: ReplayRecoveryWorld,
    *,
    backend_instance_id: str,
) -> tuple[Any, Any, DurableExecutionRecord | None, Any]:
    """由指定新 Backend 完成真实 fork、运行 B/C，并返回 durable 结果。"""

    with patch(
        "app.services.execution_recovery_executor.current_backend_instance",
        return_value=SimpleNamespace(instance_id=backend_instance_id, pid=202),
    ):
        context = await finalize_handed_off_recovery_attempt(
            workspace=str(world.workspace),
            new_run_id=world.child_run_id,
            graph=world.graph,
        )
    await world.graph.ainvoke(None, config=context.fork_config)
    await stop_execution_heartbeat(context.heartbeat_task)
    attempt = await get_recovery_attempt(world.workspace, world.child_run_id)
    durable_child = await get_execution(world.workspace, world.child_run_id)
    child_lease = await get_execution_lease(world.workspace, world.child_run_id)
    return context, attempt, durable_child, child_lease


class ExecutionRecoveryLangGraphNativeTests(unittest.IsolatedAsyncioTestCase):
    """验证 Native fork 从真实 A checkpoint 继续执行 B/C。"""

    async def test_real_state_graph_replay_does_not_repeat_completed_node(self) -> None:
        """真实 checkpoint fork 后 A 只执行一次，B/C 从 child 继续。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            world = await _prepare_handed_off_replay_world(
                workspace,
                identity="langgraph-native-replay",
            )
            context, attempt, durable_child, child_lease = await _finalize_and_run_replay(
                world,
                backend_instance_id="backend-after-restart",
            )
            source_after = await world.graph.aget_state(
                {
                    "configurable": {
                        "thread_id": world.thread_id,
                        "checkpoint_ns": world.source_checkpoint_ns,
                        "checkpoint_id": world.source_checkpoint_id,
                    }
                }
            )

        self.assertEqual(world.counters, {"A": 1, "B": 1, "C": 1})
        self.assertEqual(source_after.values.get("active_run_id"), world.source_run_id)
        self.assertEqual(context.fork_snapshot.values.get("active_run_id"), world.child_run_id)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.status, RecoveryAttemptStatus.STARTED)
        self.assertIsNotNone(durable_child)
        assert durable_child is not None
        self.assertEqual(durable_child.status, DurableExecutionStatus.RUNNING)
        self.assertIsNotNone(child_lease)
        assert child_lease is not None
        self.assertEqual(
            child_lease.owner_backend_instance_id,
            "backend-after-restart",
        )

    async def test_v8_handed_off_migration_finalizes_real_checkpoint(self) -> None:
        """v8 HANDED_OFF row 升级后必须由新 Backend 从原 checkpoint 完成真实 fork。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            world = await _prepare_handed_off_replay_world(
                workspace,
                identity="langgraph-v8-migration",
            )
            _rewrite_attempt_as_v8(world)
            database_path = execution_recovery_db_path(workspace)
            connection = sqlite3.connect(database_path)
            try:
                v8_row = connection.execute(
                    """
                    SELECT strategy, source_authority_kind, source_checkpoint_id,
                           source_checkpoint_ns, status
                    FROM recovery_attempts
                    WHERE new_run_id = ?
                    """,
                    (world.child_run_id,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(
                v8_row,
                (
                    "native_checkpoint",
                    "checkpoint",
                    world.source_checkpoint_id,
                    "",
                    "handed_off",
                ),
            )

            await initialize_execution_recovery_store(workspace)
            migrated = await get_recovery_attempt(workspace, world.child_run_id)
            connection = sqlite3.connect(database_path)
            try:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(recovery_attempts)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM recovery_meta WHERE key = 'schema_version'"
                ).fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(migrated)
            assert migrated is not None
            self.assertEqual(schema_version, ("9",))
            self.assertEqual(
                columns
                & {
                    "strategy",
                    "source_authority_kind",
                    "source_authority_sha256",
                    "source_stage",
                    "source_recovery_point_id",
                    "replay_checkpoint_id",
                    "replay_checkpoint_ns",
                },
                set(),
            )
            self.assertEqual(migrated.status, RecoveryAttemptStatus.HANDED_OFF)
            self.assertEqual(migrated.source_checkpoint_id, world.source_checkpoint_id)
            self.assertEqual(migrated.source_checkpoint_ns, "")
            self.assertEqual(
                migrated.source_lifecycle_revision,
                world.source_lifecycle_revision,
            )
            self.assertEqual(
                migrated.lifecycle_ownership_mode,
                RecoveryLifecycleOwnershipMode.SOURCE_OWNED,
            )

            context, attempt, durable_child, child_lease = await _finalize_and_run_replay(
                world,
                backend_instance_id="backend-after-v8-migration",
            )

        fork_identity = context.fork_snapshot.config["configurable"]
        self.assertIsNotNone(attempt)
        self.assertIsNotNone(durable_child)
        self.assertIsNotNone(child_lease)
        assert attempt is not None
        assert durable_child is not None
        assert child_lease is not None
        self.assertEqual(attempt.status, RecoveryAttemptStatus.STARTED)
        self.assertEqual(
            child_lease.owner_backend_instance_id,
            "backend-after-v8-migration",
        )
        self.assertEqual(child_lease.status, ExecutionLeaseStatus.ACTIVE)
        self.assertEqual(durable_child.status, DurableExecutionStatus.RUNNING)
        self.assertNotEqual(fork_identity["checkpoint_id"], world.source_checkpoint_id)
        self.assertEqual(str(fork_identity.get("checkpoint_ns") or ""), "")
        self.assertEqual(tuple(context.fork_snapshot.next), ("B",))
        self.assertEqual(
            context.fork_snapshot.values.get("active_run_id"),
            world.child_run_id,
        )
        self.assertEqual(world.counters, {"A": 1, "B": 1, "C": 1})

    async def test_handed_off_restart_detects_workspace_drift_before_fork(self) -> None:
        """HANDED_OFF 重启遇到磁盘漂移时不能调用 aupdate_state。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            tracked_file = workspace / "tracked.txt"
            tracked_file.write_text("before", encoding="utf-8")
            await initialize_execution_recovery_store(workspace)
            _files, workspace_revision = workspace_inventory(workspace)
            lifecycle = create_application_lifecycle(
                application_id="workspace-drift-app",
                application_name="Workspace Drift",
                initialization_thread_id="workspace-drift-thread",
                active_run_id="workspace-drift-source",
            ).model_copy(
                update={
                    "initialization": ApplicationInitialization(
                        stage=ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
                        status=ApplicationLifecycleStatus.RUNNING,
                        threadId="workspace-drift-thread",
                    )
                }
            )
            lifecycle = write_application_lifecycle(
                workspace,
                lifecycle,
                expected_revision=0,
            )
            captured_at = datetime.now(timezone.utc)
            source = DurableExecutionRecord(
                run_id="workspace-drift-source",
                thread_id="workspace-drift-thread",
                workspace=str(workspace),
                project_id=None,
                execution_kind="application_planning",
                workflow_scope="application_planning",
                first_node="A",
                current_node="B",
                status=DurableExecutionStatus.INTERRUPTED,
                started_at=captured_at,
                updated_at=captured_at,
                ended_at=captured_at,
            )
            await insert_execution(source)
            plan = RecoveryPlan(
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                target_node="B",
                checkpoint_id="workspace-drift-checkpoint",
                checkpoint_ns="",
                lifecycle_revision=lifecycle.revision,
            )
            identity = current_backend_instance()
            child, _lease, _attempt = await claim_native_recovery_attempt(
                source=source,
                plan=plan,
                new_run_id="workspace-drift-child",
                owner_backend_instance_id=identity.instance_id,
                owner_pid=identity.pid,
                lease_ttl_seconds=60,
            )
            handed_off_lifecycle = handoff_application_planning_run_for_recovery(
                workspace,
                source_run_id=source.run_id,
                new_run_id=child.run_id,
                thread_id=source.thread_id,
                expected_lifecycle_revision=lifecycle.revision,
            )
            self.assertIsNotNone(handed_off_lifecycle)
            await update_recovery_attempt(
                workspace=workspace,
                new_run_id=child.run_id,
                status=RecoveryAttemptStatus.HANDED_OFF,
            )
            tracked_file.write_text("after", encoding="utf-8")

            class NoForkGraph:
                """提供真实 checkpoint identity 读取但记录 fork 是否发生。"""

                def __init__(self) -> None:
                    """初始化 fork 调用记录。"""

                    self.fork_called = False

                async def aget_state(self, config: dict[str, Any]) -> Any:
                    """返回与 RecoveryAttempt 一致且携带 workspace authority 的 checkpoint。"""

                    return SimpleNamespace(
                        config=config,
                        next=("B",),
                        tasks=(),
                        values={
                            "active_run_id": source.run_id,
                            "workspace_revision": workspace_revision,
                        },
                    )

                async def aget_state_history(self, config: dict[str, Any]):
                    """提供 latest source-owned root checkpoint，禁止 scan-back。"""

                    del config
                    yield await self.aget_state(
                        {
                            "configurable": {
                                "thread_id": source.thread_id,
                                "checkpoint_ns": "",
                                "checkpoint_id": plan.checkpoint_id,
                            }
                        }
                    )

                async def aupdate_state(self, _config: dict[str, Any], _updates: dict[str, Any]) -> Any:
                    """记录任何不应发生的 fork 写入。"""

                    self.fork_called = True
                    raise AssertionError("workspace drift must block fork")

            graph = NoForkGraph()
            with self.assertRaises(RecoveryExecutionError) as raised:
                await finalize_handed_off_recovery_attempt(
                    workspace=str(workspace),
                    new_run_id=child.run_id,
                    graph=graph,
                )
            failed_attempt = await get_recovery_attempt(workspace, child.run_id)
            failed_child = await get_execution(workspace, child.run_id)
            failed_lease = await get_execution_lease(workspace, child.run_id)

        self.assertEqual(
            raised.exception.code,
            "WORKSPACE_DRIFT",
            str(raised.exception),
        )
        self.assertFalse(graph.fork_called)
        self.assertIsNotNone(failed_attempt)
        self.assertIsNotNone(failed_child)
        self.assertIsNotNone(failed_lease)
        assert failed_attempt is not None
        assert failed_child is not None
        assert failed_lease is not None
        self.assertEqual(failed_attempt.status, RecoveryAttemptStatus.FINALIZATION_FAILED)
        self.assertEqual(failed_attempt.failure_code, "WORKSPACE_DRIFT")
        self.assertEqual(failed_child.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(failed_lease.status, ExecutionLeaseStatus.RELEASED)


__all__ = ["ExecutionRecoveryLangGraphNativeTests"]
