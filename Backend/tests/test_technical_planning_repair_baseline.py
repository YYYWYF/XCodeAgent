"""TechnicalPlan 无基线失败续修、已有基线校验与统一恢复的回归。"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryExecutionError,
    RecoveryPlan,
)
from app.graph.application_planning_interrupts import _technical_input_boundary
from app.graph.nodes.application_technical_planning import technical_planning_begin
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import load_application_lifecycle
from app.services.execution_recovery_executor import _apply_application_planning_transaction_authority
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalPlanningRepairBaselineTests(unittest.IsolatedAsyncioTestCase):
    """验证两处入口使用相同基线规则，并经真实恢复流程回到内部生成。"""

    async def asyncSetUp(self) -> None:
        """准备正式上游产物、当前生命周期和隔离工作区。"""

        self.harness = TechnicalPlanningRecoveryJourneyHarness()
        self.addAsyncCleanup(self._cleanup_harness)

    async def _cleanup_harness(self) -> None:
        """先关闭 checkpoint 连接，再清理测试工作区。"""

        await close_workflow_checkpointer_for_workspace(
            workspace=str(self.harness.workspace), project_id=self.harness.project_id,
        )
        self.harness.close()

    def _state(self, operation, baseline, boundary_baseline):
        """独立构造当前计划与事务摘要，以覆盖缺失、匹配和篡改场景。"""

        harness = self.harness
        return {
            "workspace": str(harness.workspace),
            "active_thread_id": harness.thread_id,
            "active_run_id": harness.source_run_id,
            "request": harness.request,
            "technical_plan": baseline,
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="technical-plan-baseline-test", operation=operation,
                boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
                request=harness.request, baseline=boundary_baseline,
            ),
        }

    async def _recover_authority(self, state):
        """直接运行兜底执行器的生产事务校验，使用真实 lifecycle authority。"""

        harness = self.harness
        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id=harness.source_run_id, thread_id=harness.thread_id,
            workspace=str(harness.workspace), execution_kind="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin", status=DurableExecutionStatus.INTERRUPTED,
            started_at=now, updated_at=now,
        )
        plan = RecoveryPlan(
            source_run_id=source.run_id, thread_id=source.thread_id,
            target_node=source.current_node, checkpoint_id="test-entry",
            lifecycle_revision=load_application_lifecycle(harness.workspace).revision,
        )
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=state)))
        return await _apply_application_planning_transaction_authority(
            workspace=str(harness.workspace), source=source, graph=graph, plan=plan,
        )

    async def test_both_entries_preserve_existing_baseline_validation(self) -> None:
        """无基线 repair 放行；已有计划、已有摘要与 revise 均保持严格一致性校验。"""

        plan = deepcopy(self.harness.candidate)
        changed = {**plan, "app": {"name": "changed"}}
        cases = (
            ("repair_absent", ApplicationPlanningOperation.REPAIR, None, None, True),
            ("repair_empty", ApplicationPlanningOperation.REPAIR, {}, None, True),
            ("repair_matching", ApplicationPlanningOperation.REPAIR, plan, plan, True),
            ("repair_missing_digest", ApplicationPlanningOperation.REPAIR, plan, None, False),
            ("repair_missing_plan", ApplicationPlanningOperation.REPAIR, {}, plan, False),
            ("repair_changed_plan", ApplicationPlanningOperation.REPAIR, changed, plan, False),
            ("repair_invalid_plan", ApplicationPlanningOperation.REPAIR, [], None, False),
            ("revise_absent", ApplicationPlanningOperation.REVISE, {}, None, False),
            ("revise_matching", ApplicationPlanningOperation.REVISE, plan, plan, True),
            ("revise_changed_plan", ApplicationPlanningOperation.REVISE, changed, plan, False),
        )
        for label, operation, baseline, digest_plan, allowed in cases:
            for entry in ("node", "executor"):
                with self.subTest(case=label, entry=entry):
                    state = self._state(operation, baseline, digest_plan)
                    if entry == "node":
                        call = technical_planning_begin(state)
                        error = ValueError
                    else:
                        call = self._recover_authority(state)
                        error = RecoveryExecutionError
                    if allowed:
                        await call
                    else:
                        with self.assertRaisesRegex(error, "baseline"):
                            await call

    async def _retry_failed_begin(self, *, has_candidate: bool, before_handoff: bool = False) -> None:
        """通过公开 AG-UI 重试失败 begin，验证原生成节点重新执行及确认门。"""

        harness = self.harness
        seed = {**deepcopy(harness.candidate), "app": {"name": "失败候选"}}
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            snapshot = await harness.graph.aget_state({"configurable": {"thread_id": harness.thread_id}})
            state = {
                **snapshot.values,
                "technical_plan_repair_candidate": seed if has_candidate else {},
                "technical_plan_repair_errors": ["候选契约错误"],
            }
            state["application_planning_recovery_boundary"] = _technical_input_boundary(
                state, operation=ApplicationPlanningOperation.REPAIR,
                gate_id="retry-gate", artifact_revision="retry-revision", request=harness.request,
            )
            if before_handoff:
                # 审阅已经提交到新 Run，但 begin 尚未完成原生成 Run 的归属交接。
                state["technical_plan_repair_source_run_id"] = harness.source_run_id
                harness.source_run_id = "technical-plan-review-retry"
                state["active_run_id"] = harness.source_run_id
            self.assertNotIn("baselineSha256", state["application_planning_recovery_boundary"])
            await harness.graph.aupdate_state(snapshot.config, state, as_node="workflow_entry")
            # 模拟 begin 内部一次异常，随后由底部重试整理新一轮生成输入。
            with patch(
                "app.graph.nodes.application_technical_planning._ensure_technical_lifecycle",
                side_effect=ValueError("测试技术入口临时失败"),
            ):
                failure_frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=harness.graph,
                    payload={
                        "threadId": harness.thread_id, "runId": harness.source_run_id,
                        "request": harness.request, "resume_from": "technical_planning_begin",
                        "forwardedProps": {
                            "workspaceRoot": str(harness.workspace), "workflowScope": "application_planning",
                        },
                    },
                )]
            self.assertNotIn("technical_plan_repair_source_run_id", "".join(failure_frames))
            source, _projection, action = await harness.resolve_action()
            self.assertEqual(source.status, DurableExecutionStatus.FAILED)
            self.assertEqual(source.current_node, "technical_planning_begin")
            with (
                patch("app.graph.nodes.planning._repair_technical_plan_candidate", return_value=deepcopy(harness.candidate)) as repair,
                patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(harness.candidate)) as generate,
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                child = await harness.execute_action(action)
            self.assertNotEqual(child.run_id, source.run_id)
            self.assertEqual(child.first_node, "technical_planning_begin")
            self.assertEqual(child.status, DurableExecutionStatus.AWAITING_USER)
            generate.assert_called_once()
            repair.assert_not_called()
            self.assertNotIn("existing_plan", generate.call_args.kwargs)
            recovered = await harness.graph.aget_state({"configurable": {"thread_id": harness.thread_id}})
            self.assertEqual(tuple(recovered.next), ("technical_planning_review",))
            self.assertTrue(any(task.interrupts for task in recovered.tasks))
            self.assertEqual(recovered.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
            self.assertEqual(recovered.values["technical_plan_repair_candidate"], {})
            self.assertEqual(recovered.values["technical_plan_repair_source_run_id"], "")
            events = [
                json.loads(line[5:].strip())
                for frame in harness.last_recovery_frames for line in frame.splitlines()
                if line.startswith("data:")
            ]
            self.assertEqual(events[0]["type"], "RUN_STARTED")
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            self.assertTrue(any(event["type"] == "STATE_SNAPSHOT" for event in events))
            self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events))

    async def test_native_failure_retry_regenerates_ignoring_failed_candidate(self) -> None:
        """首次失败只有内部候选时，复用原节点重新生成并等待确认。"""

        await self._retry_failed_begin(has_candidate=True)

    async def test_native_failure_retry_regenerates_without_candidate(self) -> None:
        """首次失败没有可解析候选时，兜底重入后重新生成并重新等待确认。"""

        await self._retry_failed_begin(has_candidate=False)

    async def test_native_failure_retry_before_review_handoff(self) -> None:
        """失败审阅已提交但 begin 尚未交接时，兜底仍可领取新 Run 并进入内部修复。"""

        await self._retry_failed_begin(has_candidate=True, before_handoff=True)

    async def test_repair_cannot_claim_unrelated_generation_run(self) -> None:
        """失败续修来源为空或不属于当前 lifecycle 时，两处入口都拒绝抢占 Run。"""

        owner = self.harness.source_run_id
        self.harness.source_run_id = "unrelated-repair-run"
        for repair_source in ("", "another-generation-run"):
            for entry in ("node", "executor"):
                with self.subTest(source=repair_source, entry=entry):
                    state = self._state(ApplicationPlanningOperation.REPAIR, {}, None)
                    state["technical_plan_repair_source_run_id"] = repair_source
                    if entry == "node":
                        with self.assertRaisesRegex(ValueError, "activeRunId"):
                            await technical_planning_begin(state)
                    else:
                        with self.assertRaises(RecoveryExecutionError) as raised:
                            await self._recover_authority(state)
                        self.assertEqual(raised.exception.code, "LIFECYCLE_DRIFT")
                    self.assertEqual(load_application_lifecycle(self.harness.workspace).active_run_id, owner)

    async def test_initial_generation_error_review_retries_without_baseline(self) -> None:
        """初次生成耗尽内部预算后，原审阅门重新生成，不使用上次失败候选。"""

        harness = self.harness
        seed = deepcopy(harness.candidate)
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            with patch(
                "app.graph.nodes.planning._generate_valid_technical_plan",
                return_value=(None, ["候选契约错误"], seed),
            ):
                await harness.run_initial()
            config = {"configurable": {"thread_id": harness.thread_id}}
            failed = await harness.graph.aget_state(config)
            self.assertEqual(failed.values["technical_plan"], {})
            pending = failed.tasks[0].interrupts[0].value
            self.assertEqual(pending["clarification"]["mode"], "technical_plan_generation_error")
            request = "请重新生成技术规划，并修复全部结构和契约错误。"
            with (
                patch("app.graph.nodes.planning._repair_technical_plan_candidate", return_value=deepcopy(harness.candidate)) as repair,
                patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(harness.candidate)) as generate,
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=harness.graph,
                    payload={
                        "threadId": harness.thread_id, "runId": "technical-plan-review-retry",
                        "message": request,
                        "forwardedProps": {
                            "workspaceRoot": str(harness.workspace), "workflowScope": "application_planning",
                            "applicationPlanningInteraction": {
                                "gateId": pending["gateId"], "artifact": pending["artifact"],
                                "artifactRevision": pending["artifactRevision"], "action": "revise", "request": request,
                            },
                        },
                    },
                )]
            generate.assert_called_once()
            repair.assert_not_called()
            self.assertTrue(frames)
            recovered = await harness.graph.aget_state(config)
            self.assertEqual(recovered.values["application_planning_recovery_boundary"]["operation"], "repair")
            self.assertEqual(recovered.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
            self.assertEqual(recovered.values["technical_plan_repair_source_run_id"], "")
            self.assertEqual(load_application_lifecycle(harness.workspace).active_run_id, "technical-plan-review-retry")
            self.assertTrue(any(task.interrupts for task in recovered.tasks))
            self.assertEqual(recovered.tasks[0].interrupts[0].value["clarification"]["mode"], "technical_plan_confirmation")
