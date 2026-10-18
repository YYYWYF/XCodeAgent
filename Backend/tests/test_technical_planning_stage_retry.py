"""技术规划阶段重试沿用现有生成节点和上次业务输入的回归。"""

from copy import deepcopy
import asyncio
import unittest
from unittest.mock import patch

from app.domain.application_planning_recovery import ApplicationPlanningOperation
from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_revision import ActiveFormalRevision, RevisionTarget
from app.domain.execution_recovery import DurableExecutionStatus
from app.graph.application_planning_interrupts import _technical_input_boundary
from app.graph.nodes.application_technical_planning import technical_planning_begin, technical_planning_generate
from app.protocols.workflow.run_control import build_workflow_cancellation_ag_ui_stream
from app.persistence.execution_recovery import get_execution
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.protocols.application_page_planning import _build_application_planning_recovery_projection
from app.workspace.plan_documents import commit_technical_plan_document
from app.services.application_lifecycle import (
    load_application_lifecycle, persist_application_lifecycle_transition, write_application_lifecycle,
)
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalPlanningStageRetryTests(unittest.IsolatedAsyncioTestCase):
    """使用真实持久化 Graph 验证 checkpoint 恢复和业务重执行保留原输入。"""

    async def asyncSetUp(self) -> None:
        """创建隔离工作区和已确认的上游资产。"""
        self.harness = TechnicalPlanningRecoveryJourneyHarness()

    async def asyncTearDown(self) -> None:
        """关闭连接并清理测试工作区。"""
        await close_workflow_checkpointer_for_workspace(workspace=str(self.harness.workspace))
        self.harness.close()

    async def _retry(self, *, has_plan: bool, native: bool, commit_failure: bool = False) -> None:
        """从生成中断或异常失败重开，检查模型实际收到的基线和修改要求。"""
        harness = self.harness
        harness.request = "删除问题文件下载接口，其他内容保留。"
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            config = {"configurable": {"thread_id": harness.thread_id}}
            snapshot = await harness.graph.aget_state(config)
            state = dict(snapshot.values)
            baseline = deepcopy(harness.candidate) if has_plan else {}
            state["technical_plan"] = deepcopy(baseline)
            if baseline:
                # 磁盘计划为最新基线；checkpoint 中仍可保留旧版本。
                baseline["summary"] = "磁盘最新技术规划"
                commit_technical_plan_document(state, baseline)
            state["technical_plan_repair_candidate"] = {"summary": "不应复用的失败候选"}
            state["technical_plan_repair_errors"] = ["上次错误"]
            state["application_planning_recovery_boundary"] = _technical_input_boundary(
                state, operation=ApplicationPlanningOperation.REPAIR,
                gate_id="retry-gate", artifact_revision="retry-revision", request=harness.request,
            )
            await harness.graph.aupdate_state(snapshot.config, state, as_node="workflow_entry")
            if native:
                if commit_failure:
                    with (
                        patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(harness.candidate), [], None)),
                        patch("app.graph.nodes.application_technical_planning.commit_technical_plan_document", side_effect=RuntimeError("暂时无法写入")),
                    ):
                        await harness.run_initial()
                else:
                    with patch("app.graph.nodes.planning._generate_valid_technical_plan", side_effect=RuntimeError("模型暂时不可用")):
                        await harness.run_initial()
            else:
                # 精确复现附件：begin 已提交 GENERATION_READY，下一次请求却要求重跑技术规划。
                prepared = await technical_planning_begin(state)
                await harness.graph.aupdate_state(config, prepared, as_node="technical_planning_begin")
            with (
                patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(harness.candidate)) as generate,
                patch("app.graph.nodes.planning._repair_technical_plan_candidate") as repair,
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                if native:
                    _, _, action = await harness.resolve_action()
                    child = await harness.execute_action(action)
                    self.assertEqual(child.first_node, "technical_planning_begin")
                    self.assertEqual(child.status, DurableExecutionStatus.AWAITING_USER, "\n".join(
                        frame for frame in harness.last_recovery_frames if "RUN_ERROR" in frame
                    ))
                else:
                    frames = [frame async for frame in build_workflow_ag_ui_stream(
                        graph=harness.graph, payload={
                            "threadId": harness.thread_id, "runId": "explicit-stage-retry",
                            "request": "请从上次保存的规划状态继续执行。",
                            "resume_from": "technical_planning",
                            "forwardedProps": {"workspaceRoot": str(harness.workspace), "workflowScope": "application_planning"},
                        },
                    )]
                    self.assertFalse(any('"type": "RUN_ERROR"' in frame for frame in frames))
                if native and commit_failure and not has_plan:
                    # 保存失败的候选仍可由 checkpoint 安全提交，无需再次请求生成模型。
                    generate.assert_not_called()
                else:
                    generate.assert_called_once()
                repair.assert_not_called()
                if generate.call_args is not None:
                    self.assertEqual(generate.call_args.args[0]["planning_adjustment_request"], harness.request)
                    if has_plan:
                        self.assertEqual(generate.call_args.kwargs["existing_plan"]["summary"], "磁盘最新技术规划")
                    else:
                        self.assertNotIn("existing_plan", generate.call_args.kwargs)
            final = await harness.graph.aget_state(config)
            self.assertEqual(final.values["request"], harness.request)
            self.assertEqual(tuple(final.next), ("technical_planning_review",))
            self.assertTrue(any(task.interrupts for task in final.tasks))
            self.assertEqual(final.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")

    async def test_explicit_retry_with_plan(self) -> None:
        """普通阶段重试携带最新计划和原要求。"""
        await self._retry(has_plan=True, native=False)

    async def test_explicit_retry_without_plan(self) -> None:
        """普通阶段重试无计划时使用原生成节点首次生成分支。"""
        await self._retry(has_plan=False, native=False)

    async def test_native_retry_with_plan(self) -> None:
        """生成节点异常后，底部重试重新生成并保留计划基线。"""
        await self._retry(has_plan=True, native=True)

    async def test_native_retry_without_plan(self) -> None:
        """生成节点异常且无计划时，底部重试直接重新生成。"""
        await self._retry(has_plan=False, native=True)

    async def test_commit_retry_with_plan(self) -> None:
        """保存失败时以已有计划重新生成，而不是重放旧候选提交。"""
        await self._retry(has_plan=True, native=True, commit_failure=True)

    async def test_commit_retry_without_plan(self) -> None:
        """保存失败且磁盘无新基线时，checkpoint 可提交候选并保留原问题。"""
        await self._retry(has_plan=False, native=True, commit_failure=True)

    async def _revise_formal_plan(self, *, native: bool, design: bool = False) -> None:
        """真实 Graph 用当前要求和审阅门重跑原修订，不再比对最初的请求与影响确认门。"""
        harness = self.harness
        harness.request = "将分页默认值改为8，保留其他接口契约。"
        lifecycle = persist_application_lifecycle_transition(
            harness.workspace,
            stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            active_run_id=harness.source_run_id,
        )
        active = ActiveFormalRevision(
            changeId="chg-current-technical-revision",
            formalBranch="design_stage_revision" if design else "workbench_plan_revision",
            sourceThreadId="development-thread", sourceRunId="development-run",
            request="将分页默认值改为7。", target=RevisionTarget(type="application"),
            impactInteractionId="original-impact-gate", planningThreadId=harness.thread_id,
            status="design_planning" if design else "drafting", currentArtifact="technical-plan", remainingArtifacts=[],
        )
        write_application_lifecycle(
            harness.workspace,
            lifecycle.model_copy(update={
                "revision": lifecycle.revision + 1, "active_formal_revision": active,
            }),
            expected_revision=lifecycle.revision,
        )
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            config = {"configurable": {"thread_id": harness.thread_id}}
            snapshot = await harness.graph.aget_state(config)
            state = {
                **snapshot.values,
                "change_id": active.change_id,
                "change_target": {"type": "application"},
                "technical_plan": deepcopy(harness.candidate),
            }
            state["application_planning_recovery_boundary"] = _technical_input_boundary(
                state, operation=ApplicationPlanningOperation.REVISE,
                gate_id="current-technical-review-gate", artifact_revision="current-plan",
                request=harness.request,
            )
            commit_technical_plan_document(state, harness.candidate)
            await harness.graph.aupdate_state(snapshot.config, state, as_node="workflow_entry")
            if native:
                # 让 begin 在交接前失败，确保底部恢复确实经过 INPUT_COMMITTED 入场校验。
                with patch(
                    ("app.graph.nodes.application_technical_planning.ensure_technical_plan_generation_lifecycle" if design
                     else "app.graph.nodes.application_technical_planning.begin_technical_plan_revision_generation"),
                    side_effect=RuntimeError("模拟开始生成前中断"),
                ):
                    await harness.run_initial()
                source, _, action = await harness.resolve_action()
                self.assertEqual(source.current_node, "technical_planning_begin")
            with (
                patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(harness.candidate)) as generate,
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                if native:
                    child = await harness.execute_action(action)
                    self.assertEqual(child.first_node, "technical_planning_begin")
                    self.assertEqual(child.status, DurableExecutionStatus.AWAITING_USER, "\n".join(harness.last_recovery_frames))
                else:
                    await harness.run_initial()
                generate.assert_called_once()
                self.assertEqual(generate.call_args.args[0]["planning_adjustment_request"], harness.request)
            final = await harness.graph.aget_state(config)
            self.assertEqual(tuple(final.next), ("technical_planning_review",))
            self.assertTrue(any(task.interrupts for task in final.tasks))
            self.assertEqual(final.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
            persisted = load_application_lifecycle(harness.workspace)
            self.assertEqual(persisted.active_formal_revision.change_id, active.change_id)
            self.assertEqual(persisted.active_formal_revision.request, active.request)

    async def test_formal_revision_accepts_current_modification_request(self) -> None:
        """同一次正式修订允许用新审阅门和新要求再次生成，仍停在确认门。"""
        await self._revise_formal_plan(native=False)

    async def test_formal_revision_native_retry_accepts_current_modification_request(self) -> None:
        """底部兜底恢复回到原 begin 节点，当前修改不必等于最初修订请求。"""
        await self._revise_formal_plan(native=True)

    async def test_design_revision_enters_technical_generation(self) -> None:
        """设计修订重开技术生成时保留原修订，并重新等待确认。"""
        await self._revise_formal_plan(native=False, design=True)

    async def test_design_revision_entry_failure_can_retry(self) -> None:
        """设计修订在技术 begin 失败后仍可沿真实 Native Recovery 生成并回到确认门。"""
        await self._revise_formal_plan(native=True, design=True)

    async def test_pause_generation_can_continue_through_recovery(self) -> None:
        """真实取消控制把技术规划暂停落为中断，并沿原恢复动作重新生成、等待确认。"""
        harness = self.harness
        started = asyncio.Event()

        async def generate_with_pause(state):
            """仅首轮阻塞原生成节点，为真实取消请求提供确定的执行窗口。"""
            if not started.is_set():
                started.set()
                await asyncio.Future()
            return technical_planning_generate(state)

        with (
            patch("app.config.Settings.from_env", side_effect=harness.current_settings),
            patch("app.graph.application_planning_workflow.technical_planning_generate", new=generate_with_pause),
        ):
            await harness.initialize_graph()
            task = asyncio.create_task(harness.run_initial())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                frames = [frame async for frame in build_workflow_cancellation_ag_ui_stream(
                    thread_id="pause-control-thread", run_id="pause-control-run",
                    target_run_id=harness.source_run_id, workspace=str(harness.workspace),
                )]
                self.assertIn('"status":"cancelled"', "".join(frames))
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            source = await get_execution(harness.workspace, harness.source_run_id)
            self.assertEqual(source.status, DurableExecutionStatus.INTERRUPTED)
            # aget_state 可叠加未提交的模型返回并呈现空 next；恢复仍应读取
            # history 中已提交的 generate 入口，不能把展示快照当成完成事实。
            committed = await harness.graph.aget_state({"configurable": {"thread_id": harness.thread_id}})
            projected = await _build_application_planning_recovery_projection(
                workspace=str(harness.workspace), thread_id=harness.thread_id,
                graph=harness.graph, source=source,
                snapshot=committed._replace(next=(), tasks=()),
            )
            self.assertEqual(projected.classification, "ready_to_continue")
            self.assertEqual((await get_execution(harness.workspace, harness.source_run_id)).status,
                             DurableExecutionStatus.INTERRUPTED)
            _, projection, action = await harness.resolve_action()
            self.assertEqual(projection.classification, "ready_to_continue")
            with (
                patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(harness.candidate)) as generate,
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                child = await harness.execute_action(action)
            generate.assert_called_once()
            self.assertEqual(child.status, DurableExecutionStatus.AWAITING_USER)
