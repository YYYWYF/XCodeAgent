"""需求联合确认中特殊业务问题的真实原生中断往返回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph.state import ProjectState
from app.graph.application_planning_interrupts import requirement_document_review
from app.graph.nodes.product_planning import (
    _configuration_alignment_update,
    product_planning,
)
from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
from app.protocols.workflow.runtime import _validate_application_planning_resume
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec


def submission(snapshot, action, **fields):
    """从服务端真实 interrupt 构造当前版本动作，并经过生产 Runtime 预校验。"""
    gate = application_planning_interrupt_from_snapshot(snapshot)
    value = {key: gate[key] for key in ("gateId", "artifact", "artifactRevision")}
    value.update(action=action, **fields)
    _validate_application_planning_resume(snapshot, value)
    return Command(resume=value)


def build_graph(saver, product_node=product_planning):
    """挂载原产品节点和联合审阅门，正式确认完成后才结束图。"""
    def route(state):
        """业务问题或新草稿都继续等待联合确认。"""
        return END if state.get("status") == "completed" else "review"

    def design_intent(state):
        """记录已进入原修订入口，避免测试调用真实模型分类器。"""
        return {"phase": "design_intent_analysis", "status": "completed"}

    builder = StateGraph(ProjectState)
    builder.add_node("product_planning", product_node)
    builder.add_node("review", requirement_document_review)
    builder.add_node("design_intent_analysis", design_intent)
    builder.add_edge(START, "product_planning")
    builder.add_conditional_edges("product_planning", route)
    builder.add_edge("design_intent_analysis", END)
    return builder.compile(checkpointer=saver)


class RequirementDocumentSpecialInteractionTests(unittest.IsolatedAsyncioTestCase):
    """确认特殊问题可提交、不会提前确认产物，并保留配置事实源。"""

    async def test_operation_answer_resumes_original_product_node_and_waits_for_confirmation(self):
        """受限操作缺失时真实生成问题，选页面后补齐 action 并停在新的联合确认门。"""
        spec = create_requirement_spec("人员管理", agent_spec={
            "pages": [{"pageId": "people", "name": "人员列表", "path": "/people",
                "module_id": "people", "description": "管理人员信息。"}],
            "authorization_requirements": {"enabled": True, "restrictedPages": [],
                "restrictedOperations": [{"name": "停用人员", "description": "停用选定人员。",
                    "rationale": "影响人员使用状态。", "sourceRefs": ["业务描述"]}], "dataRules": []},
        })
        plan = create_product_plan(spec)
        with tempfile.TemporaryDirectory() as workspace:
            async with AsyncSqliteSaver.from_conn_string(str(Path(workspace) / "graph.sqlite")) as saver:
                graph = build_graph(saver)
                config = {"configurable": {"thread_id": "operation-answer"}}
                with patch("app.graph.nodes.product_planning.plan_product_with_chat_model", return_value=plan) as model:
                    await graph.ainvoke({"workspace": workspace, "request": "人员管理",
                        "workflow_scope": "application_planning", "requirement_spec": spec}, config)
                    snapshot = await graph.aget_state(config)
                    gate = application_planning_interrupt_from_snapshot(snapshot)
                    question = gate["clarification"]["questions"][0]
                    self.assertEqual(gate["clarification"]["mode"], "authorization_operation_action_resolution")
                    self.assertEqual(model.call_count, 3)
                    await graph.ainvoke(submission(snapshot, "answer",
                        answers={question["id"]: {"selected": ["page:people"]}}), config)
                    model.assert_called()
                    self.assertEqual(model.call_count, 3)
                current = await graph.aget_state(config)
                self.assertEqual(application_planning_interrupt_from_snapshot(current)["clarification"]["mode"],
                    "requirement_document_confirmation")
                self.assertEqual(current.values["product_plan"]["pages"][0]["actions"][0]["name"], "停用人员")
                self.assertEqual(current.values["product_plan"]["confirmation_status"], "pending_user_confirmation")
                self.assertEqual(current.values["requirement_spec"]["confirmation_status"], "pending_user_confirmation")
                self.assertFalse(current.values.get("requirements_confirmed", False))
                with self.assertRaisesRegex(ValueError, "不允许 action=answer"):
                    submission(current, "answer", answers={"answer": "继续"})
                with self.assertRaisesRegex(ValueError, "过期"):
                    _validate_application_planning_resume(current, {"gateId": gate["gateId"],
                        "artifact": gate["artifact"], "artifactRevision": gate["artifactRevision"],
                        "action": "answer", "answers": {question["id"]: "page:people"}})

    async def test_alignment_can_reconfirm_and_revise_without_bypassing_configuration(self):
        """真实配置缺管理员时能再次提交并显示原因，也能沿原修订入口处理问题。"""
        with tempfile.TemporaryDirectory() as workspace:
            application = Path(workspace) / ".devagentstudio/application.json"
            application.parent.mkdir(parents=True)
            config_text = json.dumps({"schemaVersion": 6, "configRevision": 1,
                "auth": {"enable": True}, "authorization": {"enabled": True, "initialAdministratorSubjects": []}})
            application.write_text(config_text)
            spec = create_requirement_spec("创建一个库存管理系统")
            plan = create_product_plan(spec)
            state = {"workspace": workspace, "workflow_scope": "application_planning",
                "requirement_spec": spec, "product_plan": plan}
            initial = _configuration_alignment_update(state, spec, plan, ["缺少真实初始管理员 subjectId。"])

            def product_node(current):
                """首次只发出诊断，后续全部调用原产品确认实现。"""
                if not current.get("application_planning_interaction"):
                    return initial
                return product_planning(current)

            async with AsyncSqliteSaver.from_conn_string(str(Path(workspace) / "graph.sqlite")) as saver:
                graph = build_graph(saver, product_node)
                config = {"configurable": {"thread_id": "configuration-alignment"}}
                await graph.ainvoke(state, config)
                snapshot = await graph.aget_state(config)
                gate = application_planning_interrupt_from_snapshot(snapshot)
                self.assertEqual(gate["clarification"]["mode"], "requirement_document_confirmation")
                self.assertIn("subjectId", gate["clarification"]["errors"][0])
                await graph.ainvoke(submission(snapshot, "confirm"), config)
                current = await graph.aget_state(config)
                gate = application_planning_interrupt_from_snapshot(current)
                self.assertIsNotNone(gate)
                self.assertEqual(gate["clarification"]["mode"], "requirement_document_confirmation")
                self.assertTrue(any("subjectId" in error for error in gate["clarification"]["errors"]))
                self.assertFalse(current.values.get("requirements_confirmed", False))
                self.assertFalse((application.parent / "specs/requirement-spec.json").exists())
                await graph.ainvoke(submission(current, "revise", request="补充真实初始管理员主体并重新对齐需求"), config)
                revised = await graph.aget_state(config)
                self.assertEqual(revised.values["phase"], "design_intent_analysis")
                self.assertEqual(revised.values["request"], "补充真实初始管理员主体并重新对齐需求")
                self.assertFalse(revised.values.get("requirements_confirmed", False))
                self.assertEqual(application.read_text(), config_text)

    async def test_aligned_drafts_can_finish_joint_confirmation(self):
        """诊断后的当前草稿与配置一致时，正式确认原子提升两份文档。"""
        with tempfile.TemporaryDirectory() as workspace:
            application = Path(workspace) / ".devagentstudio/application.json"
            application.parent.mkdir(parents=True)
            application.write_text(json.dumps({"schemaVersion": 6, "configRevision": 1,
                "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []}}))
            spec = create_requirement_spec("创建一个库存管理系统")
            plan = create_product_plan(spec)
            state = {"workspace": workspace, "workflow_scope": "application_planning",
                "requirement_spec": spec, "product_plan": plan}
            initial = _configuration_alignment_update(state, spec, plan, ["请调整需求配置投影。"])

            def product_node(current):
                """模拟用户已通过正常编辑对齐草稿后的当前确认版本。"""
                return product_planning(current) if current.get("application_planning_interaction") else initial

            async with AsyncSqliteSaver.from_conn_string(str(Path(workspace) / "graph.sqlite")) as saver:
                graph = build_graph(saver, product_node)
                config = {"configurable": {"thread_id": "aligned-confirmation"}}
                await graph.ainvoke(state, config)
                snapshot = await graph.aget_state(config)
                await graph.ainvoke(submission(snapshot, "confirm"), config)
                current = await graph.aget_state(config)
                self.assertFalse(current.next)
                self.assertTrue(current.values["requirements_confirmed"])
                self.assertEqual(current.values["product_plan"]["confirmation_status"], "confirmed")
                for suffix in ["specs/requirement-spec.json", "plans/product-plan.json"]:
                    value = json.loads((application.parent / suffix).read_text())
                    self.assertEqual(value["confirmation_status"], "confirmed")
