"""回归真实日志中的空响应、工具参数误解析及单测有界恢复。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.small_task.runner import normalize_small_task_result
from app.graph.nodes.small_task import small_task_repair, unit_test_repair
from app.graph.workflow import route_small_task_result
from app.services.small_task import execute_small_task_batch
from tests.test_unit_test_repair_budget import repair_task


class SmallTaskOutputRecoveryTests(unittest.TestCase):
    """执行真实归一化、差异验证、修复节点及预算边界，模型响应使用确定性替身。"""

    def test_invalid_outputs_never_become_results_or_generic_summaries(self) -> None:
        """拒绝空文本、工具参数、嵌套回退和缺字段结果，保留明确错误码与原因。"""
        responses = [
            "", "Agent completed without a text message.",
            'Action: read_file\nAction Input: {"file_path": "/frontend/src/pages/AgeEntryPage/index.tsx"}',
            '{"file_path": "/frontend/src/pages/AgeEntryPage/index.tsx"}',
            '{"status":"completed","summary":"broken", "nested": {"status":"completed","summary":"inner"}',
            '{"status":"completed"}', '{"status":"completed","summary":"  "}',
            '{"status":"unknown","summary":"结果"}', '[]',
        ]
        for response in responses:
            with self.subTest(response=response):
                result = normalize_small_task_result(response)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failureCode"], "invalid_agent_output")
                self.assertTrue(result["failureReason"])
                self.assertEqual(result["summary"], result["failureReason"])
                self.assertNotIn("Agent 未提供执行摘要", result["summary"])

    def test_complete_json_and_fenced_results_preserve_business_decisions(self) -> None:
        """完整 JSON 的成功、业务失败与人工确认语义保持不变。"""
        for status in ("completed", "already_satisfied", "failed", "requires_workflow", "requires_user_confirmation"):
            text = json.dumps({"status": status, "summary": "有效结果", "failureReason": "业务失败" if status == "failed" else None})
            for response in (text, f"```json\n{text}\n```"):
                result = normalize_small_task_result(response)
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["failureCode"])

    def test_real_batch_protocol_failures_retry_until_per_check_budget_is_exhausted(self) -> None:
        """前两张图的响应走真实执行器，前九次返回复测，第十次停止且不清零额度。"""
        for response in ("Agent completed without a text message.", 'Action: read_file\nAction Input: {"file_path":"/backend/src/test/java/ExampleTest.java"}'):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as workspace:
                state = {"workspace": workspace}
                for attempt in range(1, 11):
                    # 模拟复测失败后 RepairPlanner 建立新一轮计划，历史计数继续保留。
                    state.update({"small_task_tasks": [repair_task("backend_unit_tests", f"repair-{attempt}")],
                                  "unit_test_repair_charged_checks": []})
                    with patch("app.services.small_task.invoke_small_task_agent", return_value=response):
                        update = unit_test_repair(state)
                    self.assertEqual(update["unit_test_repair_attempts"], {"backend_unit_tests": attempt})
                    self.assertEqual(route_small_task_result(update), "unit_test" if attempt < 10 else "handle_failure")
                    self.assertEqual(len(update["small_task_results"]), attempt)
                    if attempt == 10:
                        self.assertIn("10 次修复额度", update["message"])
                        self.assertIn("SmallTask Agent", update["message"])
                    state.update(update)
                state.update({"small_task_tasks": [repair_task("backend_unit_tests", "eleventh")],
                              "unit_test_repair_charged_checks": []})
                with patch("app.services.small_task.invoke_small_task_agent") as invoke:
                    result = unit_test_repair(state)
                invoke.assert_not_called()
                self.assertEqual(result["status"], "failed")

    def test_invalid_response_keeps_actual_changes_for_retest(self) -> None:
        """Agent 已落盘但最终响应无效时保留真实 Diff，不能把失败当成未修改。"""
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "backend/src/test/java/ExampleTest.java"
            target.parent.mkdir(parents=True)
            target.write_text("before", encoding="utf-8")

            def modify_then_return_empty(**kwargs: object) -> str:
                """模拟真实文件修改后空响应，确保复测能看到保留的变更。"""
                target.write_text("after", encoding="utf-8")
                return "Agent completed without a text message."

            with patch("app.services.small_task.invoke_small_task_agent", side_effect=modify_then_return_empty):
                result = unit_test_repair({"workspace": workspace, "repair_tasks": [repair_task("backend_unit_tests")]})
            self.assertEqual(route_small_task_result(result), "unit_test")
            self.assertTrue(result["small_task_code_change_sets"])
            self.assertEqual(result["small_task_results"][0]["changedFiles"], ["backend/src/test/java/ExampleTest.java"])
            self.assertEqual(result["small_task_results"][0]["status"], "failed")

    def test_business_failure_and_scope_escalation_do_not_auto_retry(self) -> None:
        """声明完成但未修改、明确失败及人工范围确认沿用原边界。"""
        for status in ("completed", "failed", "requires_user_confirmation"):
            response = json.dumps({"status": status, "summary": "结果", "failureReason": "业务失败" if status == "failed" else None,
                                   "escalation": {"requestedPaths": ["backend/src/test/java/Other.java"]}})
            with tempfile.TemporaryDirectory() as workspace, patch("app.services.small_task.invoke_small_task_agent", return_value=response):
                result = unit_test_repair({"workspace": workspace, "repair_tasks": [repair_task("backend_unit_tests")]})
            self.assertEqual(route_small_task_result(result), "await_user_input" if status == "requires_user_confirmation" else "handle_failure")

    def test_integration_repairs_do_not_inherit_unit_test_retry_policy(self) -> None:
        """共享 Agent 的协议错误分类不改变集成测试的独立重试策略。"""
        with tempfile.TemporaryDirectory() as workspace, patch("app.services.small_task.invoke_small_task_agent", return_value=""):
            result = small_task_repair({"workspace": workspace, "repair_tasks": [repair_task("backend_unit_tests")]})
        self.assertEqual(route_small_task_result(result), "handle_failure")

    def test_unauthorized_changes_override_protocol_retry(self) -> None:
        """空响应伴随越权写入时仍停止，不能以输出错误为由继续派发。"""
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "outside.py"
            target.write_text("before", encoding="utf-8")

            def unauthorized_write(**kwargs: object) -> str:
                """模拟被真实快照检测出的批次外写入。"""
                target.write_text("after", encoding="utf-8")
                return ""

            with patch("app.services.small_task.invoke_small_task_agent", side_effect=unauthorized_write):
                result = execute_small_task_batch(state={"workspace": workspace}, tasks=[repair_task("backend_unit_tests")])
            self.assertIsNone(result["results"][0]["failureCode"])
            self.assertIn("批次外文件变更", result["results"][0]["failureReason"])

    def test_ag_ui_run_continues_through_protocol_failure_retest_and_next_repair(self) -> None:
        """真实 AG-UI 流在协议失败后继续同轮运行，复测及下一次修复后才正常结束。"""
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from app.graph.state import ProjectState
        from app.protocols.workflow import build_workflow_ag_ui_stream

        def initial_tasks(state: ProjectState) -> dict:
            """提供单测失败后的第一轮受限修复计划。"""
            return {"phase": "unit_test", "status": "in_progress",
                    "repair_tasks": [repair_task("backend_unit_tests")],
                    "repair_return_node": "unit_test"}

        def recheck_and_plan(state: ProjectState) -> dict:
            """模拟第一次真实复测失败需新计划，第二次复测通过。"""
            passed = state["unit_test_repair_attempts"]["backend_unit_tests"] == 2
            return {"phase": "unit_test", "status": "completed" if passed else "in_progress",
                    "unit_test_gate_passed": passed,
                    "small_task_tasks": [repair_task("backend_unit_tests", "second")],
                    "unit_test_repair_charged_checks": []}

        def after_recheck(state: ProjectState) -> str:
            """测试图仅以确定性复测结果决定是否继续修复。"""
            return END if state.get("unit_test_gate_passed") else "unit_test_repair"

        builder = StateGraph(ProjectState)
        builder.add_node("development_readiness_gate", initial_tasks)
        builder.add_node("unit_test_repair", unit_test_repair)
        builder.add_node("unit_test", recheck_and_plan)
        builder.add_edge(START, "development_readiness_gate")
        builder.add_edge("development_readiness_gate", "unit_test_repair")
        builder.add_conditional_edges("unit_test_repair", route_small_task_result, {"unit_test": "unit_test"})
        builder.add_conditional_edges("unit_test", after_recheck, {END: END, "unit_test_repair": "unit_test_repair"})
        graph = builder.compile(checkpointer=InMemorySaver())

        async def collect(workspace: str) -> list[dict]:
            """完整消费流并保留状态快照和结束帧用于断言。"""
            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "output-recovery", "runId": "output-recovery-run",
                "messages": [{"role": "user", "content": "修复单元测试"}],
                "forwardedProps": {"workspaceRoot": workspace},
            })]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.protocols.workflow.request._project_plan_start_values", return_value={},
        ), patch("app.services.small_task.invoke_small_task_agent", side_effect=[
            "Agent completed without a text message.",
            '{"status":"already_satisfied","summary":"复核当前文件与目标一致","verification":["检查通过"]}',
        ]) as invoke:
            events = asyncio.run(collect(workspace))
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertNotIn("RUN_ERROR", [event["type"] for event in events])
        workflow = events[-1]["result"]["workflow"]
        self.assertEqual(workflow["summary"]["unitTestRepairAttempts"], {"backend_unit_tests": 2})
        self.assertTrue(workflow["summary"]["unitTestGatePassed"])
        self.assertEqual(workflow["summary"]["smallTaskResults"][0]["failureCode"], "invalid_agent_output")
