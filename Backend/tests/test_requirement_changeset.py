"""需求待确认卡复用 ChangeSet 的原子修订、澄清和真实联合确认回归。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agents.main.requirement_revision import (
    generate_requirement_revision, requirement_revision_document,
)
from app.domain.application_planning_recovery import application_planning_sha256
from app.graph.state import ProjectState
from app.graph.nodes.requirements import requirements
from app.graph.nodes.product_planning import product_planning
from app.graph.application_planning_interrupts import requirement_document_review, requirements_review
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.requirement_spec_merger import requirement_revision_diff
from app.tools.ask_user import AskUserInput
from app.workspace.spec_documents import write_requirement_spec_draft_document
from tests.test_requirement_document_special_interactions import submission


def change(document: dict, request: str, operations: list[dict]) -> dict:
    """使用与技术规划相同的 ChangeSet Schema，为联合文档生成测试操作。"""

    return {"schemaVersion": 1, "baseSha256": application_planning_sha256(document),
            "intents": [{"id": "intent-1", "sourceText": request, "operations": operations}]}


class RequirementChangeSetTests(unittest.TestCase):
    """复用同一执行器，修改结果不能再被需求/产品旧合并器撤销。"""

    def setUp(self):
        """准备有效模块、页面与包含邮箱信息项的需求产品草稿。"""

        self.spec = create_requirement_spec("创建人员管理应用")
        self.spec["confirmation_status"] = "pending_user_confirmation"
        self.product = create_product_plan(self.spec)
        self.page_id = self.spec["pages"][0]["pageId"]
        self.product["pages"][0]["information_items"].append({"itemId": "email", "label": "邮箱", "description": "人员邮箱。"})
        self.state = {"requirement_spec": self.spec, "product_plan": self.product}

    def test_cross_document_remove_and_text_revision_preserve_unrelated_content(self):
        """一次修改两份文档，删除信息项与修改页面说明均不依赖业务词特判。"""

        request = "删除人员邮箱，并补充页面说明"
        document = requirement_revision_document(self.spec, self.product)
        old = self.spec["pages"][0]["description"]
        changeset = change(document, request, [
            {"op": "replace", "path": f"/requirement_spec/pages/{self.page_id}/description", "expectedBefore": old, "value": old + " 不再展示邮箱。"},
            {"op": "remove", "path": f"/product_plan/pages/{self.page_id}/information_items/email", "expectedBefore": self.product["pages"][0]["information_items"][-1]},
        ])
        before = deepcopy(self.state)
        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset):
            result = generate_requirement_revision(self.state, self.spec, request)
        self.assertEqual(self.state, before)
        candidate = result["requirement_revision_product_candidate"]
        self.assertFalse(any(item["itemId"] == "email" for item in candidate["pages"][0]["information_items"]))
        self.assertEqual(candidate["pages"][1:], self.product["pages"][1:])
        self.assertEqual(result["requirement_spec_diff"]["pages"][0]["_change_status"], "modified")
        self.assertEqual(result["requirement_spec"]["confirmation_status"], "pending_user_confirmation")

    def test_page_overview_diff_tracks_interaction_and_layout_independently(self):
        """只改路由时交互与布局均未变，修改操作结果时仅交互标修改。"""
        old_path = self.spec["pages"][0]["path"]
        document = requirement_revision_document(self.spec, self.product)
        route_request = "人员明细列表路由改成 /page/detail"
        route_changes = change(document, route_request, [
            {"op": "replace", "path": f"/requirement_spec/pages/{self.page_id}/path", "expectedBefore": old_path, "value": "/page/detail"},
            {"op": "replace", "path": f"/product_plan/pages/{self.page_id}/path", "expectedBefore": old_path, "value": "/page/detail"},
        ])
        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=route_changes):
            route_result = generate_requirement_revision(self.state, self.spec, route_request)
        route_overview = route_result["requirement_spec_diff"]["_page_design_overview"][self.page_id]
        self.assertEqual(route_overview, {"interaction": "unchanged", "layout": "unchanged"})

        action = {
            "actionId": "load_list", "name": "加载列表", "description": "加载人员明细列表。",
            "requiresConfirmation": False,
            "behavior": {"type": "business", "expectedResult": "展示人员明细列表。"},
        }
        self.product["pages"][0]["actions"] = [action]
        action_document = requirement_revision_document(self.spec, self.product)
        old_result = action["behavior"]["expectedResult"]
        action_request = "修改分页操作的预期结果"
        action_changes = change(action_document, action_request, [{
            "op": "replace",
            "path": f"/product_plan/pages/{self.page_id}/actions/{action['actionId']}/behavior/expectedResult",
            "expectedBefore": old_result,
            "value": "分页数据已经刷新。",
        }])
        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=action_changes):
            action_result = generate_requirement_revision(self.state, self.spec, action_request)
        action_overview = action_result["requirement_spec_diff"]["_page_design_overview"][self.page_id]
        self.assertEqual(action_overview, {"interaction": "modified", "layout": "unchanged"})

    def test_model_operations_are_bound_to_full_request_by_requirement_planner(self):
        """设计阶段模型只返回操作，平台而不是模型复制用户完整原文。"""

        from app.agents.main.requirement_revision import plan_requirement_changeset
        from app.domain.technical_plan_changeset import RevisionOperationBatch

        document = requirement_revision_document(self.spec, self.product)
        request = "我想在人员实体添加姓名和岗位的字段，列表中显示姓名、岗位信息，另外新增弹框也需要填写姓名和岗位信息"
        old = self.spec["pages"][0]["description"]
        response = MagicMock(response_metadata={}, tool_calls=[{
            "name": "RevisionOperationBatch",
            "args": {"schemaVersion": 1, "baseSha256": application_planning_sha256(document),
                "operations": [{"op": "replace", "path": f"/requirement_spec/pages/{self.page_id}/description",
                    "expectedBefore": old, "value": old + " 包含姓名和岗位。"}]},
        }])
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = response
        with patch("app.agents.main.requirement_revision.create_chat_model", return_value=model):
            result = plan_requirement_changeset(document, request, [])
        self.assertEqual(result.intents[0].source_text, request)
        self.assertEqual(result.intents[0].operations[0].value, old + " 包含姓名和岗位。")
        tools = model.bind_tools.call_args.args[0]
        self.assertEqual(tools, [RevisionOperationBatch, AskUserInput])

    def test_clarification_keeps_original_baseline_and_answer_is_not_confirmation(self):
        """提问不应用部分操作，回答后仍回到同一文档基线并再次等待联合确认。"""

        request = "人员邮箱调整"
        question = AskUserInput(questions=[{"header": "邮箱", "question": "邮箱如何调整？", "type": "text"}])
        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=question):
            pending = generate_requirement_revision(self.state, self.spec, request)
        self.assertEqual(pending["requirement_changeset_context"]["spec"], self.spec)
        self.assertEqual(pending["requirement_spec"]["confirmation_status"], "pending_user_input")
        resumed = {**self.state, **pending, "application_planning_interaction": {"action": "answer"}}
        document = requirement_revision_document(self.spec, self.product)
        full = request + "\n删除邮箱"
        changeset = change(document, full, [{"op": "remove", "path": f"/product_plan/pages/{self.page_id}/information_items/email", "expectedBefore": self.product["pages"][0]["information_items"][-1]}])
        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset) as model:
            result = generate_requirement_revision(resumed, pending["requirement_spec"], "删除邮箱")
        self.assertEqual(model.call_args.args[1], full)
        self.assertFalse(result["requirements_confirmed"])
        self.assertEqual(result["requirement_spec_baseline"], self.spec)
        self.assertEqual(result["requirement_changeset_context"], {})

    def test_retry_continues_frozen_request_after_interaction_is_cleared(self):
        """失败重试清空一次性交互后，仍须把冻结的新需求与澄清答案一起交给模型。"""
        original = (
            "本次迭代的新需求：实现一个前端页面，调用接口并展示字符串“React前端编程！”。"
        )
        frozen = {"spec": self.spec, "product": self.product, "request": original}
        state = {
            **self.state,
            "application_planning_interaction": {},
            "requirement_changeset_context": frozen,
        }
        retry_request = "原始需求：\n请为新应用「人员明细」完成规划。\n\n用户补充确认：\n回答：新增独立页面"
        question = AskUserInput(questions=[{"header": "继续澄清", "question": "请补充页面信息。", "type": "text"}])

        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=question) as planner:
            pending = generate_requirement_revision(state, self.spec, retry_request)

        model_request = planner.call_args.args[1]
        self.assertIn(original, model_request)
        self.assertIn("新增独立页面", model_request)
        self.assertEqual(pending["requirement_changeset_context"]["spec"], self.spec)
        self.assertEqual(pending["requirement_changeset_context"]["product"], self.product)

    def test_new_iteration_retry_preserves_pending_changeset_context(self):
        """重新识别新迭代基线时不得清空上一轮尚未完成的联合修订事务。"""
        from app.agents.main.requirement_revision import new_iteration_revision_state
        from app.domain.application_lifecycle import ApplicationLifecycleStage

        with tempfile.TemporaryDirectory() as workspace:
            confirmed = {**deepcopy(self.spec), "confirmation_status": "confirmed"}
            frozen = {"spec": self.spec, "product": self.product, "request": "新增独立页面并展示 React前端编程！"}
            state = {
                "workspace": workspace,
                "workflow_scope": "application_planning",
                "application_planning_interaction": {},
                "requirement_spec_loaded_from_disk": True,
                "requirement_changeset_context": frozen,
            }
            lifecycle = MagicMock()
            lifecycle.initialization.stage = ApplicationLifecycleStage.ANALYZING_REQUIREMENT
            with (
                patch("app.services.requirement_spec_carryover.read_requirement_spec_carryover", return_value={"spec": confirmed}),
                patch("app.services.application_lifecycle.load_application_lifecycle", return_value=lifecycle),
                patch("app.services.product_plan_carryover.read_product_plan_iteration_baseline", return_value=self.product),
            ):
                result = new_iteration_revision_state(state, confirmed, "回答：新增独立页面")

            self.assertIsNotNone(result)
            self.assertEqual(result["requirement_changeset_context"], frozen)

    def test_noop_invalid_target_and_whole_document_removal_preserve_disk(self):
        """空效果或任一无效操作失败后不得写需求或产品草稿。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = {**self.state, "workspace": workspace, "workflow_scope": "application_planning"}
            path = write_requirement_spec_draft_document(state, self.spec)
            state["requirement_spec_path"] = path
            before = Path(path).read_bytes()
            request = "删除邮箱"
            document = requirement_revision_document(self.spec, self.product)
            changeset = change(document, request, [{"op": "remove", "path": "/product_plan/pages/missing", "expectedBefore": None}])
            state["application_planning_interaction"] = {"action": "revise", "request": request}
            with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset):
                with self.assertRaisesRegex(ValueError, "原草稿已保留"):
                    requirements(state)
            self.assertEqual(Path(path).read_bytes(), before)

    def test_joint_draft_write_failure_restores_both_documents(self):
        """产品草稿写入失败时，需求草稿也必须回滚，原 Markdown 编辑不能丢失。"""

        from app.agents.main.requirement_revision import write_joint_revision_drafts
        from app.workspace.product_plan_documents import write_product_plan_documents, product_plan_draft_json_path, product_plan_draft_markdown_path
        from app.workspace.spec_documents import requirement_spec_draft_json_path, requirement_spec_draft_markdown_path
        with tempfile.TemporaryDirectory() as workspace:
            state = {**self.state, "workspace": workspace}
            write_requirement_spec_draft_document(state, self.spec)
            write_product_plan_documents(state, self.product)
            paths = [requirement_spec_draft_json_path(state), requirement_spec_draft_markdown_path(state),
                     product_plan_draft_json_path(state), product_plan_draft_markdown_path(state)]
            before = {path: path.read_bytes() for path in paths}
            revised = deepcopy(self.spec)
            revised["pages"][0]["description"] += " 新修改。"
            with patch("app.workspace.product_plan_documents.write_product_plan_documents", side_effect=OSError("写入失败")):
                with self.assertRaisesRegex(OSError, "写入失败"):
                    write_joint_revision_drafts(state, {"requirement_spec": revised,
                        "requirement_revision_product_candidate": self.product,
                        "requirement_spec_diff": requirement_revision_diff(self.spec, revised)})
            for path, data in before.items():
                self.assertEqual(path.read_bytes(), data)

    def test_markdown_edit_is_synced_before_freezing_changeset(self):
        """直接修改必须基于两份最新 Markdown，不用旧 JSON 覆盖任一文档的人工编辑。"""

        from app.workspace.product_plan_documents import product_plan_draft_markdown_path, write_product_plan_documents
        from app.workspace.spec_documents import requirement_spec_draft_markdown_path
        with tempfile.TemporaryDirectory() as workspace:
            state = {**self.state, "workspace": workspace}
            state["requirement_spec_path"] = write_requirement_spec_draft_document(state, self.spec)
            product_path, product_json_path = write_product_plan_documents(state, self.product)
            state["product_plan_path"] = product_path
            state["product_plan_json_path"] = product_json_path
            requirement_path = requirement_spec_draft_markdown_path(state)
            requirement_path.write_text(requirement_path.read_text() + "\n人工补充需求备注。")
            product_path = product_plan_draft_markdown_path(state)
            product_path.write_text(product_path.read_text() + "\n人工补充产品备注。")
            synced = deepcopy(self.spec)
            synced["pages"][0]["description"] += " 人工补充需求备注。"
            synced_product = deepcopy(self.product)
            synced_product["pages"][0]["description"] += " 人工补充产品备注。"
            document = requirement_revision_document(synced, synced_product)
            request = "删除邮箱"
            changeset = change(document, request, [{"op": "remove",
                "path": f"/product_plan/pages/{self.page_id}/information_items/email",
                "expectedBefore": self.product["pages"][0]["information_items"][-1]}])
            with (
                patch("app.agents.main.document_sync.sync_requirement_spec_from_markdown", return_value=synced) as sync_spec,
                patch("app.agents.main.document_sync.sync_product_plan_from_markdown", return_value=synced_product) as sync_product,
                patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset),
            ):
                result = generate_requirement_revision(state, self.spec, request)
            sync_spec.assert_called_once()
            sync_product.assert_called_once()
            self.assertIn("人工补充需求备注", sync_spec.call_args.args[1])
            self.assertIn("人工补充产品备注", sync_product.call_args.args[2])
            self.assertIn("人工补充需求备注", result["requirement_spec"]["pages"][0]["description"])
            self.assertIn("人工补充产品备注", result["requirement_revision_product_candidate"]["pages"][0]["description"])

    def test_new_iteration_input_reuses_joint_changeset_with_carryover(self):
        """新迭代首次输入直接修订两份上一轮基线，不再整份生成或旧合并。"""

        from app.domain.application_lifecycle import ApplicationLifecycleStage
        from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle
        from app.services.application_planning_generation_lifecycle import ensure_generation_running
        from app.services.product_plan_carryover import write_product_plan_carryover
        from app.services.requirement_spec_carryover import write_requirement_spec_carryover
        from app.services.stage_artifacts import load_stage_artifacts
        from app.workspace.spec_documents import (
            confirmed_requirement_spec_json_path, write_confirmed_requirement_spec_document,
        )
        with tempfile.TemporaryDirectory() as workspace:
            spec = deepcopy(self.spec)
            spec["confirmation_status"] = "confirmed"
            product = deepcopy(self.product)
            product["confirmation_status"] = "confirmed"
            state = {"workspace": workspace, "workflow_scope": "application_planning"}
            write_confirmed_requirement_spec_document(state, spec)
            formal_json = confirmed_requirement_spec_json_path(state)
            formal_before = formal_json.read_bytes()
            first_product_page = product["pages"][0]
            write_product_plan_carryover(
                workspace, source_branch="v1_0", plan=product,
                pages={self.page_id: {
                    "requirement": spec["pages"][0], "development_page": first_product_page,
                    "definition": {key: first_product_page[key] for key in (
                        "goal", "information_items", "actions", "navigation_targets",
                    ) if key in first_product_page},
                }},
            )
            write_requirement_spec_carryover(workspace, source_branch="v1_0", spec=spec)
            write_application_lifecycle(
                workspace,
                create_application_lifecycle(application_id="iteration-changeset", application_name="人员管理"),
            )
            ensure_generation_running(
                workspace, stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                active_run_id="new-iteration-run",
                predecessor_stages={ApplicationLifecycleStage.COLLECTING_REQUIREMENT},
            )
            request = "人员页面补充政治面貌说明"
            old_spec_description = spec["pages"][0]["description"]
            old_product_description = product["pages"][0]["description"]
            new_description = old_spec_description + " 展示政治面貌。"
            document = requirement_revision_document(spec, product)
            changeset = change(document, request, [
                {"op": "replace", "path": f"/requirement_spec/pages/{self.page_id}/description",
                    "expectedBefore": old_spec_description, "value": new_description},
                {"op": "replace", "path": f"/product_plan/pages/{self.page_id}/description",
                    "expectedBefore": old_product_description, "value": new_description},
            ])
            state.update({"request": request, "application_planning_interaction": {}, "timeline": []})
            state = {**state, **load_stage_artifacts(state, "requirements")}
            self.assertTrue(state["requirement_spec_loaded_from_disk"])
            self.assertEqual(state["requirement_spec"], spec)
            self.assertEqual(state["product_plan"], {})
            with (
                patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset) as planner,
                patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                    side_effect=AssertionError("新迭代不能整份重生成需求")) as analyzer,
                patch("app.graph.nodes.requirements.merge_requirement_spec",
                    side_effect=AssertionError("新迭代不能进入旧页面合并器")) as merger,
            ):
                result = requirements(state)
            planner.assert_called_once()
            analyzer.assert_not_called()
            merger.assert_not_called()
            self.assertEqual(result["requirement_spec_baseline"], spec)
            self.assertEqual(result["requirement_spec"]["pages"][0]["description"], new_description)
            self.assertEqual(result["requirement_revision_product_candidate"]["pages"][0]["description"], new_description)
            self.assertEqual(
                [{key: value for key, value in page.items() if key != "_change_status"}
                    for page in result["requirement_revision_product_candidate"]["pages"][1:]],
                product["pages"][1:],
            )
            self.assertEqual(formal_json.read_bytes(), formal_before)
            self.assertEqual(
                json.loads(Path(result["requirement_spec_json_path"]).read_text())["pages"][0]["description"],
                new_description,
            )
            self.assertEqual(
                json.loads(Path(result["product_plan_json_path"]).read_text())["pages"][0]["description"],
                new_description,
            )
            with patch("app.graph.nodes.product_planning.plan_product_with_chat_model",
                side_effect=AssertionError("联合候选不能再次整份生成产品")) as product_planner:
                pending = product_planning({**state, **result})
            product_planner.assert_not_called()
            self.assertEqual(pending["clarification"]["mode"], "requirement_document_confirmation")
            self.assertEqual(pending["product_plan"]["pages"][0]["_change_status"], "modified")
            self.assertEqual(pending["requirement_revision_product_candidate"], {})

    def test_deleted_page_keeps_product_tombstone_for_review_and_development(self):
        """删除页面从当前清单移除后仍保留完整产品墓碑，供展示与代码清理。"""
        removed_spec = self.spec["pages"][-1]
        removed_product = self.product["pages"][-1]
        page_id = removed_spec["pageId"]
        document = requirement_revision_document(self.spec, self.product)
        request = f"删除页面 {page_id}"
        changeset = change(document, request, [
            {"op": "remove", "path": f"/requirement_spec/pages/{page_id}", "expectedBefore": removed_spec},
            {"op": "remove", "path": f"/product_plan/pages/{page_id}", "expectedBefore": removed_product},
        ])
        with patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset):
            result = generate_requirement_revision(self.state, self.spec, request)

        diff_page = next(page for page in result["requirement_spec_diff"]["pages"] if page["pageId"] == page_id)
        self.assertEqual(diff_page["_change_status"], "deleted")
        product_page = next(
            page for page in result["requirement_revision_product_candidate"]["pages"]
            if page["pageId"] == page_id
        )
        self.assertEqual(product_page["_change_status"], "deleted")
        self.assertEqual(product_page["name"], removed_product["name"])
        self.assertEqual(product_page["actions"], removed_product["actions"])
        self.assertEqual(result["requirement_spec_diff"]["_page_design_overview"][page_id], {
            "interaction": "deleted", "layout": "deleted",
        })
        from app.agents.main.requirement_revision import write_joint_revision_drafts
        from app.workspace.spec_documents import requirement_spec_draft_json_path
        from app.workspace.product_plan_documents import product_plan_draft_json_path

        with tempfile.TemporaryDirectory() as workspace:
            revision_state = {**self.state, "workspace": workspace}
            written = write_joint_revision_drafts(revision_state, result)
            spec_disk = json.loads(requirement_spec_draft_json_path(revision_state).read_text())
            product_disk = json.loads(product_plan_draft_json_path(revision_state).read_text())
            self.assertIn(page_id, [page["pageId"] for page in spec_disk["pages"]])
            self.assertIn(page_id, [page["pageId"] for page in product_disk["pages"]])
            review = product_planning({**revision_state, **written})
        self.assertIn(page_id, [page["pageId"] for page in review["product_plan"]["pages"]])

    def test_diff_uses_absence_not_model_deleted_pages_declaration(self):
        """删除标记只来自真实前后内容，旧模型 deleted_pages 声明不再参与修订。"""

        candidate = deepcopy(self.spec)
        removed = candidate["pages"].pop()
        display = requirement_revision_diff(self.spec, candidate)
        self.assertEqual(candidate["pages"], self.spec["pages"][:-1])
        self.assertEqual(display["pages"][-1]["pageId"], removed["pageId"])
        self.assertEqual(display["pages"][-1]["_change_status"], "deleted")


class RequirementChangeSetGraphTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 SQLite、原生审阅门与生产节点验证修改后仍需联合确认。"""

    async def test_card_revision_uses_changeset_without_classifier_or_full_generation(self):
        """真实卡片 revise 直达通用操作，两份 Markdown 草稿更新后等待新的确认门。"""

        with tempfile.TemporaryDirectory() as workspace:
            spec = create_requirement_spec("人员管理应用")
            spec["confirmation_status"] = "pending_user_confirmation"
            plan = create_product_plan(spec)
            page_id = spec["pages"][0]["pageId"]
            initial = {"workspace": workspace, "workflow_scope": "application_planning", "request": "人员管理应用",
                       "requirement_spec": spec, "product_plan": plan}
            application = Path(workspace) / ".devagentstudio/application.json"
            application.parent.mkdir(parents=True)
            application.write_text(json.dumps({"schemaVersion": 6, "configRevision": 1, "auth": {"enable": False},
                "authorization": {"enabled": False, "initialAdministratorSubjects": []}}))
            write_requirement_spec_draft_document(initial, spec)
            document = requirement_revision_document(spec, plan)
            request = "页面说明增加政治面貌"
            old = spec["pages"][0]["description"]
            new = old + " 展示政治面貌。"
            changeset = change(document, request, [
                {"op": "replace", "path": f"/requirement_spec/pages/{page_id}/description", "expectedBefore": old, "value": new},
                {"op": "replace", "path": f"/product_plan/pages/{page_id}/description", "expectedBefore": old, "value": new},
            ])
            builder = StateGraph(ProjectState)
            builder.add_node("requirements", requirements)
            builder.add_node("product_planning", product_planning)
            builder.add_node("review", requirement_document_review)
            builder.add_node("requirements_review", requirements_review)
            def unexpected_classification(state):
                """卡片修改不得回到旧分类分支；只注册目标满足原生 Command 图校验。"""

                raise AssertionError("需求卡直接修改不能先做意图分类")
            builder.add_node("design_intent_analysis", unexpected_classification)
            builder.add_edge(START, "product_planning")
            builder.add_edge("requirements", "product_planning")
            builder.add_conditional_edges("product_planning", lambda state: END if state.get("status") == "completed" else "review")
            async with AsyncSqliteSaver.from_conn_string(str(Path(workspace) / "graph.sqlite")) as saver:
                graph = builder.compile(checkpointer=saver)
                config = {"configurable": {"thread_id": "requirement-changeset"}}
                await graph.ainvoke(initial, config)
                before = await graph.aget_state(config)
                with (
                    patch("app.agents.main.requirement_revision.plan_requirement_changeset", return_value=changeset) as model,
                    patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model", side_effect=AssertionError("直接修改不能整份重生成")),
                    patch("app.graph.nodes.product_planning.plan_product_with_chat_model", side_effect=AssertionError("联合修改不能再整份重生成产品")),
                ):
                    await graph.ainvoke(submission(before, "revise", request=request), config)
                model.assert_called_once()
                after = await graph.aget_state(config)
                self.assertEqual(after.next, ("review",))
                self.assertEqual(after.values["product_plan"]["pages"][0]["description"], new)
                self.assertEqual(after.values["requirement_spec"]["pages"][0]["description"], new)
                self.assertFalse(after.values["requirements_confirmed"])
                self.assertIn("政治面貌", Path(after.values["requirement_spec_path"]).read_text())
                await graph.ainvoke(submission(after, "confirm"), config)
                confirmed = await graph.aget_state(config)
                self.assertFalse(confirmed.next)
                self.assertTrue(confirmed.values["requirements_confirmed"])
                self.assertEqual(confirmed.values["product_plan"]["confirmation_status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
