"""P0.4E Failed Node Replay 的 durable regression closure。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from typing import Any, TypedDict
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    RecoveryActionKind,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryPoint,
    RecoveryPointKind,
    RecoverySourceAuthorityKind,
    RecoveryStrategy,
)
from app.persistence.checkpoints import (
    close_workflow_checkpointer_for_workspace,
    workflow_checkpointer,
)
from app.persistence.execution_recovery import (
    get_execution,
    get_recovery_attempt,
    insert_execution,
    insert_recovery_point,
    list_executions_for_thread,
    list_recovery_points,
    list_recovery_attempts_for_thread,
    list_recovery_attempts_from_source,
    update_execution_node,
)
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
from app.services.application_lifecycle import (
    create_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)
from app.services.execution_recovery import (
    capture_recovery_point,
    observe_execution_failed,
    observe_execution_finished,
)
from app.services.execution_recovery_action_planner import (
    plan_failed_node_reentry_action,
    plan_interrupted_continue_action,
)
from app.services.execution_recovery_executor import (
    WorkflowReentryExecutor,
)
from app.services.execution_recovery_lineage import resolve_recovery_lineage_head
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.workflow_reentry import (
    FailureTargetResolver,
    InterruptedTargetResolver,
    recovery_plan_from_reentry,
)


class FakeModelNotFoundError(Exception):
    """提供可被生产 failure classifier 识别的模型 404。"""

    __module__ = "openai"

    def __init__(self, *, model: str) -> None:
        """保存 fake provider 返回的模型和 HTTP 状态。"""

        super().__init__(f"model not found: {model}")
        self.status_code = 404
        self.model = model


class ReplayState(TypedDict, total=False):
    """声明 Failed Node Replay Graph 使用的最小业务状态。"""

    request: str
    workspace: str
    active_run_id: str
    active_thread_id: str
    model: str
    provider: str
    application_name: str
    requirement_document: str
    product_plan: dict[str, Any]
    some_business_state: str
    failed_node_output: str
    execution_log: list[str]
    technical_plan: dict[str, Any]


class _Snapshot:
    """保存 Coordinator 与 Executor 所需的真实 checkpoint 视图。"""

    def __init__(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
        next_nodes: list[str],
        values: dict[str, Any],
    ) -> None:
        """构造带 root checkpoint identity 的 StateSnapshot double。"""

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
        self.metadata: dict[str, Any] = {}


class _SnapshotGraph:
    """为 selector/coordinator 测试按 checkpoint 身份返回固定快照。"""

    def __init__(self, snapshots: dict[str, _Snapshot]) -> None:
        """保存可验证的 checkpoint snapshot 映射。"""

        self._snapshots = snapshots
        self.configs: list[dict[str, Any]] = []

    async def aget_state(self, config: dict[str, Any]) -> _Snapshot:
        """按精确 checkpointId 返回快照，拒绝隐式 latest 查询。"""

        self.configs.append(config)
        configurable = config["configurable"]
        checkpoint_id = str(configurable["checkpoint_id"])
        return self._snapshots[checkpoint_id]

    async def aget_state_history(self, _config: dict[str, Any]):
        """按 newest-first 提供真实 committed history 的测试替身。"""

        for snapshot in reversed(tuple(self._snapshots.values())):
            yield snapshot


def _settings(model_name: str, *, provider: str = "openai") -> Settings:
    """构造测试运行时当前配置，不读取外部环境或调用 provider。"""

    return Settings(
        model_base_url="https://model.test.invalid/v1",
        model_api_key="test-key",
        model_name=model_name,
        model_provider=provider,
        checkpoint_db_path="",
        execution_recovery_heartbeat_seconds=0.01,
        execution_recovery_lease_ttl_seconds=60.0,
    )


def _failure(
    *,
    model: str | None = "deepseek-xxx",
    origin: ExecutionFailureOrigin = ExecutionFailureOrigin.MODEL_CALL,
    replay_compatible: bool = True,
    http_status: int | None = 404,
) -> ExecutionFailureEvidence:
    """构造测试使用的结构化失败证据。"""

    return ExecutionFailureEvidence(
        origin=origin,
        code="MODEL_NOT_FOUND",
        operation="technical_planning",
        provider="deepseek",
        model=model,
        http_status=http_status,
        replay_compatible=replay_compatible,
        diagnostic_message="model not found",
    )


_DEFAULT_FAILURE = object()


class FailedNodeReplayDurableHarness:
    """提供可重建 Graph、SQLite checkpoint 与 Workbench lifecycle 的测试环境。"""

    def __init__(
        self,
        *,
        source_status: DurableExecutionStatus = DurableExecutionStatus.FAILED,
        failure: ExecutionFailureEvidence | None | object = _DEFAULT_FAILURE,
    ) -> None:
        """初始化隔离 workspace，并保存可动态切换的当前模型配置。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)
        self.thread_id = "p0-4e-failed-node-thread"
        self.project_id = "p0-4e-failed-node-project"
        self.source_run_id = "failed-node-source-a"
        self.source_status = source_status
        self.current_model = "deepseek-xxx"
        self.current_provider = "deepseek"
        self.source_failure: ExecutionFailureEvidence | None = (
            _failure() if failure is _DEFAULT_FAILURE else failure  # type: ignore[assignment]
        )
        self.calls: list[tuple[str, str, str]] = []
        self.graph: Any | None = None
        self.checkpointer: AsyncSqliteSaver | None = None
        self.source: DurableExecutionRecord | None = None
        self.source_point: RecoveryPoint | None = None
        self.heartbeat_tasks: list[asyncio.Task[None] | None] = []

    def settings(self) -> Settings:
        """返回当前 Backend 重启后应使用的 Settings 快照。"""

        return _settings(self.current_model, provider=self.current_provider)

    def close(self) -> None:
        """释放隔离临时目录，SQLite 连接由异步清理函数负责关闭。"""

        self.temporary_workspace.cleanup()

    async def start(self) -> None:
        """写入 lifecycle、执行 source 失败和 exact predecessor RecoveryPoint。"""

        self._seed_workbench_lifecycle()
        await self._build_graph()
        assert self.graph is not None
        source_config = {"configurable": {"thread_id": self.thread_id}}
        try:
            await self.graph.ainvoke(self._initial_state(), config=source_config)
        except FakeModelNotFoundError:
            pass
        snapshot = await self.graph.aget_state(source_config)
        configurable = snapshot.config["configurable"]
        checkpoint_id = str(configurable["checkpoint_id"])
        now = datetime.now(timezone.utc)
        self.source = DurableExecutionRecord(
            run_id=self.source_run_id,
            thread_id=self.thread_id,
            workspace=str(self.workspace),
            project_id=self.project_id,
            execution_kind="workbench",
            workflow_scope="application",
            first_node="technical_planning",
            current_node="technical_planning",
            status=self.source_status,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=(
                self.source_failure
                if self.source_status is DurableExecutionStatus.FAILED
                else None
            ),
        )
        await insert_execution(self.source)
        lifecycle = self._load_lifecycle()
        self.source_point = await insert_recovery_point(
            workspace=self.workspace,
            point=RecoveryPoint(
                recovery_point_id="failed-node-source-point",
                run_id=self.source_run_id,
                thread_id=self.thread_id,
                kind=RecoveryPointKind.CHECKPOINT,
                checkpoint_id=checkpoint_id,
                checkpoint_ns="",
                graph_node="technical_planning",
                next_nodes=["technical_planning"],
                phase="technical_planning",
                state_status="running",
                lifecycle_revision=lifecycle.revision,
                captured_at=now,
            ),
        )

    async def restart_backend_graph(self) -> None:
        """关闭第一套 checkpoint connection，再从同一 SQLite 文件重建 Graph。"""

        self.graph = None
        await close_workflow_checkpointer_for_workspace(
            workspace=str(self.workspace),
            project_id=self.project_id,
        )
        await self._build_graph()

    async def _build_graph(self) -> None:
        """使用当前 workspace 的 SQLite checkpointer 创建新的 Graph 实例。"""

        self.checkpointer = await workflow_checkpointer(
            workspace=str(self.workspace),
            project_id=self.project_id,
        )
        builder = StateGraph(ReplayState)

        async def technical_planning(state: ReplayState) -> dict[str, Any]:
            """记录当前 runtime 模型并模拟失败或成功的 Technical Planning。"""

            settings = Settings.from_env()
            run_id = str(state.get("active_run_id") or "")
            self.calls.append((settings.model_name, run_id, "technical_planning"))
            if settings.model_name.startswith("deepseek"):
                raise FakeModelNotFoundError(model=settings.model_name)
            return {
                "execution_log": [*state.get("execution_log", []), "technical_planning"],
                "technical_plan": {"model": settings.model_name, "provider": settings.model_provider},
            }

        builder.add_node("technical_planning", technical_planning)
        builder.add_edge(START, "technical_planning")
        builder.add_edge("technical_planning", END)
        self.graph = builder.compile(checkpointer=self.checkpointer)

    def _seed_workbench_lifecycle(self) -> None:
        """把 source 放入 READY_FOR_WORKBENCH，使 Native handoff 可真实校验 ownership。"""

        lifecycle = create_application_lifecycle(
            application_id=self.project_id,
            application_name="P0.4E Replay",
            initialization_thread_id=self.thread_id,
            active_run_id=self.source_run_id,
        ).model_copy(
            update={
                "initialization": ApplicationInitialization(
                    stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    status=ApplicationLifecycleStatus.COMPLETED,
                    threadId=self.thread_id,
                )
            }
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)
        start_workbench_execution(
            self.workspace,
            scope="application",
            target_id=self.project_id,
            page_id=None,
            thread_id=self.thread_id,
            run_id=self.source_run_id,
            phase="technical_planning",
        )

    def _load_lifecycle(self) -> Any:
        """读取当前 lifecycle，供 RecoveryPoint 固化 revision。"""

        from app.services.application_lifecycle import load_application_lifecycle

        lifecycle = load_application_lifecycle(self.workspace)
        if lifecycle is None:
            raise AssertionError("test lifecycle was not persisted")
        return lifecycle

    def _initial_state(self) -> ReplayState:
        """返回含历史模型诊断字段和正式业务状态的 source 输入。"""

        return {
            "request": "执行 Technical Planning",
            "workspace": str(self.workspace),
            "active_run_id": self.source_run_id,
            "active_thread_id": self.thread_id,
            "model": "deepseek-xxx",
            "provider": "deepseek",
            "application_name": "P0.4E Replay",
            "requirement_document": "confirmed requirement",
            "product_plan": {"confirmed": True},
            "some_business_state": "keep-me",
            "execution_log": [],
        }

    async def resolve_action(self) -> tuple[Any, Any]:
        """经由当前 Workflow authority 和薄 Action Planner 解析 Backend action。"""

        if self.graph is None or self.source is None:
            raise AssertionError("harness is not started")
        source = await get_execution(self.workspace, self.source.run_id)
        if source is None:
            raise AssertionError("source execution disappeared")
        if source.status is DurableExecutionStatus.FAILED:
            try:
                reentry_plan = await FailureTargetResolver().resolve(
                    workspace=str(self.workspace),
                    source=source,
                    graph=self.graph,
                )
            except RecoveryExecutionError as exc:
                action_plan = plan_failed_node_reentry_action(
                    workspace=str(self.workspace),
                    source=source,
                    error=exc,
                )
            else:
                action_plan = plan_failed_node_reentry_action(
                    workspace=str(self.workspace),
                    source=source,
                    reentry_plan=reentry_plan,
                )
        elif source.status is DurableExecutionStatus.INTERRUPTED:
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace),
                source=source,
                graph=self.graph,
            )
            if resolution.kind == "continue" and resolution.reentry_plan is not None:
                action_plan = plan_interrupted_continue_action(
                    workspace=str(self.workspace),
                    source=source,
                    reentry_plan=resolution.reentry_plan,
                )
            else:
                action_plan = plan_interrupted_continue_action(
                    workspace=str(self.workspace),
                    source=source,
                    error=RecoveryExecutionError(
                        resolution.reason_code,
                        resolution.reason,
                    ),
                )
        else:
            action_plan = plan_failed_node_reentry_action(
                workspace=str(self.workspace),
                source=source,
                error=RecoveryExecutionError(
                    "RECOVERY_SOURCE_NOT_ACTIONABLE",
                    "当前 source 不是 FAILED 或 INTERRUPTED。",
                ),
            )
        return source, action_plan

    async def run_child(self, context: Any) -> None:
        """从真实 fork checkpoint 执行 child，并登记 heartbeat 清理任务。"""

        self.heartbeat_tasks.append(context.heartbeat_task)
        assert self.graph is not None
        await self.graph.ainvoke(None, config=context.fork_config)


class FailedNodeReplaySelectorTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 failed-node predecessor selector 和 action planner 的 fail-closed 边界。"""

    async def asyncSetUp(self) -> None:
        """创建隔离 workspace 和统一的 failed execution 事实。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)
        self.thread_id = "selector-thread"
        self.source_run_id = "selector-source"
        lifecycle = create_application_lifecycle(
            application_id="selector-application",
            application_name="Selector Recovery",
            initialization_thread_id=self.thread_id,
        ).model_copy(
            update={
                "initialization": ApplicationInitialization(
                    stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    status=ApplicationLifecycleStatus.COMPLETED,
                    threadId=self.thread_id,
                )
            }
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)
        now = datetime.now(timezone.utc)
        self.source = DurableExecutionRecord(
            run_id=self.source_run_id,
            thread_id=self.thread_id,
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench",
            workflow_scope="application",
            first_node="technical_planning",
            current_node="technical_planning",
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=_failure(),
        )
        await insert_execution(self.source)

    async def asyncTearDown(self) -> None:
        """释放 selector 测试 workspace。"""

        self.temporary_workspace.cleanup()

    async def _point(
        self,
        point_id: str,
        checkpoint_id: str,
        next_nodes: list[str],
        offset: int,
    ) -> RecoveryPoint:
        """写入可控捕获顺序的 checkpoint RecoveryPoint。"""

        point = RecoveryPoint(
            recovery_point_id=point_id,
            run_id=self.source_run_id,
            thread_id=self.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id=checkpoint_id,
            checkpoint_ns="",
            graph_node=next_nodes[0] if next_nodes else None,
            next_nodes=next_nodes,
            captured_at=datetime.fromtimestamp(1_000 + offset, tz=timezone.utc),
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        return point

    def _graph(self, checkpoint_ids: list[str]) -> _SnapshotGraph:
        """为给定 checkpoint 列表构造一致 snapshot double。"""

        return _SnapshotGraph(
            {
                checkpoint_id: _Snapshot(
                    thread_id=self.thread_id,
                    checkpoint_id=checkpoint_id,
                    next_nodes=["technical_planning"],
                    values={"request": "test", "active_run_id": self.source_run_id},
                )
                for checkpoint_id in checkpoint_ids
            }
        )

    async def test_failed_execution_selects_exact_failed_node_predecessor(self) -> None:
        """失败 execution 必须由 exact predecessor 驱动 retry_failed_node action。"""

        await self._point("point-a", "checkpoint-a", ["requirement"], 1)
        predecessor = await self._point(
            "point-b", "checkpoint-b", ["technical_planning"], 2
        )
        await self._point("point-c", "checkpoint-c", ["other_node"], 3)
        graph = self._graph(["checkpoint-b"])
        reentry_plan = await FailureTargetResolver().resolve(
            workspace=str(self.workspace),
            source=self.source,
            graph=graph,
        )
        recovery_plan = recovery_plan_from_reentry(reentry_plan)
        action_plan = plan_failed_node_reentry_action(
            workspace=str(self.workspace),
            source=self.source,
            reentry_plan=reentry_plan,
        )

        self.assertEqual(recovery_plan.checkpoint_id, predecessor.checkpoint_id)
        self.assertEqual(recovery_plan.reason_code, "FAILED_NODE_REENTRY_READY")
        self.assertIsNotNone(action_plan.primary_action)
        assert action_plan.primary_action is not None
        self.assertEqual(action_plan.primary_action.kind, RecoveryActionKind.RETRY_FAILED_NODE)

    async def test_newer_unrelated_checkpoint_does_not_override_predecessor(self) -> None:
        """更新更晚但无关的 checkpoint 不能取代失败节点 predecessor。"""

        predecessor = await self._point(
            "point-b", "checkpoint-b", ["technical_planning"], 1
        )
        await self._point("point-c", "checkpoint-c", ["other_node"], 2)
        graph = self._graph(["checkpoint-b"])
        reentry_plan = await FailureTargetResolver().resolve(
            workspace=str(self.workspace),
            source=self.source,
            graph=graph,
        )
        recovery_plan = recovery_plan_from_reentry(reentry_plan)

        self.assertEqual(recovery_plan.checkpoint_id, predecessor.checkpoint_id)
        self.assertEqual(recovery_plan.next_nodes, ["technical_planning"])

    async def test_multi_successor_checkpoint_is_not_failed_node_authority(self) -> None:
        """包含多个 successor 的 checkpoint 必须拒绝作为 failed-node authority。"""

        await self._point(
            "point-b",
            "checkpoint-b",
            ["technical_planning", "other_node"],
            1,
        )
        graph = _SnapshotGraph(
            {
                "checkpoint-b": _Snapshot(
                    thread_id=self.thread_id,
                    checkpoint_id="checkpoint-b",
                    next_nodes=["technical_planning", "other_node"],
                    values={"active_run_id": self.source_run_id},
                )
            }
        )
        with self.assertRaises(RecoveryExecutionError) as raised:
            await FailureTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=graph,
            )

        action_plan = plan_failed_node_reentry_action(
            workspace=str(self.workspace),
            source=self.source,
            error=raised.exception,
        )
        self.assertEqual(action_plan.reason_code, "NODE_ENTRY_AUTHORITY_MISSING")
        self.assertIsNone(action_plan.primary_action)

    async def test_missing_predecessor_never_falls_back_to_latest_checkpoint(self) -> None:
        """找不到 exact predecessor 时不得把 latest checkpoint 伪装成 Native Replay。"""

        await self._point("point-a", "checkpoint-a", ["requirement"], 1)
        await self._point("point-c", "checkpoint-c", ["page_design"], 2)
        await self._point("point-d", "checkpoint-d", ["build"], 3)
        graph = self._graph([])
        with self.assertRaises(RecoveryExecutionError) as raised:
            await FailureTargetResolver().resolve(
                workspace=str(self.workspace),
                source=self.source,
                graph=graph,
            )

        action_plan = plan_failed_node_reentry_action(
            workspace=str(self.workspace),
            source=self.source,
            error=raised.exception,
        )
        self.assertEqual(action_plan.reason_code, "NODE_ENTRY_AUTHORITY_MISSING")
        self.assertIsNone(action_plan.primary_action)


class FailedNodeReplayExecutionTests(unittest.IsolatedAsyncioTestCase):
    """覆盖真实 SQLite restart、Native fork、模型 authority 和 recovery lineage。"""

    async def asyncSetUp(self) -> None:
        """创建会在每个测试中销毁并重建的 durable harness。"""

        self.harness = FailedNodeReplayDurableHarness()
        self.addAsyncCleanup(self._cleanup_harness)

    async def _cleanup_harness(self) -> None:
        """停止 recovery heartbeat、关闭 checkpoint connection 并释放 workspace。"""

        for task in self.harness.heartbeat_tasks:
            await stop_execution_heartbeat(task)
        await close_workflow_checkpointer_for_workspace(
            workspace=str(self.harness.workspace),
            project_id=self.harness.project_id,
        )
        self.harness.close()

    async def _start(self) -> None:
        """在动态 Settings patch 下启动第一套失败 runtime。"""

        with patch(
            "app.config.Settings.from_env",
            side_effect=self.harness.settings,
        ):
            await self.harness.start()

    async def _assert_default_failed_node_replay(
        self,
        failure: ExecutionFailureEvidence | None,
    ) -> None:
        """验证异常 FAILED replay 不依赖错误类型或注入式节点白名单。"""

        self.harness.close()
        self.harness = FailedNodeReplayDurableHarness(failure=failure)
        await self._start()
        source, action_plan = await self.harness.resolve_action()
        self.assertEqual(source.status, DurableExecutionStatus.FAILED)
        self.assertEqual(action_plan.reason_code, "FAILED_NODE_REENTRY_READY")
        self.assertIsNotNone(action_plan.primary_action)
        assert action_plan.primary_action is not None
        self.assertEqual(
            action_plan.primary_action.kind,
            RecoveryActionKind.RETRY_FAILED_NODE,
        )

    async def test_business_failure_uses_failed_node_replay(self) -> None:
        """业务失败最终逃出 Node 后仍必须按失败节点重放。"""

        await self._assert_default_failed_node_replay(
            _failure(
                origin=ExecutionFailureOrigin.BUSINESS,
                replay_compatible=False,
            )
        )

    async def test_unknown_failure_uses_failed_node_replay(self) -> None:
        """未知失败最终逃出 Node 后仍必须按失败节点重放。"""

        await self._assert_default_failed_node_replay(
            _failure(
                origin=ExecutionFailureOrigin.UNKNOWN,
                replay_compatible=False,
                http_status=None,
            )
        )

    async def test_failed_without_exception_evidence_is_not_default_replay(self) -> None:
        """FAILED 且缺失异常证据时不能自动进入 Failed Node Replay。"""

        self.harness.close()
        self.harness = FailedNodeReplayDurableHarness(failure=None)
        await self._start()
        source, action_plan = await self.harness.resolve_action()

        self.assertEqual(source.status, DurableExecutionStatus.FAILED)
        self.assertEqual(
            action_plan.reason_code,
            "FAILED_EXCEPTION_EVIDENCE_MISSING",
        )
        self.assertIsNone(action_plan.primary_action)

    async def test_forked_child_reenters_failed_node(self) -> None:
        """Native fork 必须产生新 child，并把 next 精确保留为失败节点。"""

        await self._start()
        assert self.harness.graph is not None
        self.harness.current_model = "mimo-v2.5-pro"
        with patch(
            "app.config.Settings.from_env",
            side_effect=self.harness.settings,
        ):
            context = await WorkflowReentryExecutor().prepare_failure_retry(
                workspace=str(self.harness.workspace),
                source_run_id=self.harness.source_run_id,
                graph=self.harness.graph,
            )
            self.harness.heartbeat_tasks.append(context.heartbeat_task)
            self.assertNotEqual(context.new_run_id, self.harness.source_run_id)
            self.assertEqual(context.thread_id, self.harness.thread_id)
            self.assertEqual(context.child_execution.first_node, "technical_planning")
            self.assertEqual(tuple(context.fork_snapshot.next), ("technical_planning",))
            await self.harness.graph.ainvoke(None, config=context.fork_config)

        attempt = await get_recovery_attempt(self.harness.workspace, context.new_run_id)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.strategy, RecoveryStrategy.NATIVE_CHECKPOINT)
        self.assertEqual(attempt.status, RecoveryAttemptStatus.STARTED)
        self.assertEqual(
            self.harness.calls,
            [
                ("deepseek-xxx", self.harness.source_run_id, "technical_planning"),
                ("mimo-v2.5-pro", context.new_run_id, "technical_planning"),
            ],
        )

    async def test_predecessor_business_state_survives_failed_node_replay(self) -> None:
        """Replay 必须保留 predecessor 正式业务状态而不伪造失败节点产物。"""

        await self._start()
        assert self.harness.graph is not None
        self.harness.current_model = "mimo-v2.5-pro"
        with patch(
            "app.config.Settings.from_env",
            side_effect=self.harness.settings,
        ):
            context = await WorkflowReentryExecutor().prepare_failure_retry(
                workspace=str(self.harness.workspace),
                source_run_id=self.harness.source_run_id,
                graph=self.harness.graph,
            )
            self.harness.heartbeat_tasks.append(context.heartbeat_task)

        values = context.fork_snapshot.values
        self.assertEqual(values["requirement_document"], "confirmed requirement")
        self.assertEqual(values["product_plan"], {"confirmed": True})
        self.assertEqual(values["application_name"], "P0.4E Replay")
        self.assertEqual(values["some_business_state"], "keep-me")
        self.assertNotIn("failed_node_output", values)

    async def test_failed_node_replay_after_backend_restart_uses_current_model_configuration(
        self,
    ) -> None:
        """重启后 Failed Node Replay 必须只调用当前 MiMo，而不复用旧 DeepSeek。"""

        await self._start()
        with patch(
            "app.config.Settings.from_env",
            side_effect=self.harness.settings,
        ):
            await self.harness.restart_backend_graph()
        assert self.harness.graph is not None
        self.harness.current_model = "mimo-v2.5-pro"
        self.harness.current_provider = "mimo"
        with (
            patch(
                "app.config.Settings.from_env",
                side_effect=self.harness.settings,
            ),
            patch(
                "app.protocols.execution_recovery.workflow_graph_for_request",
                new=AsyncMock(return_value=self.harness.graph),
            ),
        ):
            source, action_plan = await self.harness.resolve_action()
            self.assertEqual(source.run_id, self.harness.source_run_id)
            self.assertIsNotNone(action_plan.primary_action)
            assert action_plan.primary_action is not None
            self.assertEqual(
                action_plan.primary_action.kind,
                RecoveryActionKind.RETRY_FAILED_NODE,
            )
            frames = [
                frame
                async for frame in build_execution_recovery_ag_ui_stream(
                    payload={
                        "forwardedProps": {
                            "workspaceRoot": str(self.harness.workspace),
                            "executionRecovery": {
                                "action": "execute",
                                "incidentId": action_plan.incident_id,
                                "actionId": action_plan.primary_action.action_id,
                            },
                        }
                    },
                )
            ]

        output = "".join(frames)
        self.assertIn('"type":"RUN_FINISHED"', output)
        self.assertEqual(
            [call for call in self.harness.calls if call[0].startswith("deepseek")],
            [("deepseek-xxx", self.harness.source_run_id, "technical_planning")],
        )
        mimo_calls = [
            call for call in self.harness.calls if call[0] == "mimo-v2.5-pro"
        ]
        self.assertEqual(len(mimo_calls), 1)
        self.assertTrue(mimo_calls[0][1].startswith("recovery-"))
        self.assertEqual(mimo_calls[0][2], "technical_planning")
        attempts = await list_recovery_attempts_for_thread(
            self.harness.workspace,
            thread_id=self.harness.thread_id,
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].status, RecoveryAttemptStatus.STARTED)
        self.assertEqual(attempts[0].strategy, RecoveryStrategy.NATIVE_CHECKPOINT)
        self.assertEqual(attempts[0].source_authority_kind, RecoverySourceAuthorityKind.CHECKPOINT)

    async def test_repeated_failed_node_replay_advances_from_current_lineage_head(self) -> None:
        """真实 Recovery Runtime 连续失败必须沿 A→B→C 推进。"""

        await self._start()
        assert self.harness.graph is not None
        self.harness.current_model = "deepseek-child-failing"
        source, first_action = await self.harness.resolve_action()
        assert first_action.primary_action is not None
        first_payload = {
            "forwardedProps": {
                "workspaceRoot": str(self.harness.workspace),
                "executionRecovery": {
                    "action": "execute",
                    "incidentId": first_action.incident_id,
                    "actionId": first_action.primary_action.action_id,
                },
            }
        }
        with (
            patch("app.config.Settings.from_env", side_effect=self.harness.settings),
            patch(
                "app.protocols.execution_recovery.workflow_graph_for_request",
                new=AsyncMock(return_value=self.harness.graph),
            ),
        ):
            first_frames = [
                frame
                async for frame in build_execution_recovery_ag_ui_stream(
                    payload=first_payload
                )
            ]
            attempts = await list_recovery_attempts_for_thread(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
            )
            self.assertEqual(len(attempts), 1)
            first_child_run_id = attempts[0].new_run_id
            child_points = await list_recovery_points(
                self.harness.workspace,
                first_child_run_id,
            )
            second_source, second_action = await self._resolve_current_action(
                first_child_run_id
            )
            self.assertEqual(second_source.run_id, first_child_run_id)
            self.assertEqual(second_action.primary_action.kind, RecoveryActionKind.RETRY_FAILED_NODE)
            self.harness.current_model = "mimo-v2.5-pro"
            second_context = await WorkflowReentryExecutor().prepare_failure_retry(
                workspace=str(self.harness.workspace),
                source_run_id=second_source.run_id,
                graph=self.harness.graph,
            )
            self.harness.heartbeat_tasks.append(second_context.heartbeat_task)
            await self.harness.graph.ainvoke(None, config=second_context.fork_config)
            await observe_execution_finished(
                workspace=str(self.harness.workspace),
                run_id=second_context.new_run_id,
                thread_id=self.harness.thread_id,
                workflow_scope="application",
                status=DurableExecutionStatus.COMPLETED,
            )

        self.assertIn('"type":"RUN_ERROR"', "".join(first_frames))
        self.assertTrue(
            any(
                point.run_id == first_child_run_id
                and point.thread_id == self.harness.thread_id
                and point.next_nodes == ["technical_planning"]
                and point.checkpoint_ns == ""
                for point in child_points
            )
        )
        attempts = await list_recovery_attempts_for_thread(
            self.harness.workspace,
            thread_id=self.harness.thread_id,
        )
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0].source_run_id, self.harness.source_run_id)
        self.assertEqual(attempts[1].source_run_id, first_child_run_id)
        self.assertNotEqual(attempts[1].source_run_id, self.harness.source_run_id)
        resolution = await resolve_recovery_lineage_head(
            self.harness.workspace,
            thread_id=self.harness.thread_id,
            execution_kind="workbench",
        )
        self.assertIsNotNone(resolution.head)
        assert resolution.head is not None
        self.assertEqual(resolution.head.run_id, second_context.new_run_id)

    async def _resolve_current_action(self, source_run_id: str) -> tuple[Any, Any]:
        """按指定当前 source 重新走 Workflow authority 与 Action Planner。"""

        assert self.harness.graph is not None
        source = await get_execution(self.harness.workspace, source_run_id)
        if source is None:
            raise AssertionError("lineage source disappeared")
        if source.status is not DurableExecutionStatus.FAILED:
            raise AssertionError("test helper only resolves failed-node actions")
        reentry_plan = await FailureTargetResolver().resolve(
            workspace=str(self.harness.workspace),
            source=source,
            graph=self.harness.graph,
        )
        action_plan = plan_failed_node_reentry_action(
            workspace=str(self.harness.workspace),
            source=source,
            reentry_plan=reentry_plan,
        )
        return source, action_plan

    async def test_public_execute_double_click_creates_one_failed_node_child(self) -> None:
        """同一 incident/action 并发执行只能 claim 一个 child execution。"""

        await self._start()
        assert self.harness.graph is not None
        _source, action_plan = await self.harness.resolve_action()
        assert action_plan.primary_action is not None
        payload = {
            "forwardedProps": {
                "workspaceRoot": str(self.harness.workspace),
                "executionRecovery": {
                    "action": "execute",
                    "incidentId": action_plan.incident_id,
                    "actionId": action_plan.primary_action.action_id,
                },
            }
        }

        async def no_op_runtime(**_kwargs: Any):
            """让并发协议测试停在 recovery transaction，不重复消费 child Graph。"""

            context = _kwargs.get("native_recovery_context")
            if context is not None:
                await stop_execution_heartbeat(context.heartbeat_task)
            yield "runtime-stub"

        with (
            patch(
                "app.config.Settings.from_env",
                side_effect=self.harness.settings,
            ),
            patch(
                "app.protocols.execution_recovery.workflow_graph_for_request",
                new=AsyncMock(return_value=self.harness.graph),
            ),
            patch(
                "app.protocols.execution_recovery.build_workflow_ag_ui_stream",
                side_effect=no_op_runtime,
            ),
        ):
            await asyncio.gather(
                self._consume_recovery_stream(payload),
                self._consume_recovery_stream(payload),
            )

        attempts = await list_recovery_attempts_for_thread(
            self.harness.workspace,
            thread_id=self.harness.thread_id,
        )
        executions = await list_executions_for_thread(
            self.harness.workspace,
            thread_id=self.harness.thread_id,
            execution_kind="workbench",
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            len([execution for execution in executions if execution.run_id != self.harness.source_run_id]),
            1,
        )

    async def _consume_recovery_stream(self, payload: dict[str, Any]) -> None:
        """消费一份公开 AG-UI recovery execute stream。"""

        _ = [
            frame
            async for frame in build_execution_recovery_ag_ui_stream(
                payload=payload,
            )
        ]

    async def test_stale_action_is_rejected_without_creating_child(self) -> None:
        """source authority 变化后旧 incident/action 必须 fail closed。"""

        await self._start()
        assert self.harness.graph is not None
        _source, action_plan = await self.harness.resolve_action()
        await update_execution_node(
            workspace=self.harness.workspace,
            run_id=self.harness.source_run_id,
            node_name="other_node",
        )
        payload = {
            "forwardedProps": {
                "workspaceRoot": str(self.harness.workspace),
                "executionRecovery": {
                    "action": "execute",
                    "incidentId": action_plan.incident_id,
                    "actionId": action_plan.primary_action.action_id
                    if action_plan.primary_action
                    else "",
                },
            }
        }
        with patch(
            "app.protocols.execution_recovery.workflow_graph_for_request",
            new=AsyncMock(return_value=self.harness.graph),
        ):
            frames = [
                frame
                async for frame in build_execution_recovery_ag_ui_stream(
                    payload=payload,
                )
            ]

        self.assertIn("STALE_RECOVERY_ACTION", "".join(frames))
        self.assertEqual(
            await list_recovery_attempts_from_source(
                self.harness.workspace,
                self.harness.source_run_id,
            ),
            [],
        )
        self.assertIsNone(await get_execution(self.harness.workspace, "recovery-child"))

    async def test_interrupted_native_checkpoint_recovery_remains_supported(self) -> None:
        """INTERRUPTED 的 valid checkpoint 仍可走 continue_checkpoint Native flow。"""

        self.harness.close()
        self.harness = FailedNodeReplayDurableHarness(
            source_status=DurableExecutionStatus.INTERRUPTED
        )
        await self._start()
        assert self.harness.graph is not None
        with patch(
            "app.config.Settings.from_env",
            side_effect=self.harness.settings,
        ):
            source, action_plan = await self.harness.resolve_action()
            self.assertEqual(source.status, DurableExecutionStatus.INTERRUPTED)
            self.assertIsNotNone(action_plan.primary_action)
            assert action_plan.primary_action is not None
            self.assertEqual(
                action_plan.primary_action.kind,
                RecoveryActionKind.CONTINUE_CHECKPOINT,
            )
            context = await WorkflowReentryExecutor().prepare_interrupted_continue(
                workspace=str(self.harness.workspace),
                source_run_id=source.run_id,
                graph=self.harness.graph,
            )
            self.harness.heartbeat_tasks.append(context.heartbeat_task)

        self.assertEqual(context.recovery_plan.strategy, RecoveryStrategy.NATIVE_CHECKPOINT)
        self.assertEqual(tuple(context.fork_snapshot.next), ("technical_planning",))
        attempt = await get_recovery_attempt(self.harness.workspace, context.new_run_id)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.source_status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(attempt.status, RecoveryAttemptStatus.STARTED)


__all__ = [
    "FailedNodeReplayDurableHarness",
    "FailedNodeReplaySelectorTests",
    "FailedNodeReplayExecutionTests",
]
