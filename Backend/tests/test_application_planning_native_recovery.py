"""真实 LangGraph Requirement answer committed Native Recovery 回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import (
    get_execution,
    insert_execution,
    insert_recovery_point,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
    write_application_lifecycle,
)
from app.services.application_planning_recovery_coordinator import (
    resolve_application_planning_recovery,
)
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_recovery_policies import (
    production_recovery_replay_policies,
)


class PlanningRecoveryState(TypedDict, total=False):
    """声明真实 Planning recovery 测试图使用的最小状态。"""

    active_run_id: str
    active_thread_id: str
    workspace: str
    status: str
    phase: str
    clarification: dict[str, Any]
    application_planning_interaction: dict[str, Any]
    requirements_executions: list[str]
    lifecycle: dict[str, Any]
    observability: dict[str, Any]
    resume_from: str


def _build_committed_answer_graph(counters: dict[str, int]) -> Any:
    """构造 review 原生中断并在 requirements 前形成 durable checkpoint。"""

    def review(_state: PlanningRecoveryState) -> dict[str, Any]:
        """消费真实 Native Interrupt 回答并把完整交互写入 Graph State。"""

        submission = interrupt(
            {
                "type": "application_planning_review",
                "gateId": "requirement_spec:revision-1",
                "artifact": "requirement_spec",
                "artifactRevision": "revision-1",
                "phase": "requirements",
                "clarification": {
                    "mode": "ask_user_question",
                    "status": "requires_user_input",
                    "questions": [{"id": "role", "prompt": "业务角色是什么？"}],
                },
            }
        )
        counters["review"] += 1
        return {"application_planning_interaction": dict(submission)}

    def requirements(state: PlanningRecoveryState) -> dict[str, Any]:
        """模拟真实 requirements 入口切换 lifecycle 并记录唯一执行次数。"""

        persist_application_lifecycle_transition(
            state["workspace"],
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=state["active_run_id"],
        )
        counters["requirements"] += 1
        return {
            "phase": "requirements",
            "status": "completed",
            "requirements_executions": [
                *state.get("requirements_executions", []),
                state["active_run_id"],
            ],
        }

    builder = StateGraph(PlanningRecoveryState)
    builder.add_node("review", review)
    builder.add_node("requirements", requirements)
    builder.add_edge(START, "review")
    builder.add_edge("review", "requirements")
    builder.add_edge("requirements", END)
    return builder.compile(
        checkpointer=InMemorySaver(),
        interrupt_before=["requirements"],
    )


def _create_awaiting_lifecycle(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
) -> ApplicationLifecycle:
    """沿真实状态机写入 awaiting clarification 生命周期并返回 revision N。"""

    lifecycle = create_application_lifecycle(
        application_id="app-1",
        application_name="花名册",
        initialization_thread_id=thread_id,
        active_run_id=run_id,
    )
    write_application_lifecycle(workspace, lifecycle, expected_revision=0)
    persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        status=ApplicationLifecycleStatus.RUNNING,
        active_run_id=run_id,
    )
    return persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        status=ApplicationLifecycleStatus.AWAITING_USER,
        active_run_id=run_id,
    )


class ApplicationPlanningNativeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 Lifecycle transition 前后两个真实 Native Recovery crash 窗口。"""

    async def test_committed_answer_recovers_across_both_lifecycle_windows(self) -> None:
        """两个窗口都应 fork 新 run，并且只继续执行一次 requirements。"""

        for transition_started in (False, True):
            with self.subTest(transition_started=transition_started):
                await self._assert_native_recovery_window(
                    transition_started=transition_started,
                )

    async def _assert_native_recovery_window(self, *, transition_started: bool) -> None:
        """建立真实 answer-committed checkpoint 并执行完整 Native Recovery。"""

        counters = {"review": 0, "requirements": 0}
        graph = _build_committed_answer_graph(counters)
        source_run_id = "run-A"
        thread_id = f"planning-native-{int(transition_started)}"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            awaiting = _create_awaiting_lifecycle(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
            )
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke(
                {
                    "active_run_id": source_run_id,
                    "active_thread_id": thread_id,
                    "workspace": str(workspace),
                    "phase": "requirements",
                    "status": "requires_user_input",
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "questions": [
                            {"id": "role", "prompt": "业务角色是什么？"},
                        ],
                    },
                    "requirements_executions": [],
                },
                config=config,
            )
            interrupted = await graph.aget_state(config)
            self.assertTrue(any(task.interrupts for task in interrupted.tasks))

            await graph.ainvoke(
                Command(
                    resume={
                        "action": "answer",
                        "artifact": "requirement_spec",
                        "gate_id": "requirement_spec:revision-1",
                        "artifact_revision": "revision-1",
                        "answers": {"role": "本人"},
                        "request": "创建花名册",
                    }
                ),
                config=config,
            )
            committed = await graph.aget_state(config)
            self.assertEqual(tuple(committed.next), ("requirements",))
            self.assertFalse(any(task.interrupts for task in committed.tasks))

            identity = committed.config["configurable"]
            captured_at = datetime.now(timezone.utc)
            source = DurableExecutionRecord(
                run_id=source_run_id,
                thread_id=thread_id,
                workspace=str(workspace),
                project_id="app-1",
                execution_kind="application_planning",
                workflow_scope="application_planning",
                first_node="review",
                current_node="requirements",
                status=DurableExecutionStatus.INTERRUPTED,
                started_at=captured_at,
                updated_at=captured_at,
                ended_at=captured_at,
            )
            await insert_execution(source)
            point = RecoveryPoint(
                recovery_point_id=f"point-{int(transition_started)}",
                run_id=source_run_id,
                thread_id=thread_id,
                kind=RecoveryPointKind.CHECKPOINT,
                checkpoint_id=str(identity["checkpoint_id"]),
                checkpoint_ns=str(identity.get("checkpoint_ns") or ""),
                graph_node="requirements",
                completed_node=None,
                next_nodes=["requirements"],
                phase="requirements",
                state_status="requires_user_input",
                lifecycle_revision=awaiting.revision,
                captured_at=captured_at,
            )
            await insert_recovery_point(workspace=workspace, point=point)

            current = awaiting
            if transition_started:
                current = persist_application_lifecycle_transition(
                    workspace,
                    stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    status=ApplicationLifecycleStatus.RUNNING,
                    active_run_id=source_run_id,
                )
            projection = await resolve_application_planning_recovery(
                workspace=str(workspace),
                thread_id=thread_id,
                graph=graph,
                snapshot=committed,
                lifecycle=current,
                source=source,
            )
            self.assertEqual(projection.classification, "ready_to_continue")
            self.assertTrue(projection.can_continue)
            self.assertTrue(projection.input_committed)

            context = await prepare_native_recovery(
                workspace=str(workspace),
                source_run_id=source_run_id,
                graph=graph,
                replay_policies=production_recovery_replay_policies(),
            )
            handed_off = load_application_lifecycle(workspace)
            self.assertIsNotNone(handed_off)
            assert handed_off is not None
            self.assertEqual(handed_off.revision, current.revision + 1)
            self.assertEqual(handed_off.active_run_id, context.new_run_id)
            self.assertNotEqual(context.new_run_id, source_run_id)
            self.assertEqual(context.thread_id, thread_id)

            try:
                await graph.ainvoke(None, config=context.fork_config)
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
            completed = await graph.aget_state(context.observation_config)
            final_lifecycle = load_application_lifecycle(workspace)
            source_after = await get_execution(workspace, source_run_id)

        self.assertEqual(counters, {"review": 1, "requirements": 1})
        self.assertEqual(completed.values["requirements_executions"], [context.new_run_id])
        self.assertFalse(any(task.interrupts for task in completed.tasks))
        self.assertFalse(completed.next)
        self.assertIsNotNone(final_lifecycle)
        assert final_lifecycle is not None
        self.assertEqual(
            final_lifecycle.initialization.stage,
            ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        )
        self.assertEqual(final_lifecycle.initialization.status, ApplicationLifecycleStatus.RUNNING)
        self.assertEqual(final_lifecycle.active_run_id, context.new_run_id)
        self.assertIsNotNone(source_after)
        assert source_after is not None
        self.assertEqual(source_after.status, DurableExecutionStatus.INTERRUPTED)


__all__ = ["ApplicationPlanningNativeRecoveryTests"]
