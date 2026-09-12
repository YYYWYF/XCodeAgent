"""TechnicalPlan 五个 durable boundary 的真实 Native Recovery crash matrix。"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    application_planning_sha256,
)
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryLifecycleOwnershipMode,
    RecoveryPoint,
)
from app.graph.application_planning_interrupts import (
    route_technical_planning_review,
    technical_planning_review,
)
from app.graph.application_planning_workflow import _route_technical_planning_generate
from app.graph.nodes.application_technical_planning import (
    technical_planning_begin,
    technical_planning_commit,
    technical_planning_confirm,
    technical_planning_generate,
)
from app.graph.state import ProjectState
from app.persistence.execution_recovery import (
    get_execution,
    insert_execution,
    mark_execution_interrupted,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.application_planning_recovery_contracts import TechnicalPlanningRecoveryContract
from app.services.application_revision_lifecycle import register_revision_impact
from app.services.execution_recovery import capture_recovery_point
from app.services.execution_recovery_coordinator import prepare_continue
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_recovery_lineage import resolve_recovery_head
from app.services.execution_recovery_policies import production_recovery_replay_policies
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from tests.helpers.native_recovery_contract import assert_native_recovery_fork_stable


@dataclass(slots=True)
class _TechnicalRecoveryScenario:
    """保存一组共享真实 checkpointer 的 source/runtime Graph 和 durable 事实。"""

    workspace: Path
    source_graph: Any
    runtime_graph: Any
    source_config: dict[str, Any]
    source: DurableExecutionRecord
    point: RecoveryPoint
    counters: dict[str, int]
    candidate: dict[str, Any]


def _baseline() -> dict[str, Any]:
    """构造当前 TechnicalPlan transaction 使用的 canonical baseline。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "app": {"name": "Native Recovery"},
    }


def _candidate() -> dict[str, Any]:
    """构造可被 production commit 节点提交的最小 TechnicalPlan candidate。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "pending_user_confirmation",
        "app": {"name": "Native Recovery candidate"},
    }


def _build_graph(
    *,
    checkpointer: InMemorySaver,
    interrupt_before: list[str] | None = None,
) -> Any:
    """构造复用 production TechnicalPlan 节点和 successor topology 的 Graph。"""

    def terminal_node(_state: ProjectState) -> dict[str, Any]:
        """提供测试图的终点，不参与 TechnicalPlan 业务判断。"""

        return {}

    builder = StateGraph(ProjectState)
    builder.add_node("technical_planning_begin", technical_planning_begin)
    builder.add_node("technical_planning_generate", technical_planning_generate)
    builder.add_node("technical_planning_commit", technical_planning_commit)
    builder.add_node("technical_planning_review", technical_planning_review)
    builder.add_node("technical_planning_confirm", technical_planning_confirm)
    builder.add_node("design_intent_analysis", terminal_node)
    builder.add_edge(START, "technical_planning_begin")
    builder.add_edge("technical_planning_begin", "technical_planning_generate")
    builder.add_conditional_edges(
        "technical_planning_generate",
        _route_technical_planning_generate,
        {
            "technical_planning_commit": "technical_planning_commit",
            "technical_planning_review": "technical_planning_review",
        },
    )
    builder.add_edge("technical_planning_commit", "technical_planning_review")
    builder.add_conditional_edges(
        "technical_planning_review",
        route_technical_planning_review,
        {
            "technical_planning_begin": "technical_planning_begin",
            "technical_planning_confirm": "technical_planning_confirm",
            "design_intent_analysis": "design_intent_analysis",
        },
    )
    builder.add_edge("technical_planning_confirm", END)
    builder.add_edge("design_intent_analysis", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or [],
    )


def _model_stub(
    counters: dict[str, int],
    candidate: dict[str, Any],
    *,
    succeeds: bool,
):
    """构造只替代模型和校验外部部分的 TechnicalPlan 生成 stub。"""

    def generate(
        _state: ProjectState,
        _requirement: dict[str, Any],
        _existing_plan: dict[str, Any] | None,
        *,
        initial_errors: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
        """记录一次模型调用并返回成功 candidate 或可审阅的失败候选。"""

        del initial_errors
        counters["model"] += 1
        if not succeeds:
            return None, ["stub TechnicalPlan generation failure"], {}
        return dict(candidate), [], None

    return generate


def _seed_lifecycle(
    workspace: Path,
    *,
    thread_id: str,
    active_run_id: str | None,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
) -> None:
    """写入测试场景需要的当前 ApplicationLifecycle predecessor。"""

    lifecycle = create_application_lifecycle(
        application_id="app-native-recovery",
        application_name="Native Recovery",
        initialization_thread_id=thread_id,
        active_run_id=active_run_id,
    ).model_copy(
        update={
            "initialization": ApplicationInitialization(
                stage=stage,
                status=status,
                threadId=thread_id,
            )
        }
    )
    write_application_lifecycle(workspace, lifecycle, expected_revision=0)


def _initial_state(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    operation: ApplicationPlanningOperation,
    request: str,
    baseline: dict[str, Any] | None,
    gate_id: str | None,
    change_id: str | None,
) -> ProjectState:
    """构造五个 boundary 共用的最小且可由 production 节点读取的 Graph State。"""

    state: ProjectState = {
        "workspace": str(workspace),
        "workflow_scope": "application_planning",
        "request": request,
        "active_thread_id": thread_id,
        "active_run_id": run_id,
        "requirement_spec": {
            "confirmation_status": "confirmed",
            "name": "Native Recovery requirements",
        },
        "product_plan": {
            "confirmation_status": "confirmed",
            "pages": [],
        },
        "ui_designs": {"confirmation_status": "skipped"},
        "application_planning_recovery_boundary": application_planning_boundary_payload(
            operation_id=f"technical-plan-{run_id}",
            operation=operation,
            boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
            request=request,
            gate_id=gate_id,
            baseline=baseline,
        ),
    }
    if baseline is not None:
        state["technical_plan"] = baseline
    if change_id is not None:
        state["change_id"] = change_id
        state["change_target"] = {"type": "application"}
    return state


class TechnicalPlanningNativeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证 TechnicalPlan boundary 从 Coordinator 到 child runtime 的完整链路。"""

    async def _scenario(
        self,
        *,
        pause_before: str,
        operation: ApplicationPlanningOperation = ApplicationPlanningOperation.INITIAL,
        lifecycle_stage: ApplicationLifecycleStage = ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
        lifecycle_status: ApplicationLifecycleStatus = ApplicationLifecycleStatus.AWAITING_USER,
        lifecycle_active_run_id: str | None = "previous-run",
        formal_revision: bool = False,
        succeeds: bool = True,
    ) -> _TechnicalRecoveryScenario:
        """建立真实 source checkpoint、RecoveryPoint 和共享 runtime Graph。"""

        raw_workspace = tempfile.TemporaryDirectory()
        workspace = Path(raw_workspace.name)
        # 将 TemporaryDirectory 的清理责任挂到测试实例，避免 scenario helper 提前删除现场。
        self.addAsyncCleanup(self._cleanup_workspace, raw_workspace)
        thread_id = f"technical-native-{pause_before}"
        source_run_id = f"run-{pause_before}"
        request = "增加分页参数"
        baseline = _baseline() if operation is not ApplicationPlanningOperation.INITIAL else None
        counters = {"model": 0}
        candidate = _candidate()

        _seed_lifecycle(
            workspace,
            thread_id=thread_id,
            active_run_id=lifecycle_active_run_id,
            stage=lifecycle_stage,
            status=lifecycle_status,
        )
        gate_id: str | None = None
        change_id: str | None = None
        if formal_revision:
            pending = register_revision_impact(
                workspace,
                interaction_id=f"impact-{source_run_id}",
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
                    reason="TechnicalPlan native recovery test",
                ),
            )
            gate_id = pending.interaction_id
            change_id = pending.change_id

        state = _initial_state(
            workspace,
            thread_id=thread_id,
            run_id=source_run_id,
            operation=operation,
            request=request,
            baseline=baseline,
            gate_id=gate_id,
            change_id=change_id,
        )
        checkpointer = InMemorySaver()
        source_graph = _build_graph(
            checkpointer=checkpointer,
            interrupt_before=[pause_before],
        )
        runtime_graph = _build_graph(checkpointer=checkpointer)
        with patch(
            "app.graph.nodes.planning._generate_valid_technical_plan",
            side_effect=_model_stub(counters, candidate, succeeds=succeeds),
        ):
            await source_graph.ainvoke(
                state,
                {"configurable": {"thread_id": thread_id}},
            )
        source_config = {"configurable": {"thread_id": thread_id}}
        source_snapshot = await source_graph.aget_state(source_config)
        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id=source_run_id,
            thread_id=thread_id,
            workspace=str(workspace),
            project_id="app-native-recovery",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node=(list(source_snapshot.next) or ["technical_planning_begin"])[0],
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        await insert_execution(source)
        point = await capture_recovery_point(
            graph=source_graph,
            config=source_config,
            workspace=str(workspace),
            thread_id=thread_id,
            run_id=source_run_id,
            workflow_scope="application_planning",
            completed_node=None,
            snapshot=source_snapshot,
        )
        assert point is not None
        return _TechnicalRecoveryScenario(
            workspace=workspace,
            source_graph=source_graph,
            runtime_graph=runtime_graph,
            source_config=source_config,
            source=source,
            point=point,
            counters=counters,
            candidate=candidate,
        )

    async def _cleanup_workspace(self, temporary_workspace: tempfile.TemporaryDirectory[str]) -> None:
        """清理单个真实 recovery store 和 checkpoint 场景。"""

        temporary_workspace.cleanup()

    async def _prepare(
        self,
        scenario: _TechnicalRecoveryScenario,
    ) -> tuple[Any, Any]:
        """先执行只读 Coordinator，再执行真实 Native Recovery claim/fork。"""

        plan = await prepare_continue(
            workspace=str(scenario.workspace),
            source_run_id=scenario.source.run_id,
            graph=scenario.runtime_graph,
            replay_policies=production_recovery_replay_policies(),
        )
        context = await prepare_native_recovery(
            workspace=str(scenario.workspace),
            source_run_id=scenario.source.run_id,
            graph=scenario.runtime_graph,
            replay_policies=production_recovery_replay_policies(),
        )
        return plan, context

    async def _run_child(
        self,
        scenario: _TechnicalRecoveryScenario,
        context: Any,
        *,
        succeeds: bool = True,
    ) -> Any:
        """从真实 fork checkpoint 运行 child，直到下一个审阅 interrupt 或异常。"""

        try:
            with patch(
                "app.graph.nodes.planning._generate_valid_technical_plan",
                side_effect=_model_stub(
                    scenario.counters,
                    scenario.candidate,
                    succeeds=succeeds,
                ),
            ):
                await scenario.runtime_graph.ainvoke(
                    None,
                    config=context.fork_config,
                )
        finally:
            await stop_execution_heartbeat(context.heartbeat_task)
        return await scenario.runtime_graph.aget_state(context.observation_config)

    async def test_formal_input_committed_uses_pre_ownership_claim(self) -> None:
        """Formal INPUT_COMMITTED 必须先 claim lifecycle，再 fork 并进入 generation。"""

        scenario = await self._scenario(
            pause_before="technical_planning_begin",
            operation=ApplicationPlanningOperation.REVISE,
            lifecycle_stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            lifecycle_status=ApplicationLifecycleStatus.COMPLETED,
            formal_revision=True,
        )
        plan, context = await self._prepare(scenario)
        self.assertEqual(plan.decision, RecoveryDecision.READY_NATIVE)
        self.assertEqual(
            plan.lifecycle_ownership_mode,
            RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )
        self.assertEqual(context.thread_id, scenario.source.thread_id)
        self.assertNotEqual(context.new_run_id, scenario.source.run_id)
        child_snapshot = await self._run_child(scenario, context)
        lifecycle = load_application_lifecycle(scenario.workspace)
        self.assertIsNotNone(lifecycle)
        assert lifecycle is not None
        self.assertEqual(lifecycle.active_run_id, context.new_run_id)
        self.assertEqual(
            lifecycle.initialization.stage,
            ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        )
        self.assertIsNotNone(lifecycle.active_formal_revision)
        self.assertEqual(tuple(child_snapshot.next), ("technical_planning_review",))

    async def test_initial_input_committed_uses_pre_ownership_claim(self) -> None:
        """首次进入 TechnicalPlan 的 INPUT_COMMITTED 也必须完整经过 pre-ownership。"""

        scenario = await self._scenario(
            pause_before="technical_planning_begin",
            lifecycle_stage=ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
            lifecycle_status=ApplicationLifecycleStatus.AWAITING_USER,
        )
        plan, context = await self._prepare(scenario)
        self.assertEqual(plan.lifecycle_ownership_mode, RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP)
        child_snapshot = await self._run_child(scenario, context)
        self.assertEqual(tuple(child_snapshot.next), ("technical_planning_review",))
        self.assertEqual(scenario.counters["model"], 1)

    async def test_review_revise_input_committed_uses_pre_ownership_claim(self) -> None:
        """非 FormalRevision 的 TechnicalPlan review revise 也必须使用 pre-ownership。"""

        scenario = await self._scenario(
            pause_before="technical_planning_begin",
            operation=ApplicationPlanningOperation.REVISE,
            lifecycle_stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            lifecycle_status=ApplicationLifecycleStatus.AWAITING_USER,
            lifecycle_active_run_id="review-producing-run",
        )
        plan, context = await self._prepare(scenario)
        self.assertEqual(plan.lifecycle_ownership_mode, RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP)
        lifecycle = load_application_lifecycle(scenario.workspace)
        self.assertIsNotNone(lifecycle)
        assert lifecycle is not None
        self.assertEqual(lifecycle.active_run_id, context.new_run_id)
        await self._run_child(scenario, context)
        self.assertEqual(scenario.counters["model"], 1)

    async def test_input_committed_source_owned_window_is_idempotent(self) -> None:
        """begin 已成功取得 source ownership 的 crash window 不得重复 lifecycle revision。"""

        scenario = await self._scenario(
            pause_before="technical_planning_begin",
            lifecycle_stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            lifecycle_status=ApplicationLifecycleStatus.RUNNING,
            lifecycle_active_run_id="run-technical_planning_begin",
        )
        before = load_application_lifecycle(scenario.workspace)
        self.assertIsNotNone(before)
        assert before is not None
        plan, context = await self._prepare(scenario)
        self.assertEqual(plan.lifecycle_ownership_mode, RecoveryLifecycleOwnershipMode.SOURCE_OWNED)
        await self._run_child(scenario, context)
        after = load_application_lifecycle(scenario.workspace)
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(after.initialization.stage, ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN)
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(scenario.counters["model"], 1)

    async def test_generation_ready_recovery_allows_one_model_call(self) -> None:
        """GENERATION_READY 恢复必须只新增一次尚未 durable 的模型调用。"""

        scenario = await self._scenario(pause_before="technical_planning_generate")
        plan, context = await self._prepare(scenario)
        self.assertEqual(plan.lifecycle_ownership_mode, RecoveryLifecycleOwnershipMode.SOURCE_OWNED)
        await self._run_child(scenario, context)
        self.assertEqual(scenario.counters["model"], 1)

    async def test_candidate_committed_recovery_does_not_repeat_model(self) -> None:
        """CANDIDATE_COMMITTED 恢复只能进入 commit，模型调用次数保持一次。"""

        scenario = await self._scenario(pause_before="technical_planning_commit")
        self.assertEqual(scenario.counters["model"], 1)
        _plan, context = await self._prepare(scenario)
        child_snapshot = await self._run_child(scenario, context)
        self.assertEqual(tuple(child_snapshot.next), ("technical_planning_review",))
        self.assertEqual(scenario.counters["model"], 1)

    async def test_partial_commit_converges_without_model_replay(self) -> None:
        """JSON 已写入而 Markdown 失败后，下一次 child 必须幂等收敛双文件。"""

        scenario = await self._scenario(pause_before="technical_planning_commit")
        _plan, first_context = await self._prepare(scenario)
        from app.workspace import plan_documents

        original_writer = plan_documents._write_text_atomically

        def fail_markdown(path: Path, content: str) -> None:
            """模拟 Markdown replace 失败，同时保留 JSON replace 成功。"""

            if path.suffix == ".md":
                raise OSError("injected markdown replace failure")
            original_writer(path, content)

        with patch(
            "app.graph.nodes.planning._generate_valid_technical_plan",
            side_effect=_model_stub(scenario.counters, scenario.candidate, succeeds=True),
        ), patch("app.workspace.plan_documents._write_text_atomically", side_effect=fail_markdown):
            with self.assertRaises(OSError):
                await scenario.runtime_graph.ainvoke(None, config=first_context.fork_config)
        await stop_execution_heartbeat(first_context.heartbeat_task)
        child_id = first_context.new_run_id
        await mark_execution_interrupted(
            workspace=scenario.workspace,
            run_id=child_id,
            interrupted_at=datetime.now(timezone.utc),
        )
        head_before = await resolve_recovery_head(str(scenario.workspace), scenario.source.run_id)
        self.assertEqual(head_before, child_id)
        _child_plan, second_context = await self._prepare(
            _TechnicalRecoveryScenario(
                workspace=scenario.workspace,
                source_graph=scenario.source_graph,
                runtime_graph=scenario.runtime_graph,
                source_config=scenario.source_config,
                source=await get_execution(scenario.workspace, child_id),
                point=scenario.point,
                counters=scenario.counters,
                candidate=scenario.candidate,
            )
        )
        await self._run_child(scenario, second_context)
        json_path = scenario.workspace / ".xcodeagent" / "plans" / "technical-plan.json"
        markdown_path = scenario.workspace / ".xcodeagent" / "plans" / "technical-plan.md"
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertEqual(
            application_planning_sha256(json.loads(json_path.read_text(encoding="utf-8"))),
            application_planning_sha256(scenario.candidate),
        )
        self.assertEqual(scenario.counters["model"], 1)

    async def test_artifact_committed_recovery_only_enters_review_interrupt(self) -> None:
        """ARTIFACT_COMMITTED 恢复不得再次执行 generate 或 commit。"""

        scenario = await self._scenario(pause_before="technical_planning_review")
        _plan, context = await self._prepare(scenario)
        child_snapshot = await self._run_child(scenario, context)
        self.assertTrue(any(task.interrupts for task in child_snapshot.tasks))
        self.assertEqual(scenario.counters["model"], 1)

    async def test_review_ready_recovery_does_not_repeat_failed_model(self) -> None:
        """REVIEW_READY 生成失败边界恢复必须直接生成真实 review interrupt。"""

        scenario = await self._scenario(
            pause_before="technical_planning_review",
            succeeds=False,
        )
        _plan, context = await self._prepare(scenario)
        child_snapshot = await self._run_child(scenario, context, succeeds=False)
        self.assertTrue(any(task.interrupts for task in child_snapshot.tasks))
        self.assertEqual(scenario.counters["model"], 1)

    async def test_native_interrupt_has_priority_over_recovery_contract(self) -> None:
        """进入真实 review interrupt 后，下一次 GET 必须是 awaiting_user 而非 READY_NATIVE。"""

        scenario = await self._scenario(pause_before="technical_planning_review")
        _plan, context = await self._prepare(scenario)
        snapshot = await self._run_child(scenario, context)
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        await capture_recovery_point(
            graph=scenario.runtime_graph,
            config=context.observation_config,
            workspace=str(scenario.workspace),
            thread_id=context.thread_id,
            run_id=context.new_run_id,
            workflow_scope="application_planning",
            snapshot=snapshot,
        )
        await mark_execution_interrupted(
            workspace=scenario.workspace,
            run_id=context.new_run_id,
            interrupted_at=datetime.now(timezone.utc),
        )
        recovery = await prepare_continue(
            workspace=str(scenario.workspace),
            source_run_id=context.new_run_id,
            graph=scenario.runtime_graph,
            replay_policies=production_recovery_replay_policies(),
        )
        self.assertEqual(recovery.decision, RecoveryDecision.AWAITING_USER)

    async def test_all_five_boundaries_are_fork_stable(self) -> None:
        """五个 production boundary 的 identity-only fork 必须保留 successor 集合。"""

        cases = (
            ("technical_planning_begin", True),
            ("technical_planning_generate", True),
            ("technical_planning_commit", True),
            ("technical_planning_review", True),
            ("technical_planning_review", False),
        )
        for pause_before, succeeds in cases:
            with self.subTest(pause_before=pause_before, succeeds=succeeds):
                scenario = await self._scenario(
                    pause_before=pause_before,
                    succeeds=succeeds,
                )
                source = await scenario.runtime_graph.aget_state(scenario.source_config)
                await assert_native_recovery_fork_stable(
                    testcase=self,
                    graph=scenario.runtime_graph,
                    checkpoint_config=source.config,
                    expected_next_nodes=list(source.next),
                    runtime_identity_update={
                        "active_run_id": "fork-stability-child",
                        "active_thread_id": scenario.source.thread_id,
                        "resume_from": "",
                        "observability": {"run_id": "fork-stability-child"},
                    },
                )
                _plan, context = await self._prepare(scenario)
                await stop_execution_heartbeat(context.heartbeat_task)

    async def test_recovery_child_crash_continues_from_child_lineage(self) -> None:
        """source child 在 commit 前再次崩溃时，下一次 recovery 必须沿 child lineage 继续。"""

        scenario = await self._scenario(pause_before="technical_planning_commit")
        _plan, first_context = await self._prepare(scenario)
        await stop_execution_heartbeat(first_context.heartbeat_task)
        await mark_execution_interrupted(
            workspace=scenario.workspace,
            run_id=first_context.new_run_id,
            interrupted_at=datetime.now(timezone.utc),
        )
        head = await resolve_recovery_head(str(scenario.workspace), scenario.source.run_id)
        self.assertEqual(head, first_context.new_run_id)
        child = await get_execution(scenario.workspace, first_context.new_run_id)
        self.assertIsNotNone(child)
        assert child is not None
        child_scenario = _TechnicalRecoveryScenario(
            workspace=scenario.workspace,
            source_graph=scenario.source_graph,
            runtime_graph=scenario.runtime_graph,
            source_config=scenario.source_config,
            source=child,
            point=scenario.point,
            counters=scenario.counters,
            candidate=scenario.candidate,
        )
        _plan, second_context = await self._prepare(child_scenario)
        self.assertEqual(second_context.thread_id, scenario.source.thread_id)
        self.assertNotEqual(second_context.new_run_id, first_context.new_run_id)
        await stop_execution_heartbeat(second_context.heartbeat_task)

    async def test_pre_ownership_negative_identity_matrix_fails_closed(self) -> None:
        """Formal pre-ownership 的请求、baseline、target 和 lifecycle 漂移不得越权放行。"""

        scenario = await self._scenario(
            pause_before="technical_planning_begin",
            operation=ApplicationPlanningOperation.REVISE,
            lifecycle_stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            lifecycle_status=ApplicationLifecycleStatus.COMPLETED,
            formal_revision=True,
        )
        snapshot = await scenario.runtime_graph.aget_state(scenario.source_config)
        boundary = dict(snapshot.values["application_planning_recovery_boundary"])
        cases = (
            {"change_id": "wrong-change"},
            {"change_target": {"type": "page"}},
            {"application_planning_recovery_boundary": {**boundary, "gateId": "wrong-gate"}},
            {"application_planning_recovery_boundary": {**boundary, "requestSha256": "0" * 64}},
            {"application_planning_recovery_boundary": {**boundary, "baselineSha256": "0" * 64}},
        )
        for update in cases:
            with self.subTest(update=update):
                candidate_config = await scenario.runtime_graph.aupdate_state(
                    scenario.source_config,
                    update,
                )
                candidate_snapshot = await scenario.runtime_graph.aget_state(candidate_config)
                candidate_point = scenario.point.model_copy(
                    update={
                        "checkpoint_id": candidate_config["configurable"]["checkpoint_id"],
                    }
                )
                lifecycle = load_application_lifecycle(scenario.workspace)
                self.assertIsNotNone(lifecycle)
                assert lifecycle is not None
                contract = TechnicalPlanningRecoveryContract()
                matches = contract.match(
                    source=scenario.source,
                    point=candidate_point,
                    snapshot=candidate_snapshot,
                )
                assessment = contract.assess_lifecycle(
                    source=scenario.source,
                    point=candidate_point,
                    snapshot=candidate_snapshot,
                    lifecycle=lifecycle,
                ) if matches else None
                self.assertTrue(not matches or not assessment.compatible)


__all__ = ["TechnicalPlanningNativeRecoveryTests"]
