"""覆盖各单测检查独立预算、实际派发计费与阶段确认的一次性语义。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.nodes.lifecycle import test_phase_confirmation
from app.graph.nodes.small_task import unit_test_repair
from app.graph.subgraphs.unit_testing import unit_repair_planning, unit_test
from app.graph.workflow import route_test_phase_confirmation, route_unit_test_result
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.projection import _workflow_summary
from app.services.unit_test_repair_budget import charge_unit_test_repair_batch


def repair_task(check_id: str, task_id: str = "repair") -> dict:
    """构造绑定具体失败检查的真实源码范围修复任务。"""
    return {
        "id": task_id, "owner": "backend", "status": "pending",
        "description": "修复单元测试", "dependencies": [],
        "allowed_paths": ["backend/src/test/java/ExampleTest.java"],
        "target_files": ["backend/src/test/java/ExampleTest.java"],
        "source_ref": {"failed_check_id": check_id},
    }


class UnitTestRepairBudgetTests(unittest.TestCase):
    """每个子步骤允许十轮，其他步骤及确认续接不消耗其预算。"""

    def test_each_check_has_ten_attempts_independent_of_other_checks(self) -> None:
        """四种检查依次耗尽，任意前置检查均不会占用下一个检查的额度。"""
        state = {}
        for check in ("frontend_test_generation", "backend_test_generation", "frontend_unit_tests", "backend_unit_tests"):
            for attempt in range(1, 11):
                state["unit_test_repair_charged_checks"] = []
                self.assertIsNone(charge_unit_test_repair_batch(state, [repair_task(check)]))
                self.assertEqual(state["unit_test_repair_attempts"][check], attempt)
            state["unit_test_repair_charged_checks"] = []
            self.assertIn("10 次", charge_unit_test_repair_batch(state, [repair_task(check)]))
        self.assertEqual(sum(state["unit_test_repair_attempts"].values()), 40)

    def test_same_check_multiple_tasks_and_confirmation_resume_count_once(self) -> None:
        """同一轮计划的同目标任务和范围确认恢复不会重复扣费。"""
        state = {}
        tasks = [repair_task("backend_unit_tests", "a"), repair_task("backend_unit_tests", "b")]
        self.assertIsNone(charge_unit_test_repair_batch(state, tasks))
        self.assertIsNone(charge_unit_test_repair_batch(state, tasks))
        self.assertEqual(state["unit_test_repair_attempts"], {"backend_unit_tests": 1})

    def test_budget_exhaustion_does_not_partially_charge_a_batch(self) -> None:
        """批次含已耗尽目标时整批不派发，也不扣其他目标额度。"""
        state = {"unit_test_repair_attempts": {"backend_test_generation": 10}}
        self.assertIsNotNone(charge_unit_test_repair_batch(state, [
            repair_task("backend_test_generation"), repair_task("backend_unit_tests"),
        ]))
        self.assertEqual(state["unit_test_repair_attempts"], {"backend_test_generation": 10})

    def test_planner_uses_current_check_budget_and_stops_after_ten(self) -> None:
        """执行过十轮前端修复后，后端仍可修复；当前检查第十轮失败才终止。"""
        for count in (0, 4, 9, 10):
            with self.subTest(count=count):
                state = {
                    "test_results": [{"id": "backend_unit_tests", "name": "后端单元测试", "passed": False}],
                    "unit_test_repair_attempts": {"frontend_unit_tests": 10, "backend_unit_tests": count},
                    "unit_test_repair_iteration": 10,
                }
                plan = {"status": "ready", "decision": "repair", "tasks": [repair_task("backend_unit_tests")]}
                with patch("app.graph.subgraphs.testing.capture_agent_file_changes", return_value=SimpleNamespace(
                    value=plan, code_change_set=None,
                )) as planner, patch("app.graph.subgraphs.testing.write_repair_task_plan_json", return_value="plan.json"):
                    result = unit_repair_planning(state)
                self.assertEqual(result["unit_test_next_action"], "handle_failure" if count == 10 else "unit_test_repair")
                self.assertEqual(planner.call_count, 0 if count == 10 else 1)
                self.assertEqual(result["unit_test_max_repair_iterations"], 10)

    def test_dispatched_failure_and_success_both_preserve_attempts(self) -> None:
        """计费发生在派发时，Agent 返回失败也不能把额度回退。"""
        for status in ("completed", "failed", "requires_user_confirmation"):
            with self.subTest(status=status), patch("app.graph.nodes.small_task.execute_small_task_batch", return_value={
                "results": [{"taskId": "repair", "status": status, "summary": "结果", "escalation": {"requestedPaths": ["backend/src/test/java/OtherTest.java"]}}],
                "codeChangeSets": [],
            }):
                result = unit_test_repair({"repair_tasks": [repair_task("backend_unit_tests")]})
                self.assertEqual(result["unit_test_repair_attempts"], {"backend_unit_tests": 1})
                self.assertEqual(result["unit_test_repair_charged_checks"], ["backend_unit_tests"])

    def test_four_used_attempts_can_continue_without_resetting_count(self) -> None:
        """已用四次的当前会话继续第五次，旧上限快照不能覆盖服务端十次上限。"""
        with patch("app.graph.nodes.small_task.execute_small_task_batch", return_value={
            "results": [{"taskId": "repair", "status": "completed", "summary": "已修复"}],
            "codeChangeSets": [],
        }) as execute:
            result = unit_test_repair({
                "repair_tasks": [repair_task("backend_unit_tests")],
                "unit_test_repair_attempts": {"backend_unit_tests": 4},
                "unit_test_max_repair_iterations": 4,
            })
        execute.assert_called_once()
        self.assertEqual(result["unit_test_repair_attempts"], {"backend_unit_tests": 5})
        self.assertEqual(result["unit_test_max_repair_iterations"], 10)

    def test_eleventh_attempt_is_blocked_before_agent_dispatch(self) -> None:
        """直接恢复修复节点也不能绕过十次上限。"""
        with patch("app.graph.nodes.small_task.execute_small_task_batch") as execute:
            result = unit_test_repair({
                "repair_tasks": [repair_task("backend_unit_tests")],
                "unit_test_repair_attempts": {"backend_unit_tests": 10},
            })
        execute.assert_not_called()
        self.assertEqual(result["status"], "failed")

    def test_projection_preserves_per_check_counts(self) -> None:
        """AG-UI 快照携带独立计数，前端不能将其写回覆盖服务端事实。"""
        state = {"unit_test_repair_attempts": {"backend_unit_tests": 2}, "unit_test_max_repair_iterations": 10}
        summary = _workflow_summary(state, [])
        self.assertEqual(summary["unitTestRepairAttempts"], state["unit_test_repair_attempts"])
        inputs = workflow_run_inputs({"resumeState": {"state": {"unitTestRepairAttempts": {"backend_unit_tests": 0}}}})
        self.assertNotIn("unit_test_repair_attempts", inputs["resume_values"])

    def test_exhausted_debug_rerun_replaces_stale_agent_error_with_current_reason(self) -> None:
        """调试重跑保持额度，但节点与失败处理必须展示本次真实终止原因。"""
        from app.graph.nodes.lifecycle import handle_failure

        state = {
            "message": "Agent 未提供执行摘要.", "error": "旧错误",
            "unit_test_repair_attempts": {"frontend_unit_tests": 10},
            "test_results": [{"id": "frontend_unit_tests", "name": "前端单元测试", "passed": False}],
        }
        with patch("app.graph.subgraphs.testing.write_repair_task_plan_json", return_value="plan.json"):
            planned = unit_repair_planning(state)
        with patch("app.graph.subgraphs.unit_testing._unit_testing_subgraph") as graph:
            graph.invoke.return_value = {**state, **planned, "unit_test_quality_gate_passed": False}
            result = unit_test(state)
        self.assertEqual(route_unit_test_result(result), "handle_failure")
        self.assertIn("10 次修复额度", result["message"])
        self.assertIn("前端单元测试", result["message"])
        self.assertEqual(result["error"], result["message"])
        self.assertEqual(handle_failure({**state, **result})["message"], result["message"])
        self.assertNotIn("未提供执行摘要", result["message"])


class UnitTestConfirmationResetTests(unittest.TestCase):
    """已经走过测试阶段的线程，从单测调试重入时必须重新确认。"""

    def test_debug_and_unit_test_answer_discard_old_confirm(self) -> None:
        """UI 快照和 checkpoint 里的旧确认均由本次空提交显式覆盖。"""
        for payload in (
            {"workflowDebug": {"enabled": True, "resumeFrom": "unit_test"}},
            {"workflowDebug": {"enabled": True, "resumeFrom": "test_phase_confirmation"}},
            {"clarificationAnswers": {"unit_test_confirmation": {"selected": "run"}}},
        ):
            inputs = workflow_run_inputs({**payload, "resumeState": {"state": {
                "testPhaseConfirmation": {"action": "confirm"},
            }}})
            self.assertEqual(inputs["resume_values"]["test_phase_confirmation"], {})

    def test_unit_test_completion_clears_confirm_and_requires_new_confirmation(self) -> None:
        """模拟节点调试后的真实单测返回，确认节点只能停留，用户提交后才放行。"""
        state = {
            "build_summary": {"status": "completed", "completed": 1},
            "test_phase_confirmation": {"action": "confirm"},
            "unit_test_decision": "run",
        }
        with patch("app.graph.subgraphs.unit_testing._unit_testing_subgraph") as graph:
            graph.invoke.return_value = {"unit_test_quality_gate_passed": True, "unit_test_next_action": "test_phase_confirmation"}
            update = unit_test(state)
        self.assertEqual(route_unit_test_result(update), "test_phase_confirmation")
        self.assertEqual(update["test_phase_confirmation"], {})
        gate = SimpleNamespace(allowed=True, model_dump=lambda **kwargs: {"allowed": True})
        with patch("app.graph.nodes.lifecycle.complete_initial_development"), patch("app.graph.nodes.lifecycle.test_entry_gate", return_value=gate):
            waiting = test_phase_confirmation({**state, **update})
            self.assertEqual(route_test_phase_confirmation(waiting), "await_user_input")
            self.assertEqual(waiting["clarification"]["mode"], "test_phase_confirmation")
            submitted = workflow_run_inputs({"clarificationAnswers": {"test_phase_confirmation": {"action": "confirm"}}})
            completed = test_phase_confirmation({**state, **update, **submitted["resume_values"]})
        self.assertEqual(route_test_phase_confirmation({**state, **update, **completed}), "integration_test")
        self.assertEqual(completed["test_phase_confirmation"], {})


if __name__ == "__main__":
    unittest.main()
