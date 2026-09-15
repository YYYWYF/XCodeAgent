"""Application Planning 真实模型失败进入 Native Recovery 的组合回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    RecoveryDecision,
    RecoveryActionKind,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.graph import application_planning_workflow
from app.graph.state import ProjectState
from app.persistence.execution_recovery import (
    get_execution,
    insert_execution,
    insert_recovery_point,
    list_recovery_points,
)
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
    write_application_lifecycle,
)
from app.services.application_planning_recovery_coordinator import (
    resolve_application_planning_recovery,
)
from app.services.execution_recovery_coordinator import prepare_continue
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_recovery_lineage import (
    RecoveryLineageState,
    resolve_recovery_lineage_head,
)
from app.services.execution_recovery_policies import (
    production_recovery_replay_policies,
)
from app.services.execution_recovery import observe_node_started as persist_node_started


class FakeModelConnectionError(Exception):
    """提供不依赖真实网络且能被生产 classifier 识别的模型连接异常。"""

    __module__ = "openai"

    def __init__(
        self,
        *,
        status_code: int = 503,
        model: str = "unavailable-model",
    ) -> None:
        """保存测试用 HTTP 状态和模型名称。"""

        super().__init__("test model failure")
        self.status_code = status_code
        self.model = model


def _settings_for(model_name: str) -> Settings:
    """构造只用于测试的当前模型配置，不访问真实环境或网络。"""

    return Settings(
        model_base_url="https://model.test.invalid/v1",
        model_api_key="test-key",
        model_name=model_name,
    )


def _build_wrapped_graph(node_names: tuple[str, ...]) -> Any:
    """用真实 Application Planning 节点包装器构造最小线性 StateGraph。"""

    builder = StateGraph(ProjectState)
    wrapper_names = {
        "requirements": "_requirements",
        "product_planning": "_product_planning",
        "ui_confirmation": "_ui_confirmation",
    }
    for node_name in node_names:
        builder.add_node(node_name, getattr(application_planning_workflow, wrapper_names[node_name]))
    builder.add_edge(START, node_names[0])
    for predecessor, successor in zip(node_names, node_names[1:]):
        builder.add_edge(predecessor, successor)
    builder.add_edge(node_names[-1], END)
    return builder.compile(checkpointer=InMemorySaver())


def _successful_requirements(_state: ProjectState) -> dict[str, Any]:
    """返回足以让真实 requirements 包装器推进阶段的成功结果。"""

    return {
        "phase": "requirements",
        "status": "completed",
        "requirement_spec": {
            "app_info": {"name": "恢复测试应用"},
            "confirmation_status": "confirmed",
        },
    }


def _successful_product_planning(_state: ProjectState) -> dict[str, Any]:
    """返回足以让真实 product_planning 包装器推进阶段的成功结果。"""

    return {
        "phase": "product_planning",
        "status": "completed",
        "product_plan": {
            "app": {"name": "恢复测试应用"},
            "pages": [],
        },
    }


async def _successful_ui_confirmation(_state: ProjectState) -> dict[str, Any]:
    """返回足以让真实 ui_confirmation 包装器完成本轮的成功结果。"""

    return {
        "phase": "ui_confirmation",
        "status": "completed",
        "ui_designs": {
            "confirmation_status": "skipped",
            "pages": [],
        },
    }


def _seed_lifecycle(workspace: Path, *, thread_id: str, run_id: str) -> None:
    """创建与测试 Graph 同线程、同 source run 的初始生命周期。"""

    lifecycle = create_application_lifecycle(
        application_id="app-recovery-test",
        application_name="恢复测试应用",
        initialization_thread_id=thread_id,
        active_run_id=run_id,
    )
    write_application_lifecycle(workspace, lifecycle, expected_revision=0)


def _runtime_payload(workspace: Path, *, thread_id: str, run_id: str) -> dict[str, Any]:
    """构造 application planning Runtime 使用的最小请求。"""

    return {
        "threadId": thread_id,
        "runId": run_id,
        "message": "执行模型失败恢复测试",
        "forwardedProps": {
            "workspaceRoot": str(workspace),
            "workflowScope": "application_planning",
        },
    }


async def _consume_runtime(graph: Any, payload: dict[str, Any]) -> list[str]:
    """消费一次完整 AG-UI Runtime 流，保留帧以确保 Graph 确实被驱动。"""

    return [
        frame
        async for frame in build_workflow_ag_ui_stream(
            graph=graph,
            payload=payload,
        )
    ]


async def _consume_native_runtime(graph: Any, context: Any) -> list[str]:
    """消费 Native Recovery child 的完整 Runtime 流并让 Durable 状态收口。"""

    return [
        frame
        async for frame in build_workflow_ag_ui_stream(
            graph=graph,
            payload={},
            native_recovery_context=context,
        )
    ]


async def _failure_point(
    workspace: Path,
    *,
    run_id: str,
    operation: str,
) -> RecoveryPoint:
    """读取指定模型失败现场的唯一未完成节点 checkpoint。"""

    points = await list_recovery_points(workspace, run_id)
    for point in reversed(points):
        if point.completed_node is None and point.next_nodes == [operation]:
            return point
    raise AssertionError(f"missing failure RecoveryPoint for {operation}")


async def _prepare_and_run_child(
    testcase: unittest.TestCase,
    *,
    workspace: Path,
    graph: Any,
    source: DurableExecutionRecord,
    snapshot: Any,
) -> Any:
    """执行生产 Projection、Coordinator、Native Executor 和 child Runtime。"""

    projection = await resolve_application_planning_recovery(
        workspace=str(workspace),
        thread_id=source.thread_id,
        graph=graph,
        snapshot=snapshot,
        lifecycle=load_application_lifecycle(workspace),
        source=source,
    )
    testcase.assertEqual(projection.classification, "ready_to_continue")
    testcase.assertTrue(projection.can_continue)
    action_plan = projection.recovery_action_plan
    testcase.assertIsNotNone(action_plan)
    assert action_plan is not None
    primary_action = action_plan.get("primaryAction")
    testcase.assertIsNotNone(primary_action)
    assert primary_action is not None
    testcase.assertEqual(
        primary_action["kind"],
        RecoveryActionKind.RETRY_FAILED_NODE.value,
    )

    plan = await prepare_continue(
        workspace=str(workspace),
        source_run_id=source.run_id,
        graph=graph,
        replay_policies=production_recovery_replay_policies(),
    )
    testcase.assertEqual(plan.decision, RecoveryDecision.READY_NATIVE)
    testcase.assertEqual(plan.reason_code, "FAILED_NODE_REENTRY_READY")
    testcase.assertEqual(plan.next_nodes, [source.current_node])
    snapshot_config = snapshot.config["configurable"]
    testcase.assertEqual(plan.checkpoint_id, snapshot_config["checkpoint_id"])
    testcase.assertEqual(plan.checkpoint_ns, snapshot_config.get("checkpoint_ns", ""))

    context = await prepare_native_recovery(
        workspace=str(workspace),
        source_run_id=source.run_id,
        graph=graph,
        replay_policies=production_recovery_replay_policies(),
    )
    testcase.assertNotEqual(context.new_run_id, source.run_id)
    testcase.assertEqual(context.thread_id, source.thread_id)
    await _consume_native_runtime(graph, context)
    return context


class ApplicationPlanningFailedModelRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证三类真实生成节点从模型 503 到 child replay 的完整链路。"""

    async def test_requirements_model_switch_uses_current_settings_on_child(self) -> None:
        """Requirements 503 后修改当前模型配置，child 必须读取新配置并成功。"""

        graph = _build_wrapped_graph(("requirements",))
        model_config = {"name": "unavailable-model"}
        called_models: list[str] = []

        def model_requirements(_state: ProjectState) -> dict[str, Any]:
            """按每次节点执行时的当前 Settings 模拟模型调用。"""

            model_name = Settings.from_env().model_name
            called_models.append(model_name)
            if model_name == "unavailable-model":
                raise FakeModelConnectionError(model=model_name)
            return _successful_requirements(_state)

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "failed-requirements-thread"
            source_run_id = "run-requirements-source"
            _seed_lifecycle(workspace, thread_id=thread_id, run_id=source_run_id)
            with patch(
                "app.config.Settings.from_env",
                side_effect=lambda: _settings_for(model_config["name"]),
            ), patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=model_requirements,
            ):
                await _consume_runtime(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id=thread_id,
                        run_id=source_run_id,
                    ),
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.status, DurableExecutionStatus.FAILED)
                self.assertEqual(source.current_node, "requirements")
                self.assertIsNotNone(source.failure)
                assert source.failure is not None
                self.assertEqual(source.failure.origin, ExecutionFailureOrigin.MODEL_CALL)
                self.assertEqual(source.failure.http_status, 503)
                self.assertTrue(source.failure.replay_compatible)
                self.assertEqual(source.failure.operation, "requirements")
                self.assertEqual(source.failure.diagnostic_message, "test model failure")

                point = await _failure_point(
                    workspace,
                    run_id=source_run_id,
                    operation="requirements",
                )
                snapshot = await graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": point.checkpoint_ns,
                            "checkpoint_id": point.checkpoint_id,
                        }
                    }
                )
                self.assertEqual(snapshot.next, ("requirements",))
                self.assertEqual(
                    snapshot.config["configurable"]["checkpoint_id"],
                    point.checkpoint_id,
                )
                self.assertEqual(
                    snapshot.config["configurable"].get("checkpoint_ns", ""),
                    point.checkpoint_ns,
                )

                model_config["name"] = "working-model"
                context = await _prepare_and_run_child(
                    self,
                    workspace=workspace,
                    graph=graph,
                    source=source,
                    snapshot=snapshot,
                )
                child = await get_execution(workspace, context.new_run_id)
                source_after = await get_execution(workspace, source_run_id)

        self.assertEqual(called_models, ["unavailable-model", "working-model"])
        self.assertIsNotNone(child)
        assert child is not None
        self.assertEqual(child.status, DurableExecutionStatus.COMPLETED)
        self.assertIsNotNone(source_after)
        assert source_after is not None
        self.assertEqual(source_after.status, DurableExecutionStatus.FAILED)
        self.assertEqual(context.thread_id, "failed-requirements-thread")

    async def test_failed_lineage_always_recovers_from_latest_child(self) -> None:
        """连续两次 503 后第三次成功，第二次恢复 source 必须是最新 child。"""

        graph = _build_wrapped_graph(("requirements",))
        model_config = {"name": "unavailable-model-a"}
        called_models: list[str] = []

        def model_requirements(_state: ProjectState) -> dict[str, Any]:
            """按当前模型名称决定本轮失败或成功，并记录调用顺序。"""

            model_name = Settings.from_env().model_name
            called_models.append(model_name)
            if model_name != "working-model":
                raise FakeModelConnectionError(model=model_name)
            return _successful_requirements(_state)

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "failed-lineage-thread"
            run_a = "run-lineage-a"
            _seed_lifecycle(workspace, thread_id=thread_id, run_id=run_a)
            with patch(
                "app.config.Settings.from_env",
                side_effect=lambda: _settings_for(model_config["name"]),
            ), patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=model_requirements,
            ):
                await _consume_runtime(
                    graph,
                    _runtime_payload(workspace, thread_id=thread_id, run_id=run_a),
                )
                source_a = await get_execution(workspace, run_a)
                self.assertIsNotNone(source_a)
                assert source_a is not None
                snapshot_a = await graph.aget_state({"configurable": {"thread_id": thread_id}})
                model_config["name"] = "unavailable-model-b"
                context_b = await _prepare_and_run_child(
                    self,
                    workspace=workspace,
                    graph=graph,
                    source=source_a,
                    snapshot=snapshot_a,
                )

                source_b = await get_execution(workspace, context_b.new_run_id)
                self.assertIsNotNone(source_b)
                assert source_b is not None
                self.assertEqual(source_b.status, DurableExecutionStatus.FAILED)
                self.assertIsNotNone(source_b.failure)
                assert source_b.failure is not None
                self.assertEqual(source_b.failure.operation, "requirements")

                snapshot_b = await graph.aget_state({"configurable": {"thread_id": thread_id}})
                lineage_b = await resolve_recovery_lineage_head(
                    str(workspace),
                    thread_id=thread_id,
                    execution_kind="application_planning",
                )
                self.assertEqual(lineage_b.state, RecoveryLineageState.RECOVERABLE_HEAD)
                self.assertIsNotNone(lineage_b.head)
                assert lineage_b.head is not None
                self.assertEqual(lineage_b.head.run_id, context_b.new_run_id)
                projection = await resolve_application_planning_recovery(
                    workspace=str(workspace),
                    thread_id=thread_id,
                    graph=graph,
                    snapshot=snapshot_b,
                    lifecycle=load_application_lifecycle(workspace),
                    source=source_a,
                    lineage_resolution=lineage_b,
                )
                self.assertEqual(projection.source_run_id, context_b.new_run_id)
                self.assertIsNotNone(projection.failure_diagnostic)
                assert projection.failure_diagnostic is not None
                self.assertEqual(
                    projection.failure_diagnostic["sourceRunId"],
                    context_b.new_run_id,
                )
                self.assertEqual(
                    projection.failure_diagnostic["model"],
                    "unavailable-model-b",
                )
                self.assertEqual(
                    projection.failure_diagnostic["message"],
                    "test model failure",
                )
                model_config["name"] = "working-model"
                context_c = await _prepare_and_run_child(
                    self,
                    workspace=workspace,
                    graph=graph,
                    source=lineage_b.head,
                    snapshot=snapshot_b,
                )

                source_a_after = await get_execution(workspace, run_a)
                source_b_after = await get_execution(workspace, context_b.new_run_id)
                child_c = await get_execution(workspace, context_c.new_run_id)
                lineage_c = await resolve_recovery_lineage_head(
                    str(workspace),
                    thread_id=thread_id,
                    execution_kind="application_planning",
                )

        self.assertEqual(
            called_models,
            ["unavailable-model-a", "unavailable-model-b", "working-model"],
        )
        self.assertNotEqual(run_a, context_b.new_run_id)
        self.assertNotEqual(context_b.new_run_id, context_c.new_run_id)
        self.assertEqual(context_c.thread_id, thread_id)
        self.assertIsNotNone(source_a_after)
        assert source_a_after is not None
        self.assertEqual(source_a_after.status, DurableExecutionStatus.FAILED)
        self.assertIsNotNone(source_b_after)
        assert source_b_after is not None
        self.assertEqual(source_b_after.status, DurableExecutionStatus.FAILED)
        self.assertIsNotNone(child_c)
        assert child_c is not None
        self.assertEqual(child_c.status, DurableExecutionStatus.COMPLETED)
        self.assertEqual(lineage_c.state, RecoveryLineageState.COMPLETED)
        self.assertIsNotNone(lineage_c.head)
        assert lineage_c.head is not None
        self.assertEqual(lineage_c.head.run_id, context_c.new_run_id)

    async def test_product_planning_failure_replays_only_product_planning(self) -> None:
        """ProductPlan 503 后 child 只能重放 product_planning，不能重跑 requirements。"""

        graph = _build_wrapped_graph(("requirements", "product_planning"))
        model_config = {"name": "unavailable-product-model"}
        counters = {"requirements": 0, "product_planning": 0}

        def requirements(_state: ProjectState) -> dict[str, Any]:
            """记录并返回成功的 Requirements 结果。"""

            counters["requirements"] += 1
            return _successful_requirements(_state)

        def product_planning(_state: ProjectState) -> dict[str, Any]:
            """记录 ProductPlan 调用并模拟首次失败、child 成功。"""

            counters["product_planning"] += 1
            model_name = Settings.from_env().model_name
            if model_name == "unavailable-product-model":
                raise FakeModelConnectionError(model=model_name)
            return _successful_product_planning(_state)

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "failed-product-thread"
            source_run_id = "run-product-source"
            _seed_lifecycle(workspace, thread_id=thread_id, run_id=source_run_id)
            with patch(
                "app.config.Settings.from_env",
                side_effect=lambda: _settings_for(model_config["name"]),
            ), patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=requirements,
            ), patch(
                "app.graph.application_planning_workflow.nodes.product_planning",
                side_effect=product_planning,
            ):
                await _consume_runtime(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id=thread_id,
                        run_id=source_run_id,
                    ),
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.status, DurableExecutionStatus.FAILED)
                self.assertEqual(source.current_node, "product_planning")
                self.assertIsNotNone(source.failure)
                assert source.failure is not None
                self.assertEqual(source.failure.operation, "product_planning")
                self.assertEqual(source.failure.origin, ExecutionFailureOrigin.MODEL_CALL)
                self.assertTrue(source.failure.replay_compatible)
                lifecycle = load_application_lifecycle(workspace)
                self.assertIsNotNone(lifecycle)
                assert lifecycle is not None
                self.assertEqual(
                    lifecycle.initialization.stage,
                    ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                )
                self.assertEqual(
                    lifecycle.initialization.status,
                    ApplicationLifecycleStatus.FAILED,
                )

                point = await _failure_point(
                    workspace,
                    run_id=source_run_id,
                    operation="product_planning",
                )
                snapshot = await graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": point.checkpoint_ns,
                            "checkpoint_id": point.checkpoint_id,
                        }
                    }
                )
                model_config["name"] = "working-product-model"
                context = await _prepare_and_run_child(
                    self,
                    workspace=workspace,
                    graph=graph,
                    source=source,
                    snapshot=snapshot,
                )
                child = await get_execution(workspace, context.new_run_id)

        self.assertEqual(counters, {"requirements": 1, "product_planning": 2})
        self.assertIsNotNone(child)
        assert child is not None
        self.assertEqual(child.status, DurableExecutionStatus.COMPLETED)

    async def test_ui_confirmation_failure_replays_only_ui_confirmation(self) -> None:
        """UI 设计模型 503 后 child 只能重放 ui_confirmation。"""

        graph = _build_wrapped_graph(
            ("requirements", "product_planning", "ui_confirmation")
        )
        model_config = {"name": "unavailable-ui-model"}
        counters = {"requirements": 0, "product_planning": 0, "ui_confirmation": 0}

        def requirements(_state: ProjectState) -> dict[str, Any]:
            """记录并返回成功的 Requirements 结果。"""

            counters["requirements"] += 1
            return _successful_requirements(_state)

        def product_planning(_state: ProjectState) -> dict[str, Any]:
            """记录并返回成功的 ProductPlan 结果。"""

            counters["product_planning"] += 1
            return _successful_product_planning(_state)

        async def ui_confirmation(_state: ProjectState) -> dict[str, Any]:
            """记录 UI 模型调用并模拟首次失败、child 成功。"""

            counters["ui_confirmation"] += 1
            model_name = Settings.from_env().model_name
            if model_name == "unavailable-ui-model":
                raise FakeModelConnectionError(model=model_name)
            return await _successful_ui_confirmation(_state)

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "failed-ui-thread"
            source_run_id = "run-ui-source"
            _seed_lifecycle(workspace, thread_id=thread_id, run_id=source_run_id)
            with patch(
                "app.config.Settings.from_env",
                side_effect=lambda: _settings_for(model_config["name"]),
            ), patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=requirements,
            ), patch(
                "app.graph.application_planning_workflow.nodes.product_planning",
                side_effect=product_planning,
            ), patch(
                "app.graph.application_planning_workflow.nodes.ui_confirmation",
                new=AsyncMock(side_effect=ui_confirmation),
            ):
                await _consume_runtime(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id=thread_id,
                        run_id=source_run_id,
                    ),
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.status, DurableExecutionStatus.FAILED)
                self.assertEqual(source.current_node, "ui_confirmation")
                self.assertIsNotNone(source.failure)
                assert source.failure is not None
                self.assertEqual(source.failure.operation, "ui_confirmation")
                self.assertEqual(source.failure.origin, ExecutionFailureOrigin.MODEL_CALL)
                self.assertTrue(source.failure.replay_compatible)
                lifecycle = load_application_lifecycle(workspace)
                self.assertIsNotNone(lifecycle)
                assert lifecycle is not None
                self.assertEqual(
                    lifecycle.initialization.stage,
                    ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                )
                self.assertEqual(
                    lifecycle.initialization.status,
                    ApplicationLifecycleStatus.FAILED,
                )

                point = await _failure_point(
                    workspace,
                    run_id=source_run_id,
                    operation="ui_confirmation",
                )
                snapshot = await graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": point.checkpoint_ns,
                            "checkpoint_id": point.checkpoint_id,
                        }
                    }
                )
                model_config["name"] = "working-ui-model"
                context = await _prepare_and_run_child(
                    self,
                    workspace=workspace,
                    graph=graph,
                    source=source,
                    snapshot=snapshot,
                )
                child = await get_execution(workspace, context.new_run_id)

        self.assertEqual(
            counters,
            {"requirements": 1, "product_planning": 1, "ui_confirmation": 2},
        )
        self.assertIsNotNone(child)
        assert child is not None
        self.assertEqual(child.status, DurableExecutionStatus.COMPLETED)

    async def test_missing_node_started_mirror_does_not_block_model_recovery(self) -> None:
        """product_planning 的 started mirror 丢失时仍按 exact boundary 完成恢复。"""

        graph = _build_wrapped_graph(("requirements", "product_planning"))
        model_config = {"name": "unavailable-mirror-model"}
        counters = {"requirements": 0, "product_planning": 0}

        def requirements(_state: ProjectState) -> dict[str, Any]:
            """记录并返回成功的 Requirements 结果。"""

            counters["requirements"] += 1
            return _successful_requirements(_state)

        def product_planning(_state: ProjectState) -> dict[str, Any]:
            """记录 ProductPlan 调用并按当前模型模拟失败或成功。"""

            counters["product_planning"] += 1
            model_name = Settings.from_env().model_name
            if model_name == "unavailable-mirror-model":
                raise FakeModelConnectionError(model=model_name)
            return _successful_product_planning(_state)

        async def fail_product_started(**kwargs: Any) -> None:
            """只丢弃 product_planning 的旁路 started 观测，其余照常落盘。"""

            if kwargs.get("node_name") == "product_planning":
                raise OSError("test recovery mirror unavailable")
            await persist_node_started(**kwargs)

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "stale-mirror-model-thread"
            source_run_id = "run-stale-mirror-source"
            _seed_lifecycle(workspace, thread_id=thread_id, run_id=source_run_id)
            with patch(
                "app.config.Settings.from_env",
                side_effect=lambda: _settings_for(model_config["name"]),
            ), patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=requirements,
            ), patch(
                "app.graph.application_planning_workflow.nodes.product_planning",
                side_effect=product_planning,
            ), patch(
                "app.protocols.workflow.runtime.observe_node_started",
                side_effect=fail_product_started,
            ):
                await _consume_runtime(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id=thread_id,
                        run_id=source_run_id,
                    ),
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.status, DurableExecutionStatus.FAILED)
                self.assertEqual(source.current_node, "product_planning")
                self.assertIsNotNone(source.failure)
                assert source.failure is not None
                self.assertEqual(source.failure.operation, "product_planning")
                self.assertEqual(source.failure.origin, ExecutionFailureOrigin.MODEL_CALL)
                self.assertTrue(source.failure.replay_compatible)

                point = await _failure_point(
                    workspace,
                    run_id=source_run_id,
                    operation="product_planning",
                )
                snapshot = await graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": point.checkpoint_ns,
                            "checkpoint_id": point.checkpoint_id,
                        }
                    }
                )
                self.assertEqual(snapshot.next, ("product_planning",))
                model_config["name"] = "working-mirror-model"
                context = await _prepare_and_run_child(
                    self,
                    workspace=workspace,
                    graph=graph,
                    source=source,
                    snapshot=snapshot,
                )
                child = await get_execution(workspace, context.new_run_id)

        self.assertEqual(counters, {"requirements": 1, "product_planning": 2})
        self.assertIsNotNone(child)
        assert child is not None
        self.assertEqual(child.status, DurableExecutionStatus.COMPLETED)

    async def test_non_unique_successors_remain_blocked_by_coordinator(self) -> None:
        """零或多 successor 的模型失败现场不能被 Coordinator 放行 Native。"""

        now = datetime.now(timezone.utc)
        for suffix, next_nodes in (("zero", []), ("multiple", ["A", "B"])):
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as raw_workspace:
                    workspace = Path(raw_workspace)
                    thread_id = f"blocked-successor-{suffix}"
                    run_id = f"run-blocked-{suffix}"
                    lifecycle = create_application_lifecycle(
                        application_id="app-recovery-test",
                        application_name="恢复测试应用",
                        initialization_thread_id=thread_id,
                        active_run_id=run_id,
                    )
                    write_application_lifecycle(workspace, lifecycle, expected_revision=0)
                    lifecycle = persist_application_lifecycle_transition(
                        workspace,
                        stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                        status=ApplicationLifecycleStatus.FAILED,
                        active_run_id=run_id,
                    )
                    source = DurableExecutionRecord(
                        run_id=run_id,
                        thread_id=thread_id,
                        workspace=str(workspace),
                        project_id="app-recovery-test",
                        execution_kind="application_planning",
                        workflow_scope="application_planning",
                        first_node="requirements",
                        current_node="requirements",
                        status=DurableExecutionStatus.FAILED,
                        started_at=now,
                        updated_at=now,
                        ended_at=now,
                        failure=ExecutionFailureEvidence(
                            origin=ExecutionFailureOrigin.MODEL_CALL,
                            code="http_503",
                            http_status=503,
                            replay_compatible=True,
                        ),
                    )
                    await insert_execution(source)
                    point = RecoveryPoint(
                        recovery_point_id=f"point-blocked-{suffix}",
                        run_id=run_id,
                        thread_id=thread_id,
                        kind=RecoveryPointKind.CHECKPOINT,
                        checkpoint_id=f"checkpoint-blocked-{suffix}",
                        checkpoint_ns="",
                        graph_node="requirements",
                        completed_node=None,
                        next_nodes=next_nodes,
                        phase="requirements",
                        state_status="running",
                        lifecycle_revision=lifecycle.revision,
                        captured_at=now,
                    )
                    await insert_recovery_point(workspace=workspace, point=point)
                    graph = _FixedSnapshotGraph(
                        thread_id=thread_id,
                        point=point,
                        values={"active_run_id": run_id},
                    )
                    plan = await prepare_continue(
                        workspace=str(workspace),
                        source_run_id=run_id,
                        graph=graph,
                        replay_policies=production_recovery_replay_policies(),
                    )

                    self.assertIsNone(source.failure.operation)
                    self.assertNotEqual(plan.decision, RecoveryDecision.READY_NATIVE)


class _FixedSnapshotGraph:
    """提供 Coordinator 身份校验所需的固定 checkpoint snapshot。"""

    def __init__(
        self,
        *,
        thread_id: str,
        point: RecoveryPoint,
        values: dict[str, Any],
    ) -> None:
        """保存测试现场的 checkpoint identity、successor 与 state values。"""

        self.snapshot = SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": point.checkpoint_ns,
                    "checkpoint_id": point.checkpoint_id,
                }
            },
            next=tuple(point.next_nodes),
            tasks=(),
            values=values,
        )

    async def aget_state(self, _config: dict[str, Any]) -> SimpleNamespace:
        """返回固定 snapshot，模拟真实 LangGraph 的精确 checkpoint 读取。"""

        return self.snapshot


__all__ = ["ApplicationPlanningFailedModelRecoveryTests"]
