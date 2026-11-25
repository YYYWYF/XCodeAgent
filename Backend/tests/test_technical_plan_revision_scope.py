"""技术修改语义识别、范围核验与现有澄清卡往返的回归。"""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents.main.technical_plan_revision_scope import (
    CLARIFICATION_MODE, TechnicalPlanRevisionScope, resolve_technical_plan_revision_scope,
    validate_revision_scope,
)
from app.domain.application_planning_interaction import ApplicationPlanningInteraction
from app.graph.application_planning_interrupts import validate_application_planning_review_action
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.application_page_planning import build_application_page_planning_ag_ui_stream
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.technical_planning_retry import prepare_technical_planning_retry
from app.workspace.plan_documents import technical_plan_draft_json_path
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness
from tests.test_technical_plan_content_changes import _changeset


def _plan() -> dict:
    """构造同路径不同方法的两个接口，检验方法选择和范围隔离。"""

    return {"api_contracts": [{"id": "logs", "name": "日志", "schemas": {"LogResponse": {}}, "endpoints": [
        {"id": "logs.get", "method": "GET", "path": "/api/personal-logs", "name": "查询日志"},
        {"id": "logs.post", "method": "POST", "path": "/api/personal-logs", "name": "新增日志"},
    ]}]}


class TechnicalPlanRevisionScopeTests(unittest.TestCase):
    """语义模型接收完整自然语言，平台不再重复以正则限制用户表达。"""

    def test_natural_method_wording_reaches_model_and_validated_get_scope(self):
        """截图中的 GET 与路径不相邻时，由模型选择稳定身份并通过代码核验。"""

        request = "我希望get接口里面的/api/personal-logs接口里面的id字段能叫diaryId"
        with patch("app.agents.main.technical_plan_revision_scope.Settings.from_env"), \
             patch("app.agents.main.technical_plan_revision_scope.create_chat_model") as model:
            model.return_value.invoke.return_value = SimpleNamespace(content=json.dumps({
                "endpoints": [{"contract_id": "logs", "method": "GET", "path": "/api/personal-logs"}],
            }))
            targets, schemas, questions = resolve_technical_plan_revision_scope(request, _plan())
        prompt = model.return_value.invoke.call_args.args[0]
        self.assertIn(request, prompt)
        self.assertIn('"method": "POST"', prompt)
        self.assertEqual(set(targets), {"logs.get"})
        self.assertEqual(targets["logs.get"]["method"], "GET")
        self.assertEqual((schemas, questions), ({}, []))

    def test_ambiguity_never_partially_executes_other_targets(self):
        """有明确目标也必须先回答全部歧义，不能先改一部分。"""

        targets, schemas, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"contract_id": "logs", "method": "GET", "path": "/api/personal-logs"}],
            "questions": ["这个路径有 GET、POST，请明确要修改哪个接口。"],
        }), _plan())
        self.assertEqual((targets, schemas), ({}, {}))
        self.assertIn("GET、POST", questions[0])

    def test_new_endpoint_does_not_require_an_existing_catalog_target(self):
        """明确新增的接口由 ChangeSet 校验，目标识别不能因旧目录不存在而阻断。"""

        targets, schemas, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"method": "GET", "path": "/api/new-logs", "operation": "add"}],
        }), _plan())
        self.assertEqual((targets, schemas, questions), ({}, {}, []))

    def test_missing_method_or_duplicate_name_cannot_choose_a_target(self):
        """模型只提取路径或接口名时，平台仍独立检查候选是否唯一。"""

        targets, _, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"path": "/api/personal-logs"}],
        }), _plan())
        self.assertEqual(targets, {})
        self.assertIn("GET /api/personal-logs", questions[0])
        self.assertIn("POST /api/personal-logs", questions[0])
        plan = _plan()
        targets, _, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"name": "查询日志"}],
        }), plan)
        self.assertEqual(set(targets), {"logs.get"})
        self.assertEqual(questions, [])
        plan["api_contracts"][0]["endpoints"][1]["name"] = "查询日志"
        targets, _, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"name": "查询日志"}],
        }), plan)
        self.assertEqual(targets, {})
        self.assertTrue(questions)

    def test_unknown_deleted_or_conflicting_identity_requires_clarification(self):
        """模型编造、已删除或互相矛盾的目标不能绕过当前身份核验。"""

        for endpoints in (
            [{"contract_id": "logs", "path": "/api/missing"}],
            [{"contract_id": "wrong", "method": "GET", "path": "/api/personal-logs"}],
            [{"contract_id": "logs", "method": "GET", "path": "/api/personal-logs", "operation": "delete"},
             {"contract_id": "logs", "method": "GET", "path": "/api/personal-logs", "operation": "modify"}],
        ):
            with self.subTest(endpoints=endpoints):
                targets, _, questions = validate_revision_scope(
                    TechnicalPlanRevisionScope.model_validate({"endpoints": endpoints}), _plan())
                self.assertEqual(targets, {})
                self.assertTrue(questions)
        plan = _plan()
        plan["api_contracts"][0]["endpoints"][0]["_change_status"] = "deleted"
        _, _, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"contract_id": "logs", "method": "GET", "path": "/api/personal-logs"}],
        }), plan)
        self.assertTrue(questions)

    def test_explicit_schema_and_path_change_are_verified_against_current_catalog(self):
        """路径新值只作为修改结果，类型仍按所属分组核验。"""

        targets, schemas, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "endpoints": [{"contract_id": "logs", "method": "GET", "path": "/api/personal-logs", "new_path": "/api/logs"}],
            "schemas": [{"contract_id": "logs", "name": "LogResponse"}],
        }), _plan())
        self.assertEqual(questions, [])
        self.assertEqual(targets["logs.get"]["path"], "/api/personal-logs")
        self.assertEqual(targets["logs.get"]["new_path"], "/api/logs")
        self.assertEqual(schemas, {"logs": {"LogResponse"}})
        _, _, questions = validate_revision_scope(TechnicalPlanRevisionScope.model_validate({
            "schemas": [{"contract_id": "logs", "name": "LogResponseExtra"}],
        }), _plan())
        self.assertTrue(questions)

    def test_bottom_supplement_keeps_original_change_and_questions(self):
        """底部只补一句 GET 时仍保留原字段改名目的及本轮问题。"""

        state = {"request": "把 id 改成 diaryId", "technical_plan": _plan(), "clarification": {
            "mode": CLARIFICATION_MODE, "questions": [{"header": "修改范围1", "question": "GET 还是 POST？"}],
        }}
        with patch("app.services.technical_planning_retry.current_technical_plan_json_path") as path:
            path.return_value.is_file.return_value = False
            update = prepare_technical_planning_retry(state, new_request="GET")
            with self.assertRaisesRegex(ValueError, "不能为空"):
                prepare_technical_planning_retry(state, new_request="   ")
        self.assertIn("把 id 改成 diaryId", update["request"])
        self.assertIn("GET", update["request"])
        self.assertEqual(update["request"], update["design_change_generation_request"])


class TechnicalPlanClarificationJourneyTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 SQLite、Graph 和 AG-UI 检查歧义卡及两种补充入口。"""

    async def asyncSetUp(self):
        """生成待确认计划，随后发起一次需要澄清的修改。"""

        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.settings = patch("app.config.Settings.from_env", side_effect=self.h.current_settings)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.scope_patch = patch("app.graph.nodes.application_technical_planning.resolve_technical_plan_revision_scope")
        self.scope = self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)
        await self.h.initialize_graph()
        with patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(self.h.candidate), [], None)):
            await self.h.run_initial()
        self.config = {"configurable": {"thread_id": self.h.thread_id}}
        self.draft_path = technical_plan_draft_json_path({"workspace": str(self.h.workspace)})
        self.draft = self.draft_path.read_bytes()
        self.original = "把接口里的 id 改成 diaryId"
        self.scope.return_value = ({}, {}, ["同一路径有 GET、POST，请明确修改哪个接口。"])
        with patch("app.graph.nodes.planning._generate_valid_technical_plan") as generate:
            events = await self._submit_card("revise", self.original)
        generate.assert_not_called()
        self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events))
        self.pending = await self.h.graph.aget_state(self.config)
        self.assertEqual(self.pending.values["clarification"]["mode"], CLARIFICATION_MODE)
        self.assertEqual(self.draft_path.read_bytes(), self.draft)

    async def asyncTearDown(self):
        """释放测试工作区和 checkpoint，不触及真实应用。"""

        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace))
        self.h.close()

    async def _submit_card(self, action: str, request: str = "", answers: dict | None = None):
        """绑定真实当前审阅门提交卡片动作并返回完整 AG-UI 事件。"""

        snapshot = await self.h.graph.aget_state(self.config)
        pending = snapshot.tasks[0].interrupts[0].value
        frames = [frame async for frame in build_workflow_ag_ui_stream(graph=self.h.graph, payload={
            "threadId": self.h.thread_id, "runId": f"card-{action}", "request": request or "补充修改信息",
            "forwardedProps": {"workspaceRoot": str(self.h.workspace), "workflowScope": "application_planning",
                "applicationPlanningInteraction": {
                    "gateId": pending["gateId"], "artifact": pending["artifact"],
                    "artifactRevision": pending["artifactRevision"], "action": action,
                    "request": request, "answers": answers or {},
                }},
        })]
        return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

    def _generator(self, requirement, document, request, **kwargs):
        """仅替换 ChangeSet 模型，真实原子执行、校验、保存与审阅门照常执行。"""

        before = document["architecture"]["frontend"]
        return _changeset(document, request, [{
            "op": "replace", "path": "/architecture/frontend",
            "expectedBefore": before, "value": before + "，本轮澄清后修改。",
        }])

    async def _assert_new_confirmation(self):
        """澄清回答不能直接确认产物，生成新版后必须再次停回正式确认卡。"""

        snapshot = await self.h.graph.aget_state(self.config)
        self.assertEqual(snapshot.tasks[0].interrupts[0].value["clarification"]["mode"], "technical_plan_confirmation")
        self.assertEqual(snapshot.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        self.assertNotEqual(self.draft_path.read_bytes(), self.draft)
        self.assertEqual(self.scope.call_args.args[0].count(self.original), 1)
        self.assertIn("GET", self.scope.call_args.args[0])

    async def test_existing_card_answer_reanalyzes_and_requires_new_confirmation(self):
        """原问题卡输入 GET，完整问答进入语义分析并正常产出新待确认版本。"""

        self.scope.return_value = ({}, {}, [])
        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", side_effect=self._generator) as generate:
            events = await self._submit_card("answer", answers={"修改范围1": "GET"})
        self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events), events[-1])
        output = [event["value"] for event in events if event.get("name") == "technical-planning.output"]
        self.assertTrue(any(item["text"] == "技术修改操作已生成" for item in output))
        self.assertTrue(any(item["text"] == "技术修改已应用并通过校验，正在准备待确认版本" for item in output))
        self.assertNotIn("expectedBefore", json.dumps(output))
        generate.assert_called_once()
        self.assertIn("GET", generate.call_args.args[0]["planning_adjustment_request"])
        await self._assert_new_confirmation()

    async def test_bottom_input_reanalyzes_with_original_request(self):
        """底部新输入走原对话端点，清除歧义后正常进入原修改和确认链路。"""

        self.scope.return_value = ({}, {}, [])
        snapshot = await self.h.graph.aget_state(self.config)
        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", side_effect=self._generator) as generate:
            frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.h.graph, payload={
                "threadId": self.h.thread_id, "runId": "bottom-supplement", "forwardedProps": {
                    "technicalPlanningConversation": {
                        "action": "revise", "workspaceRoot": str(self.h.workspace), "applicationId": self.h.project_id,
                        "planningThreadId": self.h.thread_id, "request": "GET接口里面的那个",
                        "sourceRunId": snapshot.values["active_run_id"],
                        "checkpointId": snapshot.config["configurable"]["checkpoint_id"],
                    }},
            })]
        self.assertNotIn('"type":"RUN_ERROR"', "".join(frames))
        generate.assert_called_once()
        await self._assert_new_confirmation()

    async def test_clarification_cannot_be_submitted_as_formal_confirmation(self):
        """澄清卡只补充事实，不能提交 confirm 绕过新产物生成。"""

        pending = self.pending.tasks[0].interrupts[0].value
        submission = ApplicationPlanningInteraction.model_validate({
            "gateId": pending["gateId"], "artifact": pending["artifact"],
            "artifactRevision": pending["artifactRevision"], "action": "confirm",
        })
        with self.assertRaises(ValueError):
            validate_application_planning_review_action(self.pending.values, "technical_planning", submission)
        self.assertEqual(self.draft_path.read_bytes(), self.draft)
