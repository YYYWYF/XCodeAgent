from __future__ import annotations

import asyncio
import unittest
from tempfile import TemporaryDirectory

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph.application_planning_interrupts import requirements_review
from app.graph.state import ProjectState
from app.protocols.application_planning_interrupt import (
    project_application_planning_interrupt,
)
from app.protocols.workflow import build_workflow_ag_ui_stream


def _requirements_fixture(state: ProjectState) -> dict:
    """构造可确认的最小需求节点，用于验证真实 interrupt 往返。"""

    interaction = state.get("application_planning_interaction")
    if isinstance(interaction, dict) and interaction.get("action") == "confirm":
        return {
            "phase": "requirements",
            "status": "completed",
            "requirement_spec": {"confirmation_status": "confirmed"},
            "clarification": {"status": "clear"},
        }
    if isinstance(interaction, dict) and interaction.get("action") == "revise":
        return {
            "phase": "requirements",
            "status": "requires_user_input",
            "requirement_spec": {
                "confirmation_status": "pending_user_confirmation",
                "name": str(state.get("request") or "修订需求"),
            },
            "clarification": {
                "status": "requires_user_input",
                "mode": "requirement_document_confirmation",
            },
            "application_planning_interaction": {},
        }
    return {
        "phase": "requirements",
        "status": "requires_user_input",
        "requirement_spec": {
            "confirmation_status": "pending_user_confirmation",
            "name": "任务中心",
        },
        "clarification": {
            "status": "requires_user_input",
            "mode": "requirement_document_confirmation",
        },
    }


def _route_requirements_fixture(state: ProjectState) -> str:
    """确认完成时结束测试图，否则进入需求审阅中断。"""

    return "completed" if state.get("status") == "completed" else "review"


def _design_intent_fixture(state: ProjectState) -> ProjectState:
    """提供审阅门动态目标校验所需的占位设计意图节点。"""

    return state


def _interrupt_test_graph():
    """构建只包含需求产物与原生审阅门的最小测试 Graph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("requirements", _requirements_fixture)
    builder.add_node("requirements_review", requirements_review)
    builder.add_node("design_intent_analysis", _design_intent_fixture)
    builder.add_edge(START, "requirements")
    builder.add_conditional_edges(
        "requirements",
        _route_requirements_fixture,
        {"review": "requirements_review", "completed": END},
    )
    return builder.compile(checkpointer=InMemorySaver())


def _counting_interrupt_test_graph(
    downstream_count: list[int],
    downstream_entered: asyncio.Event,
    release_downstream: asyncio.Event,
):
    """构造带真实异步下游副作用的中断 Graph，用于验证恢复串行化。"""

    async def counting_requirements(state: ProjectState) -> dict:
        """在确认恢复时计数并等待测试释放，制造可观测的并发窗口。"""

        interaction = state.get("application_planning_interaction")
        if isinstance(interaction, dict) and interaction.get("action") == "confirm":
            downstream_count[0] += 1
            downstream_entered.set()
            await release_downstream.wait()
            return {
                "phase": "requirements",
                "status": "completed",
                "requirement_spec": {"confirmation_status": "confirmed"},
                "clarification": {"status": "clear"},
            }
        return _requirements_fixture(state)

    builder = StateGraph(ProjectState)
    builder.add_node("requirements", counting_requirements)
    builder.add_node("requirements_review", requirements_review)
    builder.add_node("design_intent_analysis", _design_intent_fixture)
    builder.add_edge(START, "requirements")
    builder.add_conditional_edges(
        "requirements",
        _route_requirements_fixture,
        {"review": "requirements_review", "completed": END},
    )
    return builder.compile(checkpointer=InMemorySaver())


def _resume_payload(
    *,
    thread_id: str,
    run_id: str,
    workspace: str,
    pending: dict,
    gate_id: str | None = None,
    action: str = "confirm",
    request: str = "正确，继续规划",
):
    """构造创建规划审阅恢复请求，避免并发测试重复拼接传输载荷。"""

    return {
        "threadId": thread_id,
        "runId": run_id,
        "message": request,
        "forwardedProps": {
            "workspaceRoot": workspace,
            "workflowScope": "application_planning",
            "editorMode": "frontend",
            "applicationPlanningInteraction": {
                "gateId": gate_id or pending["gateId"],
                "artifact": pending["artifact"],
                "artifactRevision": pending["artifactRevision"],
                "action": action,
                "request": request,
            },
        },
    }


class ApplicationPlanningInterruptTests(unittest.IsolatedAsyncioTestCase):
    """验证创建规划原生中断、恢复和过期提交保护。"""

    async def test_confirm_resumes_exact_pending_review(self) -> None:
        """正确 gateId 和产物摘要应恢复原任务并完成确认。"""

        graph = _interrupt_test_graph()
        config = {"configurable": {"thread_id": "planning-confirm"}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]
        snapshot = await graph.aget_state(config)
        projected = project_application_planning_interrupt(
            dict(snapshot.values),
            snapshot,
        )
        pending = projected["application_planning_interrupt"]

        _ = [
            chunk
            async for chunk in graph.astream(
                Command(
                    resume={
                        "gate_id": pending["gateId"],
                        "artifact": pending["artifact"],
                        "artifact_revision": pending["artifactRevision"],
                        "action": "confirm",
                        "request": "正确，继续规划",
                    }
                ),
                config=config,
                stream_mode="updates",
            )
        ]

        completed = await graph.aget_state(config)
        self.assertEqual(completed.values["status"], "completed")
        self.assertEqual(
            completed.values["requirement_spec"]["confirmation_status"],
            "confirmed",
        )
        self.assertFalse(completed.tasks)

    async def test_stale_gate_is_rejected(self) -> None:
        """旧确认卡不能恢复已经变化的正式产物。"""

        graph = _interrupt_test_graph()
        config = {"configurable": {"thread_id": "planning-stale"}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]
        snapshot = await graph.aget_state(config)
        pending = snapshot.tasks[0].interrupts[0].value

        with self.assertRaisesRegex(ValueError, "已经过期"):
            _ = [
                chunk
                async for chunk in graph.astream(
                    Command(
                        resume={
                            "gate_id": "requirement_spec:stale",
                            "artifact": pending["artifact"],
                            "artifact_revision": pending["artifactRevision"],
                            "action": "confirm",
                            "request": "正确，继续规划",
                        }
                    ),
                    config=config,
                    stream_mode="updates",
                )
            ]

    async def test_same_artifact_can_be_revised_twice_before_confirmation(self) -> None:
        """同一产物连续二次修订时应生成新门禁，并只确认最后一个版本。"""

        graph = _interrupt_test_graph()
        config = {"configurable": {"thread_id": "planning-revise-twice"}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]

        revisions: list[dict] = []
        for request in ("增加审批角色", "审批角色改为财务复核员"):
            snapshot = await graph.aget_state(config)
            pending = snapshot.tasks[0].interrupts[0].value
            revisions.append(pending)
            _ = [
                chunk
                async for chunk in graph.astream(
                    Command(
                        resume={
                            "gateId": pending["gateId"],
                            "artifact": pending["artifact"],
                            "artifactRevision": pending["artifactRevision"],
                            "action": "revise",
                            "request": request,
                        }
                    ),
                    config=config,
                    stream_mode="updates",
                )
            ]

        latest = await graph.aget_state(config)
        latest_pending = latest.tasks[0].interrupts[0].value
        self.assertNotEqual(revisions[0]["gateId"], revisions[1]["gateId"])
        self.assertNotEqual(revisions[1]["gateId"], latest_pending["gateId"])
        self.assertEqual(
            latest.values["requirement_spec"]["name"],
            "审批角色改为财务复核员",
        )

        _ = [
            chunk
            async for chunk in graph.astream(
                Command(
                    resume={
                        "gateId": latest_pending["gateId"],
                        "artifact": latest_pending["artifact"],
                        "artifactRevision": latest_pending["artifactRevision"],
                        "action": "confirm",
                        "request": "确认，继续规划",
                    }
                ),
                config=config,
                stream_mode="updates",
            )
        ]

        completed = await graph.aget_state(config)
        self.assertEqual(completed.values["status"], "completed")
        self.assertFalse(completed.tasks)

    async def test_snapshot_projects_interrupt_as_confirmation_state(self) -> None:
        """冷启动恢复必须从 checkpoint interrupt 重建确认卡和等待状态。"""

        graph = _interrupt_test_graph()
        config = {"configurable": {"thread_id": "planning-recovery"}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]
        snapshot = await graph.aget_state(config)

        projected = project_application_planning_interrupt(
            dict(snapshot.values),
            snapshot,
        )

        self.assertEqual(projected["status"], "requires_user_input")
        self.assertEqual(projected["phase"], "requirements")
        self.assertEqual(
            projected["application_planning_interrupt"]["artifact"],
            "requirement_spec",
        )

    async def test_ag_ui_runtime_uses_command_resume(self) -> None:
        """AG-UI 新 run 必须用 Command 恢复同一 thread，而不是重建 Graph 输入。"""

        graph = _interrupt_test_graph()
        thread_id = "planning-ag-ui-resume"
        config = {"configurable": {"thread_id": thread_id}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]
        snapshot = await graph.aget_state(config)
        pending = snapshot.tasks[0].interrupts[0].value

        with TemporaryDirectory() as workspace:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": thread_id,
                    "runId": "planning-ag-ui-run",
                    "message": "正确，继续规划",
                    "forwardedProps": {
                        "workspaceRoot": workspace,
                        "workflowScope": "application_planning",
                        "editorMode": "frontend",
                        "applicationPlanningInteraction": {
                            "gateId": pending["gateId"],
                            "artifact": pending["artifact"],
                            "artifactRevision": pending["artifactRevision"],
                            "action": "confirm",
                            "request": "正确，继续规划",
                        },
                    },
                },
            )
            frames = [frame async for frame in stream]

        completed = await graph.aget_state(config)
        self.assertTrue(frames)
        self.assertEqual(completed.values["status"], "completed")
        self.assertFalse(completed.tasks)

    async def test_stale_gate_fails_before_revision_started_projection(self) -> None:
        """旧修订 gate 必须在首个 workflow 投影前失败，不能伪造修订开始状态。"""

        graph = _interrupt_test_graph()
        thread_id = "planning-stale-runtime"
        config = {"configurable": {"thread_id": thread_id}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]
        snapshot = await graph.aget_state(config)
        pending = snapshot.tasks[0].interrupts[0].value

        with TemporaryDirectory() as workspace:
            frames = [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload=_resume_payload(
                        thread_id=thread_id,
                        run_id="planning-stale-runtime-run",
                        workspace=workspace,
                        pending=pending,
                        gate_id="requirement_spec:stale",
                        action="revise",
                        request="增加审批角色",
                    ),
                )
            ]

        self.assertTrue(any('"type":"RUN_ERROR"' in frame for frame in frames))
        self.assertTrue(any("已经过期" in frame for frame in frames))
        self.assertFalse(any('"workflow.run.started"' in frame for frame in frames))
        self.assertFalse(any('"design_change_submission":true' in frame for frame in frames))
        current = await graph.aget_state(config)
        self.assertTrue(current.tasks)

    async def test_identical_resumes_are_serialized_and_only_one_reaches_downstream(
        self,
    ) -> None:
        """同一 thread 的相同恢复提交只允许一个进入真实下游节点。"""

        downstream_count = [0]
        downstream_entered = asyncio.Event()
        release_downstream = asyncio.Event()
        graph = _counting_interrupt_test_graph(
            downstream_count,
            downstream_entered,
            release_downstream,
        )
        thread_id = "planning-concurrent-resume"
        config = {"configurable": {"thread_id": thread_id}}
        _ = [
            chunk
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
        ]
        snapshot = await graph.aget_state(config)
        pending = snapshot.tasks[0].interrupts[0].value

        async def collect(run_id: str, workspace: str) -> list[str]:
            """消费一次 AG-UI 流，供并发恢复测试等待完整生命周期。"""

            return [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload=_resume_payload(
                        thread_id=thread_id,
                        run_id=run_id,
                        workspace=workspace,
                        pending=pending,
                    ),
                )
            ]

        with TemporaryDirectory() as workspace:
            first_task = asyncio.create_task(collect("planning-concurrent-1", workspace))
            await asyncio.wait_for(downstream_entered.wait(), timeout=5)
            second_task = asyncio.create_task(
                collect("planning-concurrent-2", workspace)
            )
            await asyncio.sleep(0)
            release_downstream.set()
            first_frames, second_frames = await asyncio.gather(
                first_task,
                second_task,
            )

        self.assertEqual(downstream_count[0], 1)
        self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in first_frames))
        self.assertTrue(any('"type":"RUN_ERROR"' in frame for frame in second_frames))
        self.assertTrue(
            any("没有可恢复的审阅中断" in frame or "已经过期" in frame for frame in second_frames)
        )
        completed = await graph.aget_state(config)
        self.assertEqual(completed.values["status"], "completed")
        self.assertFalse(completed.tasks)
