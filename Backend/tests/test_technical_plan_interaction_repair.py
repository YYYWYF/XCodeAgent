"""复现文本会话被误写为业务输入后，精确诊断与原生局部修复的闭环。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.agents.main.planner import _technical_planning_prompt
from app.agents.main.technical_plan_editor import _editor_system_prompt
from app.graph.nodes import planning
from app.services.project_plan import create_technical_plan
from app.services.runtime_agent_contracts import agent_contract_index
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import capability, plan as runtime_plan
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, tool_response
from tests.test_technical_planning_agent_context import _planning_inputs


def invalid_interaction() -> dict[str, Any]:
    """复现实际失败的三个字段，不把用户消息和回复当作结构化业务契约。"""

    value = capability()
    value["inputSchema"]["properties"] = {"message": {"type": "string", "description": "用户输入的文本消息。"}}
    value["inputSchema"]["required"] = ["message"]
    value["outcomes"] = ["回复显示在当前聊天界面的消息列表中"]
    return value


class TechnicalPlanInteractionRepairTests(TestCase):
    """验证原始错误可定位、候选不被静默修改，且恢复仍须正式确认。"""

    def _generate(self, spec: dict[str, Any], product: dict[str, Any], candidate: dict[str, Any] | None = None,
                  *, initial_errors: list[str] | None = None) -> tuple:
        """用当前应用配置执行真实生成和全量复验，所有磁盘操作均限制在临时目录。"""

        with TemporaryDirectory() as directory:
            application = Path(directory) / ".devagentstudio/application.json"
            application.parent.mkdir(parents=True)
            config = {"schemaVersion": 6, "appName": "聊天应用", "configRevision": 1, "auth": {"enable": False},
                      "authorization": {"enabled": False, "initialAdministratorSubjects": []},
                      "datasource": {"type": "database"}}
            application.write_text(json.dumps(config), encoding="utf-8")
            state = {"workspace": directory, "workflow_scope": "application_planning",
                     "requirement_spec": spec, "product_plan": product,
                     "ui_designs": {"confirmation_status": "skipped", "pages": []}}
            return planning._generate_valid_technical_plan(state, spec, candidate, initial_errors=initial_errors)

    def test_invalid_candidate_reports_agent_paths_values_and_expected_values(self) -> None:
        """正式编译与 Runtime 物化都应提供同样明确的可执行诊断。"""

        spec, _product, candidate = _planning_inputs()
        candidate["agent_contracts"][0]["interactionContract"] = invalid_interaction()
        runtime = runtime_plan()
        runtime["agent_contracts"][1]["interactionContract"] = invalid_interaction()
        cases = [
            (candidate, "agent_general_chat", "agent_contracts[0]", lambda: create_technical_plan(spec, agent_plan=candidate)),
            (runtime, "policy_assistant", "agent_contracts[1]", lambda: materialize_runtime_endpoints(runtime)),
        ]
        for source, agent_id, path, validate in cases:
            with self.subTest(agent_id=agent_id):
                before = deepcopy(source)
                with self.assertRaises(ValueError) as raised:
                    validate()
                diagnostic = str(raised.exception)
                for suffix in ("inputSchema.properties", "inputSchema.required", "outcomes"):
                    self.assertIn(f"{path}.interactionContract.{suffix}", diagnostic)
                for text in (agent_id, "当前值", "必须为 {}", "必须为 []", "AG-UI messages"):
                    self.assertIn(text, diagnostic)
                self.assertNotIn("errors.pydantic.dev", diagnostic)
                self.assertEqual(source, before)

    def test_each_invalid_field_remains_rejected(self) -> None:
        """改善错误信息不能放宽空结构、文本输出或交互能力的严格校验。"""

        for key, value in (
            ("inputSchema", invalid_interaction()["inputSchema"]),
            ("outcomes", ["文本回复"]),
            ("outcomes", [{"name": "自定义业务结果"}]),
            ("textOutput", False),
            ("publicStateSchema", {}),
            ("interactionKinds", ["unknown"]),
        ):
            with self.subTest(key=key, value=value):
                candidate = runtime_plan()
                candidate["agent_contracts"][0]["interactionContract"][key] = value
                before = deepcopy(candidate)
                with self.assertRaises(ValueError):
                    agent_contract_index(candidate)
                self.assertEqual(candidate, before)

    def test_generation_and_editor_explain_message_transport(self) -> None:
        """首次生成与局部修复共享消息来源说明，避免再次虚构 message 字段。"""

        spec, _product, _candidate = _planning_inputs()
        for prompt in (_technical_planning_prompt(spec, None), _editor_system_prompt(spec)):
            self.assertIn("AG-UI messages", prompt)
            self.assertIn("不是业务输入字段", prompt)
            self.assertIn("不是业务 outcomes", prompt)

    def test_real_local_tools_repair_invalid_candidate_before_confirmation(self) -> None:
        """真实编译失败后只调用原生局部编辑，保留业务配置并等待确认。"""

        spec, product, candidate = _planning_inputs()
        candidate["agent_contracts"][0]["interactionContract"] = invalid_interaction()
        before = deepcopy(candidate)
        original_request = "保持两个页面共用一个通用聊天助手，不增加业务接口。"
        spec["planning_adjustment_request"] = original_request
        old = json.dumps(candidate["agent_contracts"][0]["interactionContract"], ensure_ascii=False, indent=2)
        new = json.dumps(capability(), ensure_ascii=False, indent=2)
        # 候选中的对象缩进为六格；文本工具仍执行精确匹配，不模拟写入成功。
        editor = ScriptedEditorModel(responses=[
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string=old.replace("\n", "\n      "),
                          new_string=new.replace("\n", "\n      ")),
            AIMessage(content="已修复会话契约，等待用户确认。"),
        ])
        with (
            patch("app.agents.main.planner._invoke_live_chat_model", return_value=json.dumps(candidate, ensure_ascii=False)) as generate,
            patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=editor),
        ):
            result, errors, failed = self._generate(spec, product)

        self.assertEqual(errors, [])
        self.assertIsNone(failed)
        self.assertIsNotNone(result)
        self.assertEqual(result["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(result["agent_contracts"][0]["interactionContract"], capability())
        self.assertEqual(result["agent_contracts"][0]["agentSettings"], before["agent_contracts"][0]["agentSettings"])
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["api_contracts"], [])
        self.assertEqual(candidate, before)
        generate.assert_called_once()
        feedback = editor.seen_messages[0][0].content
        self.assertIn(original_request, feedback)
        self.assertIn("agent_contracts[0].interactionContract.inputSchema.properties", feedback)
        self.assertIn("AG-UI messages", feedback)

    def test_interrupted_reentry_refreshes_diagnostics_from_latest_candidate(self) -> None:
        """续修先复验最新候选；超限前修好的字段不能继续作为错误，也不能跳过正式复验。"""

        spec, product, candidate = _planning_inputs()
        contract = candidate["agent_contracts"][0]
        contract["interactionContract"] = capability()
        contract["interactionContract"]["outcomes"] = ["普通文本回复"]
        before = deepcopy(candidate)
        first = ScriptedEditorModel(responses=[
            tool_response("edit_file", file_path="/technical-plan.json",
                          old_string='"普通文本回复"', new_string=""),
            *[tool_response("grep", pattern="outcomes", output_mode="content") for _ in range(8)],
        ])
        second = ScriptedEditorModel(responses=[AIMessage(content="候选已完成修复，无需额外改动，请平台复验。")])
        with (
            patch("app.agents.main.planner._invoke_live_chat_model", side_effect=AssertionError("不得重新完整生成")),
            patch("app.agents.main.technical_plan_editor.create_chat_model", side_effect=[first, second]),
        ):
            result, errors, failed = self._generate(spec, product, candidate,
                initial_errors=["旧错误：inputSchema.properties 含 message；inputSchema.required 含 message；outcomes 非空"])

        self.assertEqual(errors, [])
        self.assertIsNone(failed)
        self.assertEqual(result["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(result["agent_contracts"][0]["interactionContract"], capability())
        self.assertEqual(candidate, before)
        first_feedback = first.seen_messages[0][0].content
        self.assertIn("agent_contracts[0].interactionContract.outcomes 当前值", first_feedback)
        self.assertNotIn("旧错误", first_feedback)
        second_feedback = second.seen_messages[0][0].content
        self.assertNotIn("outcomes 当前值", second_feedback)
        self.assertNotIn("旧错误", second_feedback)
        self.assertIn("达到调用上限", second_feedback)
