"""验证集成子图选择的实际规划输入及授权边界。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from app.graph.subgraphs.testing import integration_repair_planning
from app.agents.repair_planner.integration import IntegrationRepairDecision
from app.services.run_workers import bind_run_workers
from app.services.test_validation import evaluate_quality_gate
from app.services.workspace_process_registry import workspace_process_registry


def _decision(decision="repair", escalation=None) -> AIMessage:
    """用真实工具参数消息模拟模型提交决策。"""
    return AIMessage(content="", tool_calls=[{
        "id": "final", "name": IntegrationRepairDecision.__name__, "args": {
            "decision": decision, "escalationKind": escalation, "strategy": "修复类型错误",
            "reason": "编译诊断已定位", "failure_handling": "保留报告",
        },
    }])


def _failed_state(workspace: str) -> dict:
    """准备有真实编译位置和本轮日志引用的失败。"""
    root = Path(workspace)
    source = root / "frontend/src/a.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const a = 'SOURCE_WINDOW'\n", encoding="utf-8")
    log = root / ".devagentstudio/runtime/tests/frontend_build/stderr.log"
    log.parent.mkdir(parents=True)
    log.write_text("CURRENT_LOG\n", encoding="utf-8")
    error = "src/a.ts(1,1): error TS2322: invalid type"
    report = evaluate_quality_gate(test_results=[{
        "id": "frontend_build", "name": "构建", "passed": False, "evidence": error,
        "execution": {"cwd": "frontend", "stdout_tail": error, "stderr_tail": error,
                      "stderr_log": str(log), "returncode": 1},
    }])
    return {
        "workspace": workspace, "test_report": report,
        "test_results": report["checks"], "revision_requests": report["revision_requests"],
    }


class IntegrationPlanningInputTests(unittest.TestCase):
    def test_integration_entry_omits_build_plan(self):
        """非空、空和缺失计划均不进入模型，任务仍由平台授权。"""
        with tempfile.TemporaryDirectory() as workspace:
            base = _failed_state(workspace)
            for plan in ({"tasks": ["ONLY_IN_BUILD_PLAN"]}, {}, None):
                state = dict(base)
                if plan is not None:
                    state["build_task_plan"] = plan
                model = Mock()
                model.bind_tools.return_value.invoke.return_value = _decision()
                with patch("app.agents.repair_planner.integration.create_chat_model", return_value=model):
                    result = integration_repair_planning(state)
                model.bind_tools.assert_called_once_with([IntegrationRepairDecision])
                prompt = model.bind_tools.return_value.invoke.call_args.args[0][1].content
                self.assertNotIn("CurrentBuildTaskPlan", prompt)
                self.assertNotIn("ONLY_IN_BUILD_PLAN", prompt)
                self.assertEqual(prompt.count("TS2322"), 1)
                self.assertNotIn("SOURCE_WINDOW", prompt)
                self.assertNotIn("CURRENT_LOG", prompt)
                self.assertEqual(json.loads(prompt)["locations"][0]["path"], "frontend/src/a.ts")
                task, = result["repair_tasks"]
                self.assertEqual(task["owner"], "frontend")
                self.assertIn("frontend", task["allowed_paths"])

    def test_only_one_supplement_then_no_dispatch_on_invalid_result(self):
        """补充证据仅使用原始诊断，模型余额用完后禁止生成可执行任务。"""
        with tempfile.TemporaryDirectory() as workspace:
            state = _failed_state(workspace)
            model = Mock()
            invoke = model.bind_tools.return_value.invoke
            invoke.side_effect = [_decision("needs_evidence"), AIMessage(content="invalid")]
            with patch("app.agents.repair_planner.integration.create_chat_model", return_value=model):
                result = integration_repair_planning(state)
            self.assertEqual(invoke.call_count, 2)
            prompt = invoke.call_args.args[0][1].content
            self.assertLessEqual(len(prompt), 12_000)
            self.assertIn("SOURCE_WINDOW", prompt)
            self.assertIn("CURRENT_LOG", prompt)
            self.assertEqual(result["integration_next_action"], "handle_failure")
            self.assertEqual(result["repair_tasks"], [])
            self.assertEqual(result["repair_task_plan"]["candidateTasks"], [])

    def test_terminal_and_formal_decisions_block_code_dispatch(self):
        """即使失败有已授权路径，终止和正式修订也不会自动派发。"""
        with tempfile.TemporaryDirectory() as workspace:
            state = _failed_state(workspace)
            model = Mock()
            invoke = model.bind_tools.return_value.invoke
            invoke.side_effect = [_decision("terminal_failure"), _decision("requires_user_confirmation", "formal_revision")]
            with patch("app.agents.repair_planner.integration.create_chat_model", return_value=model), patch(
                "app.graph.subgraphs.testing.build_small_task_revision_confirmation",
                return_value={"clarification": {"mode": "revision_impact_confirmation"}},
            ) as formal:
                terminal = integration_repair_planning(state)
                waiting = integration_repair_planning(state)
            formal.assert_called_once()
            self.assertEqual(terminal["integration_next_action"], "handle_failure")
            self.assertEqual(waiting["integration_next_action"], "await_user_input")
            self.assertEqual(waiting["clarification"]["mode"], "revision_impact_confirmation")
            self.assertEqual(terminal["repair_tasks"], [])
            self.assertEqual(waiting["repair_tasks"], [])

    def test_cancelled_model_never_retries_or_writes_plan(self):
        """首轮模型完成前收到停止时，不读取补充证据或持久化计划。"""
        run_id = "cancel-integration-planning"
        with tempfile.TemporaryDirectory() as workspace:
            state = _failed_state(workspace)
            model = Mock()
            invoke = model.bind_tools.return_value.invoke

            def cancel(_messages):
                """在模型返回前标记本轮取消。"""
                workspace_process_registry.cancel_run(run_id)
                return _decision("needs_evidence")

            invoke.side_effect = cancel
            try:
                with bind_run_workers(run_id, workspace), patch(
                    "app.agents.repair_planner.integration.create_chat_model", return_value=model,
                ), patch("app.graph.subgraphs.testing.write_repair_task_plan_json") as write_plan:
                    with self.assertRaisesRegex(RuntimeError, "当前运行已停止"):
                        integration_repair_planning(state)
                    write_plan.assert_not_called()
                invoke.assert_called_once()
            finally:
                workspace_process_registry.allow_run(run_id)
