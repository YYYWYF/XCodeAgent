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
from app.graph.nodes.application_technical_planning import (
    technical_planning_begin, technical_planning_generate,
)
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import load_application_lifecycle
from app.services.execution_recovery_executor import _apply_application_planning_transaction_authority
from app.services.technical_plan_carryover import (
    drop_generated_fields,
    write_technical_plan_carryover,
)
from tests.test_technical_plan_content_changes import _changeset
from app.services.technical_plan_changeset import revision_document
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
        # 生产冷进入（checkpoint derive）不携带上游 state；这里同样只放本事务自己的计划，
        # 否则会被当成"新迭代残留的旧分支计划"而按首轮创建处理。
        return {
            "workspace": str(harness.workspace),
            "active_thread_id": harness.thread_id,
            "active_run_id": harness.source_run_id,
            "request": harness.request,
            "technical_plan": deepcopy(baseline) if isinstance(baseline, dict) and baseline else baseline,
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
                        result = await call
                        if entry == "node":
                            # 基线是"本轮计划"的业务视图：确认状态等派生字段不进对比基线。
                            self.assertEqual(
                                result["technical_plan_baseline"],
                                drop_generated_fields(baseline or {}),
                            )
                            if isinstance(baseline, dict) and baseline:
                                self.assertIsNot(result["technical_plan_baseline"], baseline)
                    else:
                        with self.assertRaisesRegex(error, "baseline"):
                            await call

    async def test_second_revision_uses_current_plan_as_review_baseline(self) -> None:
        """二次修订与上一版草稿比较，首次新增内容不得在本轮继续显示新增。"""

        current = deepcopy(self.harness.candidate)
        carried = {
            "artifact_type": "technical-plan",
            "architecture": {},
            "entities": [],
            "api_contracts": [],
            "pages": [{"pageId": "home", "references": {}}],
        }
        write_technical_plan_carryover(
            self.harness.workspace, source_branch="v1_5", plan=carried,
        )
        state = self._state(
            ApplicationPlanningOperation.REVISE, current, current,
        )

        result = await technical_planning_begin(state)

        self.assertEqual(result["technical_plan_baseline"], drop_generated_fields(current))
        self.assertEqual(result["technical_plan_baseline_origin"], "resumed")
        self.assertNotEqual(result["technical_plan_baseline"], carried)
        self.assertNotIn("technical_plan", result, "续跑的当前计划不应被当成冷进入旧数据清空")

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
            self.assertNotEqual(child.run_id, source.run_id, str(harness.last_recovery_frames))
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
    async def test_merge_failure_stops_before_document_confirmation(self) -> None:
        """合并后的完整校验失败时，真实 Graph 只挂错误门，不写技术草稿。"""

        harness = self.harness
        from app.workspace.plan_documents import technical_plan_draft_json_path
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            with (
                patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(harness.candidate), [], None)),
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=["合并后 Schema 引用不存在"]),
            ):
                await harness.run_initial()
            snapshot = await harness.graph.aget_state({"configurable": {"thread_id": harness.thread_id}})
            self.assertEqual(tuple(snapshot.next), ("technical_planning_review",))
            pending = snapshot.tasks[0].interrupts[0].value
            self.assertEqual(pending["clarification"]["mode"], "technical_plan_generation_error")
            self.assertEqual(snapshot.values["technical_plan_candidate"], {})
            self.assertFalse(technical_plan_draft_json_path({"workspace": str(harness.workspace)}).exists())

    async def test_valid_card_revision_updates_draft_and_document_artifact_markers(self) -> None:
        """待确认卡修改经真实 Graph、AG-UI 和文档引用传递新内容及修改、删除、未变更标识。"""

        harness = self.harness
        from app.workspace.plan_documents import technical_plan_draft_json_path
        from app.workspace.content_artifacts import read_artifact
        seed = deepcopy(harness.candidate)
        seed_contract = seed["api_contracts"][0]
        first_endpoint = seed_contract["endpoints"][0]
        seed_contract["endpoints"].extend([
            {**deepcopy(first_endpoint), "id": first_endpoint["id"] + ".removed", "path": first_endpoint["path"] + "/removed"},
            {**deepcopy(first_endpoint), "id": first_endpoint["id"] + ".kept", "path": first_endpoint["path"] + "/kept"},
        ])
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            with patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(seed, [], None)):
                await harness.run_initial()
            config = {"configurable": {"thread_id": harness.thread_id}}
            before = await harness.graph.aget_state(config)
            baseline = drop_generated_fields(before.values["technical_plan"])
            contract = baseline["api_contracts"][0]
            change, removed, kept = contract["endpoints"][:3]
            request = (
                f"{change['method']} {change['path']} 接口修改说明；"
                f"删除 {removed['method']} {removed['path']} 接口"
            )
            candidate = deepcopy(baseline)
            proposed = candidate["api_contracts"][0]
            proposed["endpoints"][0]["summary"] = "用户本次修改的接口说明"
            proposed["endpoints"] = [item for item in proposed["endpoints"] if item["id"] != removed["id"]]
            proposed["endpoints"][1]["summary"] = "模型顺手改写但必须丢弃"
            pending = before.tasks[0].interrupts[0].value
            changeset = _changeset(baseline, request, [
                {"op": "replace", "path": f"/api_contracts/{contract['id']}/endpoints/{change['id']}/summary",
                 "expectedBefore": change["summary"], "value": "用户本次修改的接口说明"},
                {"op": "remove", "path": f"/api_contracts/{contract['id']}/endpoints/{removed['id']}",
                 "expectedBefore": removed},
            ])
            with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset) as model:
                frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=harness.graph,
                    payload={
                        "threadId": harness.thread_id, "runId": "technical-plan-valid-card-revision",
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
            model.assert_called_once()
            after = await harness.graph.aget_state(config)
            self.assertEqual(after.tasks[0].interrupts[0].value["clarification"]["mode"], "technical_plan_confirmation")
            self.assertEqual(after.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
            endpoints = {item["id"]: item for item in after.values["technical_plan"]["api_contracts"][0]["endpoints"]}
            self.assertEqual(endpoints[change["id"]]["summary"], "用户本次修改的接口说明")
            self.assertEqual(endpoints[change["id"]]["_change_status"], "modified")
            self.assertEqual(endpoints[removed["id"]]["_change_status"], "deleted")
            self.assertEqual(endpoints[kept["id"]]["_change_status"], "unchanged")
            self.assertEqual(endpoints[kept["id"]]["summary"], kept["summary"])
            path = technical_plan_draft_json_path({"workspace": str(harness.workspace)})
            persisted = json.loads(path.read_text())
            self.assertIn("用户本次修改的接口说明", path.read_text())
            self.assertNotIn("模型顺手改写", path.read_text())
            self.assertEqual(persisted["confirmation_status"], "pending_user_confirmation")
            # 右侧实际读取技术规划及审阅基线的不可变引用，而不是内部 candidate/diff 字段。
            references = []
            for frame in frames:
                for line in frame.splitlines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    view = event.get("snapshot") or (event.get("value") or {}).get("state") or {}
                    ref = (view.get("artifactRefs") or {}).get("technical_plan")
                    if ref:
                        references.append(ref)
            self.assertTrue(references)
            doc = read_artifact(str(harness.workspace), references[-1])
            self.assertIn("用户本次修改的接口说明", str(doc))
            self.assertIn("modified", str(doc))
            self.assertIn("deleted", str(doc))

    async def test_url_revision_by_name_updates_draft_and_document_marker(self) -> None:
        """仅写接口名称和 URL 新值时，真实 Graph 采纳新路径并展示修改标识。"""

        harness = self.harness
        from app.workspace.plan_documents import technical_plan_draft_json_path
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            with patch(
                "app.graph.nodes.planning._generate_valid_technical_plan",
                return_value=(deepcopy(harness.candidate), [], None),
            ):
                await harness.run_initial()
            config = {"configurable": {"thread_id": harness.thread_id}}
            before = await harness.graph.aget_state(config)
            baseline = drop_generated_fields(before.values["technical_plan"])
            contract = baseline["api_contracts"][0]
            endpoint = contract["endpoints"][0]
            old_path = endpoint["path"]
            new_path = old_path.rstrip("/") + "/list"
            request = f"{endpoint['name']}接口url改成{new_path}"
            candidate = deepcopy(baseline)
            candidate["api_contracts"][0]["endpoints"][0]["path"] = new_path
            pending = before.tasks[0].interrupts[0].value

            changeset = _changeset(baseline, request, [{
                "op": "replace", "path": f"/api_contracts/{contract['id']}/endpoints/{endpoint['id']}/path",
                "expectedBefore": old_path, "value": new_path,
            }])
            with patch(
                "app.agents.main.technical_plan_revision.plan_technical_plan_changeset",
                return_value=changeset,
            ) as model:
                frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=harness.graph,
                    payload={
                        "threadId": harness.thread_id, "runId": "technical-plan-url-revision",
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

            self.assertTrue(frames)
            model.assert_called_once()
            self.assertEqual(model.call_args.args[2], request)
            self.assertEqual(model.call_args.args[1], revision_document(baseline))
            after = await harness.graph.aget_state(config)
            self.assertEqual(after.tasks[0].interrupts[0].value["clarification"]["mode"], "technical_plan_confirmation")
            self.assertEqual(after.values["technical_plan_baseline"], baseline)
            revised = after.values["technical_plan"]["api_contracts"][0]["endpoints"][0]
            self.assertEqual(revised["path"], new_path)
            self.assertEqual(revised["_change_status"], "modified")
            draft = technical_plan_draft_json_path({"workspace": str(harness.workspace)})
            persisted = json.loads(draft.read_text())
            self.assertEqual(persisted["api_contracts"][0]["endpoints"][0]["path"], new_path)

    async def test_unmatched_revision_preserves_draft_and_emits_error_review(self) -> None:
        """待确认卡的错误路径经真实 AG-UI 挂错误门，零模型调用且不覆盖当前草稿。"""

        harness = self.harness
        from app.workspace.plan_documents import technical_plan_draft_json_path
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            with (
                patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(harness.candidate), [], None)),
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                await harness.run_initial()
            config = {"configurable": {"thread_id": harness.thread_id}}
            before = await harness.graph.aget_state(config)
            pending = before.tasks[0].interrupts[0].value
            path = technical_plan_draft_json_path({"workspace": str(harness.workspace)})
            prior_bytes = path.read_bytes()
            request = "删除 /api/canteen-evaluations/canteens 接口"
            changeset = _changeset(before.values["technical_plan"], request, [{
                "op": "remove", "path": "/api_contracts/missing/endpoints/missing", "expectedBefore": None,
            }])
            with (
                patch("app.graph.nodes.planning._generate_valid_technical_plan") as model,
                patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset),
            ):
                frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=harness.graph,
                    payload={
                        "threadId": harness.thread_id, "runId": "technical-plan-unmatched-revision",
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
            model.assert_not_called()
            after = await harness.graph.aget_state(config)
            self.assertEqual(tuple(after.next), ("technical_planning_review",))
            self.assertEqual(after.values["technical_plan_candidate"], {})
            self.assertEqual(after.tasks[0].interrupts[0].value["clarification"]["mode"], "technical_plan_generation_error")
            self.assertIn("不存在", " ".join(after.values["technical_plan_repair_errors"]))
            self.assertEqual(path.read_bytes(), prior_bytes)
            self.assertEqual(
                drop_generated_fields(after.values["technical_plan"]),
                drop_generated_fields(before.values["technical_plan"]),
            )
            stream = "".join(frames)
            self.assertIn("不存在", stream)
            self.assertIn('"type":"RUN_FINISHED"', stream)
            self.assertNotIn('"type":"RUN_ERROR"', stream)

    def test_generation_scope_uses_transaction_baseline_and_survives_repair(self) -> None:
        """当前草稿的精确接口可修改，前次已改内容在本轮差异中显示未变更。"""

        from tests.test_technical_plan_content_changes import _base_plan, _endpoint, _OTHER_ENDPOINT_ID
        original = _base_plan()
        current = deepcopy(original)
        _endpoint(current, _OTHER_ENDPOINT_ID)["summary"] = "前次修订"
        candidate = deepcopy(current)
        _endpoint(candidate)["summary"] = "本次修订"
        _endpoint(candidate, _OTHER_ENDPOINT_ID)["summary"] = "未授权改写"
        for operation in (ApplicationPlanningOperation.REVISE, ApplicationPlanningOperation.REPAIR):
            with self.subTest(operation=operation):
                request = "GET /api/greetings/morning 接口改说明"
                state = {
                    "request": request, "technical_plan": current,
                    "requirement_spec": self.harness.requirement_spec,
                    "application_planning_recovery_boundary": application_planning_boundary_payload(
                        operation_id="direct-revision-test", operation=operation,
                        boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
                        request=request, baseline=current,
                    ),
                }
                changeset = _changeset(current, request, [{
                    "op": "replace", "path": f"/api_contracts/greeting_contract/endpoints/{_endpoint(current)['id']}/summary",
                    "expectedBefore": "基线说明", "value": "本次修订",
                }])
                with (
                    patch("app.graph.nodes.application_technical_planning._technical_plan_review_baseline", return_value=original),
                    patch("app.graph.nodes.planning._technical_planning_requirement_spec", return_value={
                        "application_config": {"auth": {"enable": False}, "authorization": {"enabled": False}},
                        "confirmed_product_plan": {},
                    }),
                    patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset) as model,
                    patch("app.graph.nodes.planning._attach_technical_plan_contracts", side_effect=lambda _state, plan: plan),
                    patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
                ):
                    result = technical_planning_generate(state)
                model.assert_called_once()
                self.assertEqual(_endpoint(result["technical_plan_candidate"])["summary"], "本次修订")
                self.assertEqual(_endpoint(result["technical_plan_candidate"], _OTHER_ENDPOINT_ID)["summary"], "前次修订")
                self.assertEqual(_endpoint(result["technical_plan_diff"])["_change_status"], "modified")
                self.assertEqual(_endpoint(result["technical_plan_diff"], _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")
                self.assertEqual(result["application_planning_recovery_boundary"]["boundary"], "candidate_committed")

    def test_new_iteration_with_carryover_uses_changeset_instead_of_full_regeneration(self) -> None:
        """新迭代只提交新增页面操作，旧接口说明和约束不得被整份生成器重写。"""
        from tests.test_technical_plan_content_changes import _base_plan, _endpoint

        baseline = _base_plan()
        request = "新增独立页面，调用接口并展示字符串“晚上好！”"
        new_page = {"pageId": "evening_greeting", "references": {"endpoint_dependencies": [], "action_implementations": []}}
        changeset = _changeset(baseline, request, [{"op": "add", "path": "/pages/evening_greeting", "value": new_page}])
        state = {
            "request": request,
            "technical_plan": {},
            "requirement_spec": self.harness.requirement_spec,
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="new-iteration-changeset-test", operation=ApplicationPlanningOperation.INITIAL,
                boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
                request=request, baseline=baseline,
            ),
        }
        with (
            patch("app.graph.nodes.application_technical_planning.read_technical_plan_carryover", return_value=baseline),
            patch("app.graph.nodes.application_technical_planning._technical_plan_review_baseline", return_value=baseline),
            patch("app.graph.nodes.planning._technical_planning_requirement_spec", return_value={
                "application_config": {"auth": {"enable": False}, "authorization": {"enabled": False}},
                "confirmed_product_plan": {},
            }),
            patch("app.graph.nodes.planning._generate_valid_technical_plan", side_effect=AssertionError("新迭代不能整份重生成")) as old_generator,
            patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset) as planner,
            patch("app.graph.nodes.planning._attach_technical_plan_contracts", side_effect=lambda _state, plan: plan),
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
        ):
            result = technical_planning_generate(state)

        old_generator.assert_not_called()
        planner.assert_called_once()
        self.assertEqual(planner.call_args.args[1], revision_document(baseline))
        self.assertEqual(planner.call_args.args[2], request)
        candidate = result["technical_plan_candidate"]
        self.assertEqual(_endpoint(candidate)["summary"], "基线说明")
        self.assertEqual(_endpoint(candidate)["error_codes"], ["INTERNAL_SERVER_ERROR"])
        self.assertEqual(candidate["entities"], baseline["entities"])
        self.assertEqual(candidate["pages"][0], baseline["pages"][0])
        self.assertEqual(candidate["pages"][1], new_page)
        self.assertEqual(result["technical_plan_diff"]["pages"][0]["_change_status"], "unchanged")
        self.assertEqual(result["technical_plan_diff"]["pages"][1]["_change_status"], "added")

    def test_materialize_revision_preserves_deleted_page_before_contract_validation(self) -> None:
        """删除页在内部校验前就有墓碑，不能按缺失业务 action 实现拒绝删除。"""
        from app.agents.main.technical_plan_revision import _materialize_revision
        from app.services.page_implementation_contract import validate_page_implementation_contracts
        from tests.test_technical_plan_content_changes import _base_plan, _endpoint

        baseline = _base_plan()
        endpoint_id = _endpoint(baseline)["id"]
        baseline["pages"][0]["references"]["action_implementations"] = [
            {"actionId": "load_greeting", "endpointId": endpoint_id}
        ]
        document = {
            **baseline,
            "pages": [],
            "api_contracts": [],
        }
        product = {
            "pages": [{
                "pageId": "home", "name": "问候页", "_change_status": "deleted",
                "actions": [{"actionId": "load_greeting", "behavior": {"type": "business"}}],
            }]
        }
        requirement = {
            "application_config": {"auth": {"enable": False}, "authorization": {"enabled": False}},
            "confirmed_product_plan": product,
        }
        with patch("app.graph.nodes.planning._attach_technical_plan_contracts", side_effect=lambda _state, plan: plan):
            candidate = _materialize_revision({}, requirement, baseline, document)

        self.assertEqual(candidate["pages"][0]["_change_status"], "deleted")
        self.assertEqual(validate_page_implementation_contracts(candidate, product, {}), [])
        from app.services.technical_plan_changeset import revision_document
        cleaned = {**candidate, **revision_document(candidate)}
        self.assertEqual(validate_page_implementation_contracts(cleaned, product, {}), [])

    def test_delete_page_and_unused_contracts_passes_full_revision_validation(self) -> None:
        """完整修订生成删除页面及无用契约后，不再要求实现已删除业务 action。"""
        from app.agents.main.technical_plan_revision import generate_technical_plan_revision
        from app.services.application_config import read_application_config
        from app.services.technical_plan_changeset import revision_document
        from tests.test_technical_plan_content_changes import _changeset

        baseline = deepcopy(self.harness.candidate)
        product = deepcopy(self.harness.product_plan)
        deleted_page = product["pages"][-1]
        deleted_page["_change_status"] = "deleted"
        page_id = deleted_page["pageId"]
        document = revision_document(baseline)
        page = next(page for page in document["pages"] if page["pageId"] == page_id)
        remaining_pages = [page for page in document["pages"] if page["pageId"] != page_id]
        remaining_endpoints = {
            str(item.get("endpoint_id") or item.get("endpointId") or "")
            for page in remaining_pages
            for key in ("endpoint_dependencies", "action_implementations")
            for item in (page.get("references") or {}).get(key, [])
        }
        operations = [{"op": "remove", "path": f"/pages/{page_id}", "expectedBefore": page}]
        for contract in document["api_contracts"]:
            used = [
                endpoint for endpoint in contract.get("endpoints", [])
                if endpoint.get("id") in remaining_endpoints
            ]
            if not used:
                operations.append({
                    "op": "remove", "path": f"/api_contracts/{contract['id']}", "expectedBefore": contract,
                })
            elif len(used) != len(contract.get("endpoints") or []):
                operations.append({
                    "op": "replace", "path": f"/api_contracts/{contract['id']}/endpoints",
                    "expectedBefore": contract["endpoints"], "value": used,
                })
        request = f"删除页面 {page_id} 并清理无用接口"
        changeset = _changeset(document, request, operations)
        requirement = {
            **self.harness.requirement_spec,
            "pages": product["pages"],
            "confirmed_product_plan": product,
            "application_config": read_application_config(self.harness.workspace),
        }
        state = {
            "workspace": str(self.harness.workspace), "workflow_scope": "application_planning",
            "requirement_spec": self.harness.requirement_spec,
            "product_plan": product, "ui_designs": self.harness.ui_designs,
        }
        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset):
            candidate, errors = generate_technical_plan_revision(state, requirement, baseline, request)

        self.assertIsNotNone(candidate, errors)
        self.assertTrue(any(
            page.get("pageId") == page_id and page.get("_change_status") == "deleted"
            for page in candidate["pages"]
        ))

    def test_named_schema_revision_survives_generation_node_and_marks_document_endpoint(self) -> None:
        """直接点名类型字段的原有修改经过生产生成节点后生效且关联接口显示修改。"""

        from tests.test_technical_plan_content_changes import _base_plan, _contract, _endpoint, _OTHER_ENDPOINT_ID
        baseline = _base_plan()
        candidate = deepcopy(baseline)
        properties = _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"]
        properties["greetingText"] = properties.pop("morningGreetingText")
        request = "把 MorningGreetingResponse 的 morningGreetingText 改成 greetingText"
        state = {
            "request": request, "technical_plan": baseline,
            "requirement_spec": self.harness.requirement_spec,
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="schema-revision-test", operation=ApplicationPlanningOperation.REVISE,
                boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
                request=request, baseline=baseline,
            ),
        }
        source = "/api_contracts/greeting_contract/schemas/MorningGreetingResponse/properties/morningGreetingText"
        changeset = _changeset(baseline, request, [{
            "op": "move", "from": source, "path": source.rsplit("/", 1)[0] + "/greetingText",
            "expectedBefore": _contract(baseline)["schemas"]["MorningGreetingResponse"]["properties"]["morningGreetingText"],
        }])
        with (
            patch("app.graph.nodes.application_technical_planning._technical_plan_review_baseline", return_value=baseline),
            patch("app.graph.nodes.planning._technical_planning_requirement_spec", return_value={
                "application_config": {"auth": {"enable": False}, "authorization": {"enabled": False}},
                "confirmed_product_plan": {},
            }),
            patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset),
            patch("app.graph.nodes.planning._attach_technical_plan_contracts", side_effect=lambda _state, plan: plan),
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
        ):
            result = technical_planning_generate(state)
        self.assertIn("greetingText", _contract(result["technical_plan_candidate"])["schemas"]["MorningGreetingResponse"]["properties"])
        self.assertEqual(_endpoint(result["technical_plan_diff"])["_change_status"], "modified")
        self.assertEqual(_endpoint(result["technical_plan_diff"], _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")

    def test_invalid_changeset_stops_without_full_candidate_generation(self) -> None:
        """稳定身份歧义或缺失目标原子失败，旧候选生成器不会被当作兜底调用。"""

        from tests.test_technical_plan_content_changes import _base_plan, _contract, _endpoint
        for duplicate, expected in ((True, "多个"), (False, "不存在")):
            base = _base_plan()
            if duplicate:
                _contract(base)["endpoints"].append(deepcopy(_endpoint(base)))
            request = "删除指定接口"
            endpoint_id = _endpoint(base)["id"] if duplicate else "missing"
            changeset = _changeset(base, request, [{
                "op": "remove", "path": f"/api_contracts/greeting_contract/endpoints/{endpoint_id}",
                "expectedBefore": _endpoint(base),
            }])
            state = {
                "request": request, "technical_plan": base,
                "requirement_spec": self.harness.requirement_spec,
                "application_planning_recovery_boundary": application_planning_boundary_payload(
                    operation_id="direct-match-test", operation=ApplicationPlanningOperation.REVISE,
                    boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
                    request=request, baseline=base,
                ),
            }
            with (
                patch("app.graph.nodes.application_technical_planning._technical_plan_review_baseline", return_value=base),
                patch("app.graph.nodes.planning._technical_planning_requirement_spec", return_value={}),
                patch("app.graph.nodes.planning._generate_valid_technical_plan") as old_model,
                patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=changeset),
            ):
                result = technical_planning_generate(state)
            old_model.assert_not_called()
            self.assertEqual(result["technical_plan_candidate"], {})
            self.assertIn(expected, " ".join(result["clarification"]["errors"]))
            self.assertEqual(result["application_planning_recovery_boundary"]["boundary"], "review_ready")

    async def test_confirm_failure_stops_without_generating_another_confirmation(self) -> None:
        """已有草稿确认失败后，真实 AG-UI/SQLite 挂起错误门，不再调用模型或改写草稿。"""

        harness = self.harness
        from app.workspace.plan_documents import technical_plan_draft_json_path
        with patch("app.config.Settings.from_env", side_effect=harness.current_settings):
            await harness.initialize_graph()
            with (
                patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(harness.candidate), [], None)),
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            ):
                await harness.run_initial()
            config = {"configurable": {"thread_id": harness.thread_id}}
            before = await harness.graph.aget_state(config)
            pending = before.tasks[0].interrupts[0].value
            draft_path = technical_plan_draft_json_path({"workspace": str(harness.workspace)})
            draft_before = draft_path.read_bytes()
            with (
                patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=["确认时发现 Schema 引用不存在"]),
                patch("app.graph.nodes.planning._generate_valid_technical_plan") as generate,
            ):
                frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=harness.graph,
                    payload={
                        "threadId": harness.thread_id, "runId": "technical-plan-confirm-failure",
                        "message": "正确，继续",
                        "forwardedProps": {
                            "workspaceRoot": str(harness.workspace), "workflowScope": "application_planning",
                            "applicationPlanningInteraction": {
                                "gateId": pending["gateId"], "artifact": pending["artifact"],
                                "artifactRevision": pending["artifactRevision"], "action": "confirm", "request": "正确，继续",
                            },
                        },
                    },
                )]
            generate.assert_not_called()
            after = await harness.graph.aget_state(config)
            self.assertEqual(tuple(after.next), ("technical_planning_review",))
            self.assertEqual(drop_generated_fields(after.values["technical_plan"]), drop_generated_fields(before.values["technical_plan"]))
            self.assertEqual(draft_path.read_bytes(), draft_before)
            self.assertEqual(after.tasks[0].interrupts[0].value["clarification"]["mode"], "project_plan_dependency_validation_error")
            self.assertEqual(load_application_lifecycle(harness.workspace).initialization.stage.value, "awaiting_technical_plan_confirmation")
            self.assertNotIn('"type":"RUN_ERROR"', "".join(frames))


class ColdStalePlanBoundaryTests(unittest.TestCase):
    """冷进入清空旧规划时，boundary 不能继续携带旧基线摘要。

    回归：新迭代从 checkpoint 残留的上一轮 `technical_plan` 取到基线摘要，随后
    又因 `technical_plan_baseline_origin` 不是 resumed 把它清空，于是留下
    "boundary 有摘要、state 无基线"的矛盾状态，续修时必然报
    "baseline 与 boundary 不匹配"。
    """

    def _state(self, *, stale_plan: dict, origin: str) -> dict:
        """构造冷进入 begin 所需的最小状态（只覆盖基线判定用到的字段）。"""

        boundary = application_planning_boundary_payload(
            operation_id="technical-plan-repair-test",
            operation=ApplicationPlanningOperation.REPAIR,
            boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
            request="删除页面",
            baseline=stale_plan,
        )
        return {
            "workspace": "/nonexistent-workspace",
            "technical_plan": deepcopy(stale_plan),
            "technical_plan_baseline_origin": origin,
            "application_planning_recovery_boundary": boundary,
        }

    def test_boundary_drops_stale_baseline_when_plan_is_cleared(self) -> None:
        """旧规划被清空时，boundary 必须同样去掉 baselineSha256。"""

        from app.graph.nodes.application_technical_planning import _boundary_payload_for_state

        stale = {"artifact_type": "technical-plan", "pages": [{"pageId": "home"}]}
        state = self._state(stale_plan=stale, origin="")
        payload = _boundary_payload_for_state(
            state,
            boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
            drop_baseline=True,
        )

        self.assertNotIn("baselineSha256", payload)

    def test_boundary_keeps_baseline_when_resumed(self) -> None:
        """续跑场景仍必须保留基线摘要，基线一致性校验才能生效。"""

        from app.graph.nodes.application_technical_planning import _boundary_payload_for_state

        stale = {"artifact_type": "technical-plan", "pages": [{"pageId": "home"}]}
        state = self._state(stale_plan=stale, origin="resumed")
        payload = _boundary_payload_for_state(
            state,
            boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
        )

        self.assertIn("baselineSha256", payload)
