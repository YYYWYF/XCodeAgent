from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.application_planning_interrupts import technical_planning_review
from app.graph.state import ProjectState
from app.persistence.execution_recovery import (
    get_execution,
    list_recovery_points,
)
from app.protocols.application_planning_interrupt import (
    project_application_planning_interrupt,
)
from app.protocols.workflow import build_workflow_ag_ui_stream


def _linear_runtime_graph(
    *,
    started_events: dict[str, asyncio.Event] | None = None,
    release_events: dict[str, asyncio.Event] | None = None,
    failing_node: str | None = None,
) -> tuple[Any, InMemorySaver]:
    """构造真实 StateGraph 和内存 checkpoint，用于 Runtime 级恢复观测。"""

    started_events = started_events or {}
    release_events = release_events or {}
    checkpointer = InMemorySaver()
    node_names = ("requirements", "product_planning", "technical_planning")
    builder = StateGraph(ProjectState)

    for node_name in node_names:
        async def linear_node(
            state: ProjectState,
            *,
            current_node: str = node_name,
        ) -> dict[str, Any]:
            """执行一个确定性节点，并按测试需要暴露开始、阻塞或异常。"""

            event = started_events.get(current_node)
            if event is not None:
                event.set()
            if current_node == failing_node:
                raise RuntimeError(f"{current_node} failed")
            release = release_events.get(current_node)
            if release is not None:
                await release.wait()
            return {
                "phase": current_node,
                "status": "completed",
                "runtime_marker": current_node,
            }

        builder.add_node(node_name, linear_node)

    builder.add_edge(START, node_names[0])
    builder.add_edge(node_names[0], node_names[1])
    builder.add_edge(node_names[1], node_names[2])
    builder.add_edge(node_names[2], END)
    return builder.compile(checkpointer=checkpointer), checkpointer


def _runtime_payload(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    interaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 application planning Runtime 使用的最小 AG-UI 请求。"""

    forwarded_props: dict[str, Any] = {
        "workspaceRoot": str(workspace),
        "workflowScope": "application_planning",
    }
    if interaction is not None:
        forwarded_props["applicationPlanningInteraction"] = interaction
    return {
        "threadId": thread_id,
        "runId": run_id,
        "message": "运行恢复观测集成测试",
        "forwardedProps": forwarded_props,
    }


async def _collect_runtime_frames(graph: Any, payload: dict[str, Any]) -> list[str]:
    """消费一次完整 Runtime 流，保留原始 AG-UI 帧供生命周期断言。"""

    return [
        frame
        async for frame in build_workflow_ag_ui_stream(
            graph=graph,
            payload=payload,
        )
    ]


async def _checkpoint_identities(
    checkpointer: InMemorySaver,
    thread_id: str,
) -> set[tuple[str, str]]:
    """读取真实 checkpointer 历史中的 checkpointId 与 namespace。"""

    identities: set[tuple[str, str]] = set()
    config = {"configurable": {"thread_id": thread_id}}
    async for item in checkpointer.alist(config):
        configurable = item.config.get("configurable", {})
        if not isinstance(configurable, dict):
            continue
        checkpoint_id = str(configurable.get("checkpoint_id") or "")
        checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
        if checkpoint_id:
            identities.add((checkpoint_id, checkpoint_ns))
    return identities


def _technical_review_graph(
    started: asyncio.Event,
    release: asyncio.Event,
) -> tuple[Any, InMemorySaver]:
    """构造 technical_planning_review 恢复到真实后继节点的最小 Graph。"""

    async def technical_planning(state: ProjectState) -> dict[str, Any]:
        """在 TechnicalPlan 节点开始后阻塞，制造可读取的 currentNode 窗口。"""

        started.set()
        await release.wait()
        return {
            "phase": "technical_planning",
            "status": "completed",
            "clarification": {"status": "clear"},
        }

    checkpointer = InMemorySaver()
    builder = StateGraph(ProjectState)
    builder.add_node(
        "technical_planning_review",
        technical_planning_review,
        destinations=("technical_planning",),
    )
    builder.add_node("technical_planning", technical_planning)
    builder.add_edge(START, "technical_planning_review")
    builder.add_edge("technical_planning", END)
    return builder.compile(checkpointer=checkpointer), checkpointer


def _technical_review_initial_state() -> ProjectState:
    """返回能够触发 TechnicalPlan 审阅中断的正式状态快照。"""

    return {
        "phase": "technical_planning",
        "status": "requires_user_input",
        "technical_plan": {
            "confirmation_status": "pending_user_confirmation",
            "name": "技术规划",
        },
        "clarification": {
            "mode": "technical_plan_confirmation",
            "status": "requires_user_input",
        },
    }


class WorkflowExecutionRecoveryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 Runtime、StateGraph 和 checkpointer 验证恢复事实时序。"""

    async def test_real_linear_graph_records_checkpoint_history_and_ag_ui_lifecycle(
        self,
    ) -> None:
        """真实 A→B→C Graph 必须记录成功边界并保留可追溯 checkpoint。"""

        graph, checkpointer = _linear_runtime_graph()
        thread_id = "runtime-linear-thread"
        run_id = "runtime-linear-run"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            frames = await _collect_runtime_frames(
                graph,
                _runtime_payload(
                    workspace,
                    thread_id=thread_id,
                    run_id=run_id,
                ),
            )
            points = await list_recovery_points(workspace, run_id)
            checkpoint_identities = await _checkpoint_identities(
                checkpointer,
                thread_id,
            )

        expected = {
            "requirements": ("product_planning",),
            "product_planning": ("technical_planning",),
            "technical_planning": (),
        }
        for completed_node, expected_next in expected.items():
            matching = [
                point
                for point in points
                if point.completed_node == completed_node
            ]
            self.assertTrue(matching, f"missing RecoveryPoint for {completed_node}")
            point = matching[-1]
            self.assertEqual(point.next_nodes, list(expected_next))
            self.assertIsNotNone(point.checkpoint_id)
            self.assertIn(
                (str(point.checkpoint_id), point.checkpoint_ns),
                checkpoint_identities,
            )
        self.assertIn('"type":"RUN_STARTED"', "".join(frames))
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))
        self.assertNotIn('"type":"RUN_ERROR"', "".join(frames))

    async def test_exception_captures_latest_checkpoint_without_completing_running_node(
        self,
    ) -> None:
        """B 抛异常时只保留 A 成功边界和 B 后继现场，禁止虚构 B 完成。"""

        graph, _ = _linear_runtime_graph(failing_node="product_planning")
        thread_id = "runtime-exception-thread"
        run_id = "runtime-exception-run"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            frames = await _collect_runtime_frames(
                graph,
                _runtime_payload(
                    workspace,
                    thread_id=thread_id,
                    run_id=run_id,
                ),
            )
            points = await list_recovery_points(workspace, run_id)
            record = await get_execution(workspace, run_id)

        self.assertTrue(any(
            point.completed_node == "requirements"
            and "product_planning" in point.next_nodes
            for point in points
        ))
        self.assertFalse(any(
            point.completed_node == "product_planning"
            and "product_planning" in point.next_nodes
            for point in points
        ))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status.value, "failed")
        self.assertIn('"type":"RUN_ERROR"', "".join(frames))
        self.assertNotIn('"type":"RUN_FINISHED"', "".join(frames))

    async def test_external_cancel_captures_running_node_as_interrupted(self) -> None:
        """外部取消 B 时必须保留未完成现场并标记为 interrupted。"""

        product_started = asyncio.Event()
        release_product = asyncio.Event()
        graph, _ = _linear_runtime_graph(
            started_events={"product_planning": product_started},
            release_events={"product_planning": release_product},
        )
        thread_id = "runtime-cancel-thread"
        run_id = "runtime-cancel-run"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)

            async def collect() -> list[str]:
                """消费待取消的 Runtime 流，直到外部取消传播。"""

                return await _collect_runtime_frames(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id=thread_id,
                        run_id=run_id,
                    ),
                )

            runtime_task = asyncio.create_task(collect())
            await asyncio.wait_for(product_started.wait(), timeout=5)
            running = await get_execution(workspace, run_id)
            self.assertIsNotNone(running)
            assert running is not None
            self.assertEqual(running.current_node, "product_planning")
            runtime_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await runtime_task
            points = await list_recovery_points(workspace, run_id)
            cancelled = await get_execution(workspace, run_id)

        self.assertIsNotNone(cancelled)
        assert cancelled is not None
        self.assertEqual(cancelled.status.value, "interrupted")
        self.assertFalse(any(
            point.completed_node == "product_planning"
            for point in points
        ))

    async def test_application_planning_snapshot_only_does_not_create_fake_execution(
        self,
    ) -> None:
        """已有 planning interrupt 的无动作读取只能投影快照，不能登记伪造运行。"""

        graph, _ = _technical_review_graph(asyncio.Event(), asyncio.Event())
        thread_id = "runtime-snapshot-thread"
        config = {"configurable": {"thread_id": thread_id}}
        _ = [
            chunk
            async for chunk in graph.astream(
                _technical_review_initial_state(),
                config=config,
                stream_mode="updates",
            )
        ]
        snapshot = await graph.aget_state(config)
        self.assertIsNotNone(
            project_application_planning_interrupt(
                dict(snapshot.values),
                snapshot,
            ).get("application_planning_interrupt")
        )

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            frames = await _collect_runtime_frames(
                graph,
                _runtime_payload(
                    workspace,
                    thread_id=thread_id,
                    run_id="runtime-snapshot-only-run",
                ),
            )
            execution = await get_execution(
                workspace,
                "runtime-snapshot-only-run",
            )

        self.assertIsNone(execution)
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))
        self.assertNotIn('"type":"RUN_ERROR"', "".join(frames))

    async def test_application_planning_review_resume_advances_real_successor(
        self,
    ) -> None:
        """review 恢复期间阻塞真实 TechnicalPlan 时 currentNode 必须已经推进。"""

        technical_started = asyncio.Event()
        release_technical = asyncio.Event()
        graph, _ = _technical_review_graph(technical_started, release_technical)
        thread_id = "runtime-review-thread"
        config = {"configurable": {"thread_id": thread_id}}
        _ = [
            chunk
            async for chunk in graph.astream(
                _technical_review_initial_state(),
                config=config,
                stream_mode="updates",
            )
        ]
        pending_snapshot = await graph.aget_state(config)
        pending = project_application_planning_interrupt(
            dict(pending_snapshot.values),
            pending_snapshot,
        )["application_planning_interrupt"]
        interaction = {
            "gateId": pending["gateId"],
            "artifact": pending["artifact"],
            "artifactRevision": pending["artifactRevision"],
            "action": "confirm",
            "request": "确认技术规划并继续",
        }

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)

            async def resume() -> list[str]:
                """消费恢复运行直到 TechnicalPlan 节点释放并完成。"""

                return await _collect_runtime_frames(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id=thread_id,
                        run_id="runtime-review-run",
                        interaction=interaction,
                    ),
                )

            runtime_task = asyncio.create_task(resume())
            await asyncio.wait_for(technical_started.wait(), timeout=5)
            running = await get_execution(workspace, "runtime-review-run")
            points = await list_recovery_points(workspace, "runtime-review-run")
            release_technical.set()
            frames = await runtime_task

        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running.current_node, "technical_planning")
        self.assertTrue(any(
            point.completed_node == "technical_planning_review"
            and point.next_nodes == ["technical_planning"]
            for point in points
        ))
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))

    async def test_recovery_observation_failure_does_not_fail_real_runtime(self) -> None:
        """恢复库入口观测失败时，真实 Graph 仍必须完成 AG-UI 生命周期。"""

        graph, _ = _linear_runtime_graph()
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            failed_observation = AsyncMock(
                side_effect=OSError("recovery db unavailable")
            )
            with patch(
                "app.protocols.workflow.runtime.observe_execution_started",
                failed_observation,
            ):
                frames = await _collect_runtime_frames(
                    graph,
                    _runtime_payload(
                        workspace,
                        thread_id="runtime-fail-open-thread",
                        run_id="runtime-fail-open-run",
                    ),
                )

        self.assertTrue(failed_observation.await_count >= 1)
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))
        self.assertNotIn('"type":"RUN_ERROR"', "".join(frames))


__all__ = ["WorkflowExecutionRecoveryRuntimeTests"]
