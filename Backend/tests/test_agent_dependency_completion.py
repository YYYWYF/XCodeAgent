"""Agent 完成登记同时提交本轮真实完成的业务依赖接口。"""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from app.services.development_completion_evidence import completed_agent_endpoints, completed_page_endpoints
from app.workspace.task_documents import build_task_plan_sha256


class AgentDependencyCompletionTests(TestCase):
    """验证依赖闭包、同轮身份及任务成功条件，不写用户生命周期。"""

    def _state(self):
        """构造含两阶段业务任务的同轮 Agent 执行事实。"""
        scope = {"type": "agent", "targetId": "assistant"}
        tasks = [{"id": stage, "unit_id": "backend:endpoint:leave_api:leave.list", "status": "completed"}
                 for stage in ("objects", "service")]
        plan = {"confirmation_status": "confirmed", "build_execution_scope": scope,
                "task_registry": {task["id"]: task for task in tasks}}
        return {"active_run_id": "run", "unit_test_gate_passed": True, "build_execution_scope": scope,
                "build_task_plan": plan, "build_run_id": "build", "build_run_plan_sha256": build_task_plan_sha256(plan),
                "build_execution_slice": {"scope": scope, "task_ids": [task["id"] for task in tasks]},
                "tasks": deepcopy(tasks)}

    def test_completed_agent_dependency_is_projected(self):
        """同轮完成的业务依赖接口可以一起登记，避免只有 Agent 变绿。"""
        state = self._state()
        with patch("app.services.development_completion_evidence.resolve_execution_slice", return_value={"tasks": state["tasks"]}):
            targets = completed_agent_endpoints(state, agent_id="assistant", run_id="run")
        self.assertEqual([(target.api_contract_id, target.endpoint_id) for target in targets], [("leave_api", "leave.list")])

    def test_other_scope_and_failed_dependency_are_not_completed(self):
        """其他目标及未完成任务不能因 Agent 成功被标成完成。"""
        state = self._state()
        self.assertEqual(completed_agent_endpoints(state, agent_id="other", run_id="run"), [])
        self.assertEqual(completed_page_endpoints(state, page_id="assistant", run_id="run"), [])
        state["tasks"][1]["status"] = "failed"
        scoped = list(state["build_task_plan"]["task_registry"].values())
        with patch("app.services.development_completion_evidence.resolve_execution_slice", return_value={"tasks": scoped}):
            self.assertEqual(completed_agent_endpoints(state, agent_id="assistant", run_id="run"), [])

    def test_only_current_execution_closure_is_projected(self):
        """累计 DAG 中已完成但不在当前依赖闭包的接口不补记。"""
        state = self._state()
        with patch("app.services.development_completion_evidence.resolve_execution_slice", return_value={"tasks": []}):
            self.assertEqual(completed_agent_endpoints(state, agent_id="assistant", run_id="run"), [])
