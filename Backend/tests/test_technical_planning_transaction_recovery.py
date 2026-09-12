"""真实 StateGraph 下 TechnicalPlan transaction boundary 的崩溃重放测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.domain.application_revision import (
    RevisionImpact,
    RevisionTarget,
)
from app.graph.nodes.application_technical_planning import technical_planning_begin
from app.graph.state import ProjectState
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.application_revision_lifecycle import register_revision_impact


def _baseline() -> dict[str, Any]:
    """构造一次 formal TechnicalPlan revision 使用的 canonical baseline。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "app": {"name": "事务边界测试"},
    }


def _revision_lifecycle(workspace: Path, *, thread_id: str) -> None:
    """把应用置于可接受 TechnicalPlan formal revision 的 ready 状态。"""

    lifecycle = create_application_lifecycle(
        application_id="app-1",
        application_name="事务边界测试",
        initialization_thread_id=thread_id,
    ).model_copy(
        update={
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
                threadId=thread_id,
            )
        }
    )
    write_application_lifecycle(workspace, lifecycle, expected_revision=0)


def _build_graph(probe_count: dict[str, int]) -> Any:
    """构造 begin→probe 的真实 StateGraph，隔离模型节点以观察 durable begin。"""

    async def generation_probe(_state: ProjectState) -> dict[str, Any]:
        """模拟 begin 后的下游 generation 节点并记录实际执行次数。"""

        probe_count["generation"] += 1
        return {"phase": "technical_planning_generate", "status": "running"}

    builder = StateGraph(ProjectState)
    builder.add_node("technical_planning_begin", technical_planning_begin)
    builder.add_node("generation_probe", generation_probe)
    builder.add_edge(START, "technical_planning_begin")
    builder.add_edge("technical_planning_begin", "generation_probe")
    builder.add_edge("generation_probe", END)
    return builder.compile(checkpointer=InMemorySaver())


class TechnicalPlanningTransactionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 formal admission、lifecycle rewind 和 begin 重放幂等性。"""

    async def test_real_graph_commits_begin_once_and_replays_without_duplicate_revision(
        self,
    ) -> None:
        """真实 Graph 重放 begin 时只能复用同一 approval 与 generation lifecycle。"""

        thread_id = "technical-planning-thread"
        run_id = "technical-planning-run"
        request = "增加分页参数"
        count = {"generation": 0}
        graph = _build_graph(count)
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            _revision_lifecycle(workspace, thread_id=thread_id)
            pending = register_revision_impact(
                workspace,
                interaction_id="impact-technical-1",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request=request,
                target=RevisionTarget(type="application"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["application"],
                    reason="技术契约变化",
                ),
            )
            baseline = _baseline()
            initial_state: ProjectState = {
                "workspace": str(workspace),
                "workflow_scope": "application_planning",
                "request": request,
                "active_thread_id": thread_id,
                "active_run_id": run_id,
                "change_id": pending.change_id,
                "change_target": {"type": "application"},
                "technical_plan": baseline,
                "application_planning_recovery_boundary": application_planning_boundary_payload(
                    operation_id=f"technical-plan-revision:{pending.change_id}",
                    operation=ApplicationPlanningOperation.REVISE,
                    boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
                    request=request,
                    gate_id=pending.interaction_id,
                    baseline=baseline,
                ),
            }

            result = await graph.ainvoke(
                initial_state,
                {"configurable": {"thread_id": thread_id}},
            )
            after_graph = load_application_lifecycle(workspace)
            self.assertIsNotNone(after_graph)
            assert after_graph is not None
            self.assertEqual(count["generation"], 1)
            self.assertEqual(
                result["application_planning_recovery_boundary"]["boundary"],
                ApplicationPlanningRecoveryBoundary.GENERATION_READY.value,
            )
            self.assertEqual(
                after_graph.initialization.stage,
                ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            )
            self.assertEqual(after_graph.initialization.status, ApplicationLifecycleStatus.RUNNING)
            self.assertEqual(after_graph.active_run_id, run_id)
            self.assertIsNone(after_graph.pending_revision_impact)
            self.assertIsNotNone(after_graph.active_formal_revision)
            assert after_graph.active_formal_revision is not None
            self.assertEqual(after_graph.active_formal_revision.change_id, pending.change_id)
            revision_after_graph = after_graph.revision

            replayed = await technical_planning_begin(initial_state)
            after_replay = load_application_lifecycle(workspace)
            self.assertIsNotNone(after_replay)
            assert after_replay is not None
            self.assertEqual(
                replayed["application_planning_recovery_boundary"]["boundary"],
                ApplicationPlanningRecoveryBoundary.GENERATION_READY.value,
            )
            self.assertEqual(after_replay.revision, revision_after_graph)
            self.assertEqual(after_replay.active_run_id, run_id)
            self.assertEqual(
                after_replay.active_formal_revision.change_id
                if after_replay.active_formal_revision is not None
                else "",
                pending.change_id,
            )


__all__ = ["TechnicalPlanningTransactionRecoveryTests"]
