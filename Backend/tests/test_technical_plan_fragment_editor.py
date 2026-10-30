"""使用真实 DeepAgents 文件工具验证局部技术修订，模型响应由本地夹具控制。"""

from copy import deepcopy
import json
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.main import planner
from app.agents.main.technical_plan_editor import (
    TECHNICAL_PLAN_CANDIDATE_PATH,
    TechnicalPlanCandidateBackend,
    edit_technical_plan_with_agent,
)
from app.config import Settings
from app.services.run_workers import bind_run_workers
from app.services.workspace_process_registry import workspace_process_registry


class ScriptedEditorModel(BaseChatModel):
    """提供原生工具调用消息，真正执行候选内存后端和中间件。"""

    responses: list[AIMessage]
    seen_messages: list = []
    seen_tools: list = []

    @property
    def _llm_type(self):
        """标识不连接外部服务的工具循环测试模型。"""

        return "technical-plan-editor-test"

    def bind_tools(self, tools, **kwargs):
        """记录本次真实工具绑定，确保没有代码执行或整文件写入能力。"""

        self.seen_tools.append([tool.name for tool in tools])
        return self

    def _generate(self, messages, **kwargs):
        """记录模型实际收到的消息，再返回下一步受控工具动作。"""

        self.seen_messages.append(deepcopy(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def tool_response(name: str, **arguments) -> AIMessage:
    """构造单次工具调用，不伪造工具执行结果。"""

    return AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": f"call-{name}"}])


def candidate_plan() -> dict:
    """构造含多个接口和稳定引用的完整技术规划。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "product_plan_sha256": "baseline-only-metadata",
        "architecture": {"frontend": "React 前端。", "backend": "Java8 Springboot 后端。", "data": "MySQL8 Redis 存储。"},
        "entities": [{"id": "Order", "name": "订单", "description": "订单实体。", "fields": []}],
        "api_contracts": [{
            "id": "orders_api", "name": "订单管理", "entity_ids": ["Order"],
            "base_path": "/api/orders", "schemas": {},
            "endpoints": [
                {"id": "orders.list", "name": "订单列表", "method": "GET", "path": "/api/orders",
                 "parameters": [{"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 20}}]},
                {"id": "orders.other", "name": "其他列表", "method": "GET", "path": "/api/other",
                 "parameters": [{"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 30}}]},
            ],
        }],
        "pages": [],
    }


class TechnicalPlanFragmentEditorTests(TestCase):
    """覆盖真实工具循环、唯一匹配、候选隔离及失败不提交边界。"""

    def setUp(self):
        """准备业务要求和禁止暴露给模型的连接信息哨兵。"""

        self.request = "仅将订单列表的 pageSize 默认值改为8，其他接口和字段保持不变。"
        self.requirement = {
            "confirmed_product_plan": {"pages": []},
            "planning_adjustment_request": self.request,
            "technical_plan_validation_feedback": "本轮需复核 Schema 引用。",
            "application_config": {"auth": {"enable": False}, "datasource": {"pwd": "SECRET_SENTINEL"}},
        }
        self.baseline = candidate_plan()
        self.settings = Settings(model_base_url="http://unused", model_api_key="unused", model_name="test")

    def _run_editor(self, responses, *, baseline=None):
        """用本地模型驱动原生 LangChain Agent 和三个 DeepAgents 工具。"""

        model = ScriptedEditorModel(responses=responses)
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
            candidate = edit_technical_plan_with_agent(
                self.requirement, baseline if baseline is not None else self.baseline,
                settings=self.settings,
            )
        return candidate, model

    def test_native_search_read_edit_changes_only_one_parameter(self):
        """搜索和读取后只替换一个参数，其他接口及基线输入保持原样。"""

        original = deepcopy(self.baseline)
        result, model = self._run_editor([
            tool_response("grep", pattern="orders.list", output_mode="content"),
            tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH, offset=0, limit=100),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": 8'),
            tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH, offset=0, limit=100),
            AIMessage(content="已修改分页默认值。"),
        ])
        expected = {key: deepcopy(original[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        expected["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"] = 8
        self.assertEqual(result, expected)
        self.assertEqual(self.baseline, original)
        for messages in model.seen_messages:
            self.assertIn(self.request, messages[0].content)
            self.assertIn("本轮需复核 Schema 引用", messages[0].content)
            self.assertNotIn("SECRET_SENTINEL", str(messages))
        self.assertEqual(set(model.seen_tools[0]), {"grep", "read_file", "edit_file"})
        self.assertNotIn("orders.other", model.seen_messages[0][0].content)

    def test_ambiguous_match_returns_error_then_can_retry_with_context(self):
        """原生 edit_file 拒绝重复匹配，模型可补充上下文后完成同一候选。"""

        result, model = self._run_editor([
            tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"type": "integer"', new_string='"type": "number"'),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="已使用唯一片段修改。"),
        ])
        errors = [m for messages in model.seen_messages for m in messages if isinstance(m, ToolMessage) and m.status == "error"]
        self.assertTrue(any("2 times" in str(m.content) for m in errors))
        self.assertEqual(result["api_contracts"][0]["endpoints"][1]["parameters"][0]["schema"]["default"], 30)

    def test_upstream_and_real_workspace_paths_are_not_writable(self):
        """工具只能编辑内存技术候选，产品事实和工作区路径均被拒绝。"""

        result, model = self._run_editor([
            tool_response("edit_file", file_path="/context/product-plan.json", old_string="[]", new_string="[1]"),
            tool_response("edit_file", file_path="/.devagentstudio/plans/technical-plan.json", old_string="20", new_string="8"),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH, old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="已修改候选。"),
        ])
        errors = [m for messages in model.seen_messages for m in messages if isinstance(m, ToolMessage) and m.status == "error"]
        self.assertTrue(any("上游上下文只读" in str(m.content) for m in errors))
        self.assertEqual(self.requirement["confirmed_product_plan"], {"pages": []})
        self.assertEqual(result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8)

    def test_model_summary_cannot_replace_candidate_file(self):
        """模型只声称完成而没有成功编辑时，不能将未改候选作为成功结果。"""

        with self.assertRaisesRegex(ValueError, "没有应用任何局部修改"):
            self._run_editor([AIMessage(content='{"architecture":{},"entities":[],"api_contracts":[],"pages":[]}')])

    def test_invalid_json_is_rejected_without_changing_baseline(self):
        """候选最终 JSON 不合法时拒绝返回，工作区基线不受失败编辑影响。"""

        original = deepcopy(self.baseline)
        with self.assertRaisesRegex(ValueError, "不是有效 JSON"):
            self._run_editor([
                tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                              old_string='"default": 20', new_string='"default": invalid'),
                AIMessage(content="完成。"),
            ])
        self.assertEqual(self.baseline, original)

    def test_intermediate_invalid_json_can_be_completed_by_later_edits(self):
        """允许同一轮关联编辑的中间 JSON 暂时不合法，但最终必须恢复合法。"""

        result, _ = self._run_editor([
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": PENDING'),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": PENDING', new_string='"default": 8'),
            AIMessage(content="已完成两步修改。"),
        ])
        self.assertEqual(result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8)

    def test_multiple_edits_in_one_tool_batch_preserve_both_changes(self):
        """模型同次返回多个编辑调用时，两处修改均需保留，不能后写覆盖先写。"""

        second_completed = Event()
        native_edit = TechnicalPlanCandidateBackend.edit

        def reverse_completion(backend, file_path, old_string, new_string, replace_all=False):
            """让第二个调用先完成，稳定复现 Graph 合并顺序与实际写入顺序不同。"""

            if old_string == '"default": 20':
                self.assertTrue(second_completed.wait(timeout=3))
            result = native_edit(backend, file_path, old_string, new_string, replace_all)
            if old_string == '"default": 30':
                second_completed.set()
            return result

        with patch.object(TechnicalPlanCandidateBackend, "edit", new=reverse_completion):
            result, model = self._run_editor([
                AIMessage(content="", tool_calls=[
                    {"name": "edit_file", "id": f"edit-{old}", "args": {
                        "file_path": TECHNICAL_PLAN_CANDIDATE_PATH,
                        "old_string": f'"default": {old}', "new_string": f'"default": {new}',
                    }} for old, new in ((20, 8), (30, 9))
                ]),
                tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH),
                AIMessage(content="两处修改完成。"),
            ])
        endpoints = result["api_contracts"][0]["endpoints"]
        self.assertEqual([item["parameters"][0]["schema"]["default"] for item in endpoints], [8, 9])
        read_back = model.seen_messages[-1][-1].content
        self.assertIn('"default": 8', read_back)
        self.assertIn('"default": 9', read_back)

    def test_unknown_root_fields_are_rejected(self):
        """模型不能向四段候选注入应用配置或其他根字段。"""

        with self.assertRaisesRegex(ValueError, "必须且只能包含"):
            self._run_editor([
                tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                              old_string='"pages": []', new_string='"pages": [], "auth": {"enable": true}'),
                AIMessage(content="完成。"),
            ])

    def test_existing_plan_dispatches_to_editor_without_full_generation(self):
        """已有 TechnicalPlan 直接进入局部工具路径，不调用完整生成模型。"""

        sections = {key: self.baseline[key] for key in ("architecture", "entities", "api_contracts", "pages")}
        with (
            patch.object(planner.Settings, "from_env", return_value=self.settings),
            patch.object(planner, "edit_technical_plan_with_agent", return_value=sections) as editor,
            patch.object(planner, "_invoke_live_chat_model", side_effect=AssertionError("不应整份生成")),
            patch.object(planner, "create_technical_plan", return_value={"candidate": True}),
        ):
            result = planner.plan_project_with_chat_model(self.requirement, existing_plan=self.baseline)
        self.assertEqual(result, {"candidate": True})
        self.assertEqual(editor.call_args.args[0]["planning_adjustment_request"], self.request)

    def test_editor_call_budget_is_bounded(self):
        """无休止搜索在有限模型轮次内失败，不产生成功候选。"""

        with self.assertRaisesRegex(ValueError, "达到调用上限"):
            self._run_editor([tool_response("grep", pattern="orders") for _ in range(10)])

    def test_generation_without_complete_candidate_uses_full_route(self):
        """没有完整候选时继续完整生成，不把缺失章节或类型错误的对象送入编辑器。"""

        sections = {key: self.baseline[key] for key in ("architecture", "entities", "api_contracts", "pages")}
        with (
            patch.object(planner.Settings, "from_env", return_value=self.settings),
            patch.object(planner, "edit_technical_plan_with_agent") as editor,
            patch.object(planner, "_invoke_live_chat_model", return_value=json.dumps(sections)) as generate,
            patch.object(planner, "create_technical_plan", return_value={"candidate": True}),
        ):
            for baseline in (None, {"architecture": {}}, {**sections, "api_contracts": [1]}):
                result = planner.plan_project_with_chat_model(self.requirement, existing_plan=baseline)
                self.assertEqual(result, {"candidate": True})
        self.assertEqual(generate.call_count, 3)
        editor.assert_not_called()

    def test_whole_candidate_replacement_is_rejected(self):
        """即使模型读到整份候选，也不能借 edit_file 绕过禁止整文件写入的约束。"""

        sections = {key: self.baseline[key] for key in ("architecture", "entities", "api_contracts", "pages")}
        whole = json.dumps(sections, ensure_ascii=False, indent=2)
        with self.assertRaisesRegex(ValueError, "不能替换整份候选"):
            self._run_editor([
                tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                              old_string=whole, new_string=whole.replace('"default": 20', '"default": 8')),
                AIMessage(content="完成。"),
            ])

    def test_unresolved_error_after_partial_edit_is_not_success(self):
        """部分编辑成功后若最后一项修改失败，不能把模型完成回复作为整体成功。"""

        with self.assertRaisesRegex(ValueError, "未解决的工具错误"):
            self._run_editor([
                tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                              old_string='"default": 20', new_string='"default": 8'),
                tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                              old_string="does-not-exist", new_string="ignored"),
                AIMessage(content="完成。"),
            ])

    def test_cancelled_run_cannot_start_editor_model_or_tools(self):
        """沿用现有运行取消边界，已停止的修订不会再调用模型或修改候选。"""

        run_id = "technical-plan-editor-cancelled"
        workspace_process_registry.cancel_run(run_id)
        try:
            with bind_run_workers(run_id, "/tmp/technical-plan-editor-cancelled"):
                with self.assertRaisesRegex(RuntimeError, "当前运行已停止"):
                    self._run_editor([AIMessage(content="不应执行。")])
        finally:
            workspace_process_registry.allow_run(run_id)

    def test_indentation_error_returns_exact_suggestion_without_fuzzy_edit(self):
        """模型带错展示缩进时仅返回唯一短片段建议，随后仍由原生工具精确替换。"""

        result, model = self._run_editor([
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string=' ' * 40 + '"default": 20', new_string=' ' * 40 + '"default": 8'),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="已按原文片段修改。"),
        ])
        errors = [m for messages in model.seen_messages for m in messages if isinstance(m, ToolMessage) and m.status == "error"]
        self.assertTrue(any("原文片段可唯一匹配" in str(m.content) for m in errors))
        self.assertEqual(result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8)

    def test_agent_contracts_are_preserved_when_editing_other_sections(self):
        """既有候选包含 Agent 合同时，局部修订必须整段保留，不能被四段白名单剥离。"""

        baseline = candidate_plan()
        baseline["agent_contracts"] = [{
            "agentId": "support_bot",
            "name": "客服智能体",
            "purpose": "解答订单查询问题。",
            "pageIds": [],
            "agentSettings": {"prompt": {"systemPrompt": "仅回答订单相关问题。"}},
        }]
        result, _ = self._run_editor([
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="已修改订单列表默认值。"),
        ], baseline=baseline)
        self.assertEqual(result["agent_contracts"], baseline["agent_contracts"])
        self.assertEqual(
            result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8,
        )
