"""验证 Runtime 技术规划断点续修、当前 Agent 合同与独立调试边界。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from app.agents.main.technical_plan_editor import (
    TECHNICAL_PLAN_CANDIDATE_PATH, TechnicalPlanEditorError, edit_technical_plan_with_agent,
)
from app.agents.main.planner import _technical_planning_prompt
from app.config import Settings
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary, application_planning_boundary_payload,
)
from app.graph.nodes import application_technical_planning, planning
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services import agent_runtime_launcher, agent_runtime_workbench
from app.services.runtime_agent_contracts import agent_contract_index
from app.services.runtime_agent_settings import validate_technical_plan_agent_contracts
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, candidate_plan, tool_response
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


def agent_contract() -> dict:
    """提供满足当前七模块合同的业务 Agent，不引入模板占位注册。"""

    return {
        "agentId": "orders", "name": "订单助手", "purpose": "解答订单问题", "pageIds": ["orders_page"],
        "agentSettings": {"prompt": {"systemPrompt": "仅回答订单问题。"}, "model": {"source": "project"},
            "memory": {}, "context": {}, "tools": {"enabled": False, "bindings": []},
            "skills": {"enabled": False}, "knowledge": {"enabled": False}},
    }


class TechnicalPlanRuntimeRecoveryTests(TestCase):
    """通过真实文件工具和生产协调节点覆盖失败后的进展与诊断保存。"""

    def setUp(self):
        """准备有明确产品入口的候选，不启动模型、数据库或业务应用。"""

        self.baseline = candidate_plan()
        self.baseline["pages"] = [{"pageId": "orders_page"}]
        self.contract = agent_contract()
        product_agent = {key: self.contract[key] for key in ("agentId", "name", "purpose")}
        product_agent["pageActionBindings"] = [{"pageId": "orders_page", "surface": {"enabled": True}}]
        self.requirement = {"confirmed_product_plan": {"pages": self.baseline["pages"], "agents": [product_agent]},
                            "planning_adjustment_request": "完善订单助手并保留分页调整。"}
        self.settings = Settings(model_name="test", model_api_key="unused", model_base_url="http://unused")

    def edit(self, responses, *, baseline=None):
        """用受控模型驱动真实 DeepAgents 工具，直接观察候选及中断异常。"""

        model = ScriptedEditorModel(responses=responses)
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
            return edit_technical_plan_with_agent(self.requirement, baseline or self.baseline, settings=self.settings)

    def test_missing_agent_contract_can_be_added_and_pass_product_validation(self):
        """首版遗漏第五段时，局部补齐仍接受严格七模块和已确认产品全集校验。"""

        candidate = self.edit([
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                old_string='"pages": [',
                new_string='"agent_contracts": ' + json.dumps([self.contract], ensure_ascii=False) + ', "pages": ['),
            AIMessage(content="已补齐订单助手。"),
        ])
        self.assertEqual(agent_contract_index(candidate, require_runtime=False)["orders"], self.contract)
        self.assertEqual(validate_technical_plan_agent_contracts(candidate, self.requirement["confirmed_product_plan"]), [])
        self.assertNotIn("agent_contracts", self.baseline)

    def test_disabled_product_agent_can_be_removed_from_candidate(self):
        """产品入口全部停用后允许清空技术合同，不能强制保留过期 Agent。"""

        baseline = {**self.baseline, "agent_contracts": [self.contract]}
        self.requirement["confirmed_product_plan"]["agents"][0]["pageActionBindings"][0]["surface"]["enabled"] = False
        # 内存文件按根对象缩进，直接替换唯一身份所在的完整数组片段。
        old = json.dumps({"agent_contracts": [self.contract]}, ensure_ascii=False, indent=2)[2:-2].strip()
        candidate = self.edit([
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                old_string=old, new_string='"agent_contracts": []'),
            AIMessage(content="已移除停用入口对应的技术合同。"),
        ], baseline=baseline)
        self.assertEqual(validate_technical_plan_agent_contracts(candidate, self.requirement["confirmed_product_plan"]), [])

    def test_invalid_resource_configuration_can_be_repaired_locally(self):
        """资源旧形状被拒绝后，仍能在第五段修复成当前精确文件合同。"""

        baseline = deepcopy(self.baseline)
        contract = deepcopy(self.contract)
        contract["agentSettings"]["knowledge"] = {"enabled": True, "retrieval": "remote"}
        baseline["agent_contracts"] = [contract]
        self.assertTrue(validate_technical_plan_agent_contracts(baseline, self.requirement["confirmed_product_plan"]))
        candidate = self.edit([
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                old_string='"retrieval": "remote"',
                new_string='"files": ["agent-runtime/src/app/knowledge/orders.md"]'),
            AIMessage(content="已修复知识文件声明。"),
        ], baseline=baseline)
        self.assertEqual(validate_technical_plan_agent_contracts(candidate, self.requirement["confirmed_product_plan"]), [])

    def test_generation_prompt_contains_authoritative_agent_identity(self):
        """模型输入必须实际携带产品 Agent 身份与入口，不能只要求保留未提供的上下文。"""

        prompt = _technical_planning_prompt(self.requirement, None)
        serialized = json.dumps({"agents": self.requirement["confirmed_product_plan"]["agents"]}, ensure_ascii=False)
        self.assertIn(serialized, prompt)

    def test_repair_can_return_unchanged_snapshot_only_for_full_revalidation(self):
        """中断前已完成修复时允许无额外改动地复核，普通修订仍拒绝口头冒充修改。"""

        model = ScriptedEditorModel(responses=[AIMessage(content="候选无需继续修改，请平台复验。")])
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
            candidate = edit_technical_plan_with_agent(self.requirement, self.baseline,
                settings=self.settings, allow_unchanged=True)
        self.assertNotIn("artifact_type", candidate)
        self.assertEqual(candidate["api_contracts"], self.baseline["api_contracts"])

    def test_budget_exhaustion_preserves_last_complete_edit_before_invalid_json(self):
        """超限前发生不完整关联编辑时，仍恢复此前成功修改，且不带已物化身份。"""

        responses = [
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                old_string='"default": 20', new_string='"default": 8'),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                old_string='"default": 30', new_string='"default": PENDING'),
            *[tool_response("grep", pattern="orders") for _ in range(8)],
        ]
        with self.assertRaisesRegex(TechnicalPlanEditorError, "达到调用上限") as raised:
            self.edit(responses)
        recovered = raised.exception.candidate
        values = [item["parameters"][0]["schema"]["default"] for item in recovered["api_contracts"][0]["endpoints"]]
        self.assertEqual(values, [8, 30])
        self.assertNotIn("artifact_type", recovered)
        self.assertEqual(self.baseline["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 20)

    def test_generation_preserves_original_diagnostic_and_partial_candidate(self):
        """一次工具超限之后，下一轮仍收到原 Schema 错误并基于已修改候选修复。"""

        partial = deepcopy(self.baseline)
        partial.pop("artifact_type")
        partial["architecture"]["frontend"] = "保留本轮分页调整。"
        with (
            patch.object(planning, "plan_project_with_chat_model", return_value=deepcopy(self.baseline)),
            patch.object(planning, "_repair_technical_plan_candidate", side_effect=[
                TechnicalPlanEditorError("达到调用上限", partial), deepcopy(self.baseline),
            ]) as repair,
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", side_effect=[["原始 Schema 错误"], []]),
        ):
            result, errors, _ = planning._generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(result)
        self.assertEqual(errors, [])
        self.assertEqual(repair.call_args.args[1], partial)
        self.assertIn("原始 Schema 错误", repair.call_args.args[2])
        self.assertIn("达到调用上限", repair.call_args.args[2])

    def test_exhausted_budget_reports_validation_and_editor_errors(self):
        """整体预算耗尽后同时返回业务诊断和工具中断原因，供原审阅门展示。"""

        with (
            patch.object(planning, "plan_project_with_chat_model", return_value=deepcopy(self.baseline)),
            patch.object(planning, "_repair_technical_plan_candidate", side_effect=TechnicalPlanEditorError("调用上限", self.baseline)),
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", return_value=["Agent 合同不完整"]),
        ):
            result, errors, failed = planning._generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNone(result)
        self.assertEqual(errors, ["Agent 合同不完整", "调用上限"])
        self.assertIsNotNone(failed)

    def test_transaction_repair_consumes_failed_candidate_and_errors(self):
        """同一事务重入使用失败候选；新建事务不能误用残留的失败输入。"""

        partial = deepcopy(self.baseline)
        partial["architecture"]["frontend"] = "已完成的局部调整。"
        for operation in (ApplicationPlanningOperation.REPAIR, ApplicationPlanningOperation.INITIAL):
            with self.subTest(operation=operation):
                state = {"requirement_spec": {}, "technical_plan": self.baseline,
                    "technical_plan_repair_candidate": partial, "technical_plan_repair_errors": ["具体合同错误"],
                    "application_planning_recovery_boundary": application_planning_boundary_payload(
                        operation_id="recovery", operation=operation,
                        boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY, request="继续规划"),
                }
                with (
                    patch.object(planning, "_technical_planning_requirement_spec", return_value=self.requirement),
                    patch.object(planning, "_generate_valid_technical_plan", return_value=(deepcopy(self.baseline), [], None)) as generate,
                ):
                    result = application_technical_planning.technical_planning_generate(state)
                self.assertEqual(generate.call_args.args[2], partial if operation is ApplicationPlanningOperation.REPAIR else self.baseline)
                self.assertEqual(generate.call_args.kwargs.get("initial_errors"), ["具体合同错误"] if operation is ApplicationPlanningOperation.REPAIR else None)
                self.assertEqual(result["technical_plan_repair_candidate"], {})
                self.assertNotIn("topology", result["technical_plan_candidate"])


class TechnicalPlanRecoveryStreamTests(IsolatedAsyncioTestCase):
    """用真实 AG-UI 和 Graph 证明保存的失败候选可以续修到正式审阅门。"""

    async def test_failed_candidate_reaches_review_without_restarting_generation(self):
        """模拟截图后的续修，保留局部进展并通过全量校验，仍等待用户选架构及确认。"""

        h = TechnicalPlanningRecoveryJourneyHarness()
        try:
            await h.initialize_graph()
            partial = {key: deepcopy(h.candidate[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
            partial["architecture"]["frontend"] += "保留失败候选中的调整。"
            endpoint = partial["api_contracts"][0]["endpoints"][0]
            original_ref = endpoint["response_schema_ref"]
            endpoint["response_schema_ref"] = "RecoveryMissingOutput"
            snapshot = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
            await h.graph.aupdate_state(snapshot.config, {
                "technical_plan": h.candidate,
                "technical_plan_repair_candidate": partial,
                "technical_plan_repair_errors": ["响应 Schema RecoveryMissingOutput 不存在。"],
                "application_planning_recovery_boundary": application_planning_boundary_payload(
                    operation_id="resume-failed-candidate", operation=ApplicationPlanningOperation.REPAIR,
                    boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
                    request=h.request, baseline=h.candidate),
            }, as_node="workflow_entry")
            model = ScriptedEditorModel(responses=[
                tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                    old_string='"response_schema_ref": "RecoveryMissingOutput"',
                    new_string=f'"response_schema_ref": "{original_ref}"'),
                AIMessage(content="已修复候选的 Schema 引用。"),
            ])
            with (
                patch("app.config.Settings.from_env", side_effect=h.current_settings),
                patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model),
                patch("app.agents.main.planner._invoke_live_chat_model", side_effect=AssertionError("不能从零重生成")),
            ):
                frames = [frame async for frame in build_workflow_ag_ui_stream(graph=h.graph, payload={
                    "threadId": h.thread_id, "runId": h.source_run_id, "request": h.request,
                    "resume_from": "technical_planning_begin", "forwardedProps": {
                        "workspaceRoot": str(h.workspace), "workflowScope": "application_planning"}})]
            state = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
            self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
            self.assertTrue(any(task.interrupts for task in state.tasks))
            self.assertIn("保留失败候选中的调整", state.values["technical_plan"]["architecture"]["frontend"])
            self.assertEqual(state.values["clarification"]["mode"], "technical_plan_topology_selection")
            self.assertEqual(state.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        finally:
            await close_workflow_checkpointer_for_workspace(workspace=str(h.workspace), project_id=h.project_id)
            h.close()


class IndependentRuntimeDebugTests(TestCase):
    """独立调试要求选定 Agent 就绪，同时拒绝正式合同之外的注册。"""

    def test_debug_target_and_snapshot_keep_partial_registry_policy(self):
        """启动与后续状态探测使用同一目标，不能重新扩大为全部正式 Agent。"""

        with tempfile.TemporaryDirectory() as directory:
            root = str(Path(directory).resolve())
            with (
                patch.object(agent_runtime_workbench, "runtime_agents", return_value={"agents": [{"agentId": "orders"}, {"agentId": "other"}]}),
                patch.object(agent_runtime_workbench, "_launch_runtime_locked", return_value={
                    "status": "running", "launch_id": "debug-target", "preview_url": "http://127.0.0.1:1234",
                    "database_required": False, "agent_ids": ["orders", "other"],
                }) as launch,
                patch.object(agent_runtime_workbench, "runtime_health", return_value=(True, "就绪")) as health,
            ):
                self.addCleanup(agent_runtime_workbench._sessions.pop, root, None)
                state, _ = agent_runtime_workbench.start_debug(root, "thread", "null", "orders")
                self.assertTrue(state["active"])
                self.assertEqual(launch.call_args.kwargs["target_agent_id"], "orders")
                self.assertTrue(agent_runtime_workbench.debug_snapshot(root)["active"])
                self.assertEqual(health.call_args.kwargs["expected_agent_ids"], ["orders"])
                self.assertEqual(health.call_args.kwargs["allowed_agent_ids"], ["orders", "other"])

    def test_launcher_health_scopes_debug_but_requires_all_agents_for_preview(self):
        """真实启动协调器仅对定向调试放宽未实现 Agent，完整预览继续要求全部注册。"""

        contract = agent_contract()
        other = {**contract, "agentId": "other"}
        plan = {"pages": [{"pageId": "orders_page"}], "agent_contracts": [contract, other], "api_contracts": [],
            "topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
                         "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(agent_runtime_launcher, "validate_runtime_launch"),
                patch.object(agent_runtime_launcher, "runtime_source_fingerprint", return_value="same"),
                patch.object(agent_runtime_launcher, "stop_runtime", return_value={"status": "stopped"}),
                patch.object(agent_runtime_launcher, "resolve_runtime_uv", return_value="uv"),
                patch.object(agent_runtime_launcher, "prepare_managed_startup"),
                patch.object(agent_runtime_launcher, "build_runtime_environment", return_value={"AGENT_RUNTIME_DATABASE_ENABLED": "false"}),
                patch.object(agent_runtime_launcher, "runtime_check_plan", return_value=plan),
                patch.object(agent_runtime_launcher.workspace_process_registry, "start", return_value=Mock(pid=1234)),
                patch.object(agent_runtime_launcher, "register_runtime"),
                patch.object(agent_runtime_launcher, "attach_redacted_logs", return_value=[]),
                patch.object(agent_runtime_launcher, "wait_runtime_ready", return_value=(True, "就绪", False)) as ready,
            ):
                for target in ("orders", None):
                    result = agent_runtime_launcher._launch_runtime_locked(root, on_progress=None, settings=None,
                        skip_install=True, target_agent_id=target,
                        debug={"token": "test", "expiresAt": 1, "origins": "[]"} if target else None)
                    self.assertEqual(result["status"], "running", result.get("message"))
                    self.assertEqual(ready.call_args.kwargs["expected_agent_ids"], ["orders"] if target else ["orders", "other"])
                    self.assertEqual(ready.call_args.kwargs["allowed_agent_ids"], ["orders", "other"] if target else None)
