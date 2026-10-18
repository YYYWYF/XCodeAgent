"""验证续接的 checkpoint、凭据消费和开发执行登记不会形成交接空洞。"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

from app.domain.application_revision import ActiveFormalRevision, RevisionTarget
from app.graph.state import ProjectState
from app.graph.workflow import route_workflow_start
from app.services.application_lifecycle import (
    create_application_lifecycle, load_application_lifecycle, start_workbench_execution,
    write_application_lifecycle,
)
from app.services.application_revision_lifecycle import issue_revision_continuation, validate_revision_continuation
from app.services.revision_continuation_handoff import persist_revision_build_entry
from app.services.workflow_reentry import workflow_entry


def _development_entry(state):
    """隔离测试只返回确认，不调用 Agent 或外部服务。"""
    return {"status": "requires_user_input"}


class RevisionContinuationHandoffTests(unittest.IsolatedAsyncioTestCase):
    """用真实 LangGraph checkpoint 与真实生命周期原子写验证平台交接。"""

    def setUp(self) -> None:
        """准备已确认技术规划和一个尚未消费的正常修订续接。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = directory.name
        self.path = Path(self.workspace) / ".devagentstudio/plans/technical-plan.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"artifact_type": "technical-plan", "confirmation_status": "confirmed"}))
        state = create_application_lifecycle(application_id="app", application_name="交接测试", initialization_thread_id="planning")
        active = ActiveFormalRevision(changeId="change", formalBranch="workbench_plan_revision",
            sourceThreadId="source", sourceRunId="source-run", request="二次修改：返回计划后继续开发",
            target=RevisionTarget(type="application"), impactInteractionId="impact",
            planningThreadId="planning", status="drafting")
        write_application_lifecycle(self.workspace, state.model_copy(update={"active_formal_revision": active}))
        self.token, _ = issue_revision_continuation(self.workspace, change_id="change", technical_plan_path=self.path)
        self.identity = {"change_id": "change", "token": self.token}

    def _claim(self):
        """通过生产服务原子消费凭据并登记开发执行。"""
        return start_workbench_execution(self.workspace, scope="application", target_id="application",
            page_id=None, thread_id="development", run_id="development-run", phase="api_design_readiness_gate",
            owner_session_id="owner", revision_continuation_consume=self.identity)

    async def test_validation_does_not_consume_and_failed_registration_can_retry(self) -> None:
        """入场前检查或持久化失败后，原凭据仍可再次提交。"""
        before = load_application_lifecycle(self.workspace)
        validate_revision_continuation(self.workspace, **self.identity, technical_plan_path=self.path)
        self.assertEqual(load_application_lifecycle(self.workspace), before)
        with patch("app.services.application_lifecycle._persist_workbench_execution_snapshot", side_effect=OSError("planned write failure")):
            with self.assertRaises(OSError):
                self._claim()
        self.assertEqual(load_application_lifecycle(self.workspace), before)
        result = self._claim()
        self.assertIn("development-run", result.active_executions)
        self.assertEqual(result.active_formal_revision.status, "building")

    async def test_consumption_execution_and_locks_share_one_atomic_write(self) -> None:
        """不会先持久化 building 再单独登记开发执行。"""
        with patch("app.services.application_lifecycle.write_application_lifecycle", wraps=write_application_lifecycle) as writer:
            result = self._claim()
        self.assertEqual(writer.call_count, 1)
        self.assertIsNone(result.active_formal_revision.continuation_token_sha256)
        self.assertIsNotNone(result.active_formal_revision.continuation_consumed_at)
        self.assertEqual(result.active_run_id, "development-run")
        self.assertEqual(result.resource_locks.application.run_id, "development-run")

    async def test_interruption_after_claim_has_original_request_and_real_node(self) -> None:
        """交接后尚未调用任何业务节点时，checkpoint 已能定位开发入口及原提问。"""
        builder = StateGraph(ProjectState)
        builder.add_node("workflow_entry", workflow_entry)
        builder.add_node("api_design_readiness_gate", _development_entry)
        builder.add_edge("__start__", "workflow_entry")
        builder.add_conditional_edges("workflow_entry", route_workflow_start,
            {"api_design_readiness_gate": "api_design_readiness_gate"})
        builder.add_edge("api_design_readiness_gate", "__end__")
        graph = builder.compile(checkpointer=InMemorySaver())
        # 原开发会话曾承载自由修改时，也不能把 conversation Scope 带进主流程恢复。
        await graph.aupdate_state({"configurable": {"thread_id": "development"}},
                                  {"workflow_scope": "conversation"}, as_node="workflow_entry")
        inputs = {"workspace": self.workspace, "resume_from": "api_design_readiness_gate"}
        state = {"active_run_id": "development-run", "active_thread_id": "development",
                 "owner_session_id": "owner", "request": "二次修改：返回计划后继续开发"}
        await persist_revision_build_entry(graph, inputs=inputs, state=state,
                                          thread_id="development", run_id="development-run")
        self._claim()
        snapshot = await graph.aget_state({"configurable": {"thread_id": "development"}})
        self.assertEqual(snapshot.next, ("api_design_readiness_gate",))
        self.assertEqual(snapshot.values["request"], state["request"])
        self.assertEqual(snapshot.values["active_run_id"], "development-run")
        self.assertEqual(snapshot.values["workflow_scope"], "")
        self.assertNotIn(self.token, str(snapshot.values))
