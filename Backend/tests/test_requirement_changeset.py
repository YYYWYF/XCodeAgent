"""需求待确认卡复用 ChangeSet 的原子修订、澄清和真实联合确认回归。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
