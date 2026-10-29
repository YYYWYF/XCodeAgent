"""局部技术修订与真实全量校验、AG-UI 产物提交及确认门的集成回归。"""

from copy import deepcopy
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.graph.nodes import planning
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.workspace.plan_documents import commit_technical_plan_document, technical_plan_json_path
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, tool_response
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalPlanFragmentWorkflowTests(IsolatedAsyncioTestCase):
    """证明局部工具结果仍需通过完整合同检查，并重新等待正式确认。"""

    async def asyncSetUp(self):
        """建立具有真实上游正式产物和生命周期的隔离工作区。"""

        self.harness = TechnicalPlanningRecoveryJourneyHarness()

    async def asyncTearDown(self):
        """关闭临时工作区的 checkpoint，再清理测试资源。"""

        await close_workflow_checkpointer_for_workspace(
            workspace=str(self.harness.workspace), project_id=self.harness.project_id,
        )
        self.harness.close()

    async def _prepare_revision(self):
        """把本轮请求与已有计划冻结到原技术事务输入边界。"""

        h = self.harness
        h.request = "在前端架构末尾追加局部修订验收 FRAGMENT-KEEP，其他内容保持不变。"
        await h.initialize_graph()
        baseline = deepcopy(h.candidate)
        commit_technical_plan_document({"workspace": str(h.workspace)}, baseline)
        snapshot = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
        await h.graph.aupdate_state(snapshot.config, {
            "technical_plan": baseline,
            "request": h.request,
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="fragment-revision", operation=ApplicationPlanningOperation.REPAIR,
                boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
                request=h.request, baseline=baseline,
            ),
        }, as_node="workflow_entry")
        return baseline

    async def _run(self, models, *, full_generation=None):
        """从生产 AG-UI 入口执行真实 Graph，仅替换外部聊天模型响应。"""

        h = self.harness
        with (
            patch("app.config.Settings.from_env", side_effect=h.current_settings),
            patch("app.agents.main.technical_plan_editor.create_chat_model", side_effect=models),
            patch("app.agents.main.planner._invoke_live_chat_model", side_effect=full_generation or AssertionError("已有候选后不能整份重生成")),
        ):
            return [frame async for frame in build_workflow_ag_ui_stream(
                graph=h.graph,
                payload={
                    "threadId": h.thread_id, "runId": h.source_run_id, "request": h.request,
                    "resume_from": "technical_planning_begin",
                    "forwardedProps": {"workspaceRoot": str(h.workspace), "workflowScope": "application_planning"},
                },
            )]

    async def test_local_edit_survives_full_validation_repair_and_stops_for_confirmation(self):
        """真实校验发现坏 Schema 后继续局部修复，保留用户要求和上一轮成功编辑。"""

        baseline = await self._prepare_revision()
        old_frontend = baseline["architecture"]["frontend"]
        ref = baseline["api_contracts"][0]["endpoints"][0]["response_schema_ref"]
        first = ScriptedEditorModel(responses=[
            tool_response("edit_file", file_path="/technical-plan.json", old_string=old_frontend,
                          new_string=old_frontend + "局部修订验收 FRAGMENT-KEEP。"),
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string=f'"response_schema_ref": "{ref}"',
                          new_string='"response_schema_ref": "FragmentMissingOutput"'),
            AIMessage(content="已完成修改。"),
        ])
        second = ScriptedEditorModel(responses=[
            tool_response("grep", pattern="FragmentMissingOutput", output_mode="content"),
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string='"response_schema_ref": "FragmentMissingOutput"',
                          new_string=f'"response_schema_ref": "{ref}"'),
            AIMessage(content="已修复引用，并保留原始修改意见。"),
        ])
        with patch.object(planning, "_project_plan_validation_errors", wraps=planning._project_plan_validation_errors) as validate:
            frames = await self._run([first, second])
        state = await self.harness.graph.aget_state({"configurable": {"thread_id": self.harness.thread_id}})
        self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
        self.assertTrue(any(task.interrupts for task in state.tasks))
        self.assertGreaterEqual(validate.call_count, 2)
        result = state.values["technical_plan"]
        self.assertIn("FRAGMENT-KEEP", result["architecture"]["frontend"])
        self.assertEqual(result["api_contracts"], baseline["api_contracts"])
        self.assertEqual(result["entities"], baseline["entities"])
        self.assertEqual(result["pages"], baseline["pages"])
        self.assertEqual(result["confirmation_status"], "pending_user_confirmation")
        self.assertNotIn("FragmentMissingOutput", technical_plan_json_path(state.values).read_text())
        for model in (first, second):
            for messages in model.seen_messages:
                self.assertIn(self.harness.request, messages[0].content)
        self.assertIn("FragmentMissingOutput", second.seen_messages[0][0].content)

    async def test_failed_local_repair_does_not_overwrite_existing_plan(self):
        """三轮候选持续不合法时，磁盘既有产物保持不变且只展示生成错误审阅门。"""

        baseline = await self._prepare_revision()
        path = technical_plan_json_path({"workspace": str(self.harness.workspace)})
        before = path.read_bytes()
        models = [ScriptedEditorModel(responses=[
            tool_response("edit_file", file_path="/technical-plan.json", old_string='"pages":', new_string='"pages" invalid:'),
            AIMessage(content="完成。"),
        ]) for _ in range(3)]
        frames = await self._run(models)
        self.assertEqual(path.read_bytes(), before)
        state = await self.harness.graph.aget_state({"configurable": {"thread_id": self.harness.thread_id}})
        self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
        self.assertEqual(state.values["clarification"]["mode"], "technical_plan_generation_error")
        self.assertNotIn("invalid", json.dumps(baseline))

    async def test_initial_candidate_repairs_schema_and_language_with_native_tools(self):
        """首版完整候选的 Schema 和中文说明错误统一局部修复，并保留正式确认门。"""

        h = self.harness
        await h.initialize_graph()
        sections = {key: deepcopy(h.candidate[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        ref = sections["api_contracts"][0]["endpoints"][0]["response_schema_ref"]
        sections["api_contracts"][0]["endpoints"][0]["response_schema_ref"] = "InitialMissingOutput"
        sections["architecture"]["frontend"] = "React renders weather forecasts."
        generated_inputs = []

        def generate(requirement, **kwargs):
            """只允许一次完整生成，后续任何整份重写都使测试失败。"""

            generated_inputs.append(deepcopy(requirement))
            self.assertEqual(len(generated_inputs), 1)
            return json.dumps(sections, ensure_ascii=False)

        editor = ScriptedEditorModel(responses=[
            tool_response("grep", pattern="InitialMissingOutput", output_mode="content"),
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string='"response_schema_ref": "InitialMissingOutput"',
                          new_string=f'"response_schema_ref": "{ref}"'),
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string="React renders weather forecasts.", new_string="React 渲染天气预报。"),
            AIMessage(content="已修复引用和中文说明，等待确认。"),
        ])
        with patch("app.agents.main.planner._invoke_prompt_with_chat_model", side_effect=AssertionError("不能另起翻译模型")):
            frames = await self._run([editor], full_generation=generate)
        state = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
        self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
        self.assertTrue(any(task.interrupts for task in state.tasks))
        result = state.values["technical_plan"]
        self.assertEqual(result["architecture"]["frontend"], "React 渲染天气预报。")
        self.assertEqual(result["api_contracts"], h.candidate["api_contracts"])
        self.assertEqual(result["entities"], h.candidate["entities"])
        self.assertEqual(result["pages"], h.candidate["pages"])
        self.assertEqual(result["confirmation_status"], "pending_user_confirmation")
        self.assertTrue(technical_plan_json_path(state.values).is_file())
        self.assertEqual(len(generated_inputs), 1)
        for messages in editor.seen_messages:
            self.assertIn(h.request, messages[0].content)
            self.assertIn("InitialMissingOutput", messages[0].content)
            self.assertIn("architecture.frontend", messages[0].content)

    async def test_initial_generation_repeats_only_until_a_complete_candidate_exists(self):
        """截断和缺章节均不形成编辑基线，仍在三轮预算内保留要求重新完整生成。"""

        h = self.harness
        await h.initialize_graph()
        sections = {key: deepcopy(h.candidate[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        outputs = iter(['{"architecture": {}, "entities": [', '{"architecture": {}}', json.dumps(sections)])
        generated_inputs = []

        def generate(requirement, **kwargs):
            """返回无法解析、缺少章节和完整候选，并记录每轮实际输入。"""

            self.assertFalse(kwargs.get("existing_plan"))
            generated_inputs.append(deepcopy(requirement))
            return next(outputs)

        frames = await self._run([], full_generation=generate)
        state = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
        self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
        self.assertEqual(state.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(len(generated_inputs), 3)
        self.assertIn("无法恢复", generated_inputs[1]["technical_plan_validation_feedback"])
        self.assertIn("缺少字段", generated_inputs[2]["technical_plan_validation_feedback"])
        for requirement in generated_inputs:
            self.assertEqual(requirement["planning_adjustment_request"], h.request)

    async def test_initial_model_fields_rejected_before_materialization_are_edited(self):
        """根 JSON 已完整但平台拒绝模型权限字段时，仍保留原候选走局部修复。"""

        h = self.harness
        await h.initialize_graph()
        sections = {key: deepcopy(h.candidate[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        forbidden = {"required": False, "roles": ["admin"]}
        sections["api_contracts"][0]["authentication"] = forbidden
        generated_inputs = []
        original_config = (h.workspace / ".devagentstudio/application.json").read_bytes()

        def generate(requirement, **kwargs):
            """完整生成仅执行一次，禁止因权限字段校验失败而重写计划。"""

            generated_inputs.append(deepcopy(requirement))
            self.assertEqual(len(generated_inputs), 1)
            return json.dumps(sections)

        editor = ScriptedEditorModel(responses=[
            tool_response("grep", pattern="roles", output_mode="content"),
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string=json.dumps(forbidden, indent=2).replace("\n", "\n      "),
                          new_string=json.dumps({"required": False}, indent=2).replace("\n", "\n      ")),
            AIMessage(content="已移除模型越界字段，保留应用配置。"),
        ])
        frames = await self._run([editor], full_generation=generate)
        state = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
        self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
        self.assertEqual(state.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(len(generated_inputs), 1)
        self.assertNotIn("roles", state.values["technical_plan"]["api_contracts"][0]["authentication"])
        self.assertEqual((h.workspace / ".devagentstudio/application.json").read_bytes(), original_config)
        for messages in editor.seen_messages:
            self.assertIn(h.request, messages[0].content)
            self.assertIn("authentication 只能包含 boolean required", messages[0].content)

    async def test_initial_candidate_exhausts_edit_budget_without_full_regeneration(self):
        """首版已有候选后，局部修复耗尽只能报错，不能回退全量或写入坏产物。"""

        h = self.harness
        await h.initialize_graph()
        sections = {key: deepcopy(h.candidate[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        sections["api_contracts"][0]["endpoints"][0]["response_schema_ref"] = "InitialMissingOutput"
        generated_inputs = []

        def generate(requirement, **kwargs):
            """首个候选产生后禁止再次调用完整生成器。"""

            generated_inputs.append(deepcopy(requirement))
            self.assertEqual(len(generated_inputs), 1)
            return json.dumps(sections)

        editors = [ScriptedEditorModel(responses=[
            tool_response("edit_file", file_path="/technical-plan.json", old_string='"pages":', new_string='"pages" invalid:'),
            AIMessage(content="完成。"),
        ]) for _ in range(2)]
        frames = await self._run(editors, full_generation=generate)
        state = await h.graph.aget_state({"configurable": {"thread_id": h.thread_id}})
        self.assertEqual(tuple(state.next), ("technical_planning_review",), "\n".join(frames))
        self.assertEqual(state.values["clarification"]["mode"], "technical_plan_generation_error")
        self.assertFalse(technical_plan_json_path(state.values).exists())
        self.assertEqual(len(generated_inputs), 1)
        self.assertEqual(state.values["technical_plan_repair_candidate"]["api_contracts"][0]["endpoints"][0]["response_schema_ref"], "InitialMissingOutput")
        for editor in editors:
            for messages in editor.seen_messages:
                self.assertIn(h.request, messages[0].content)
