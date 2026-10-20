"""测试阶段把 SmallTask 作为内部修复步骤，失败重试重新执行完整测试。"""

import tempfile
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.workflow import build_graph
from app.services.business_node_retry import prepare_business_node_retry, retry_action_plan, read_retry_hint
from app.services.application_lifecycle import start_workbench_execution
from app.services.retry_preparation import abandon_prepared_retry
from tests.test_business_node_retry import BusinessNodeRetryTests
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class TestingStageRetryTests(unittest.IsolatedAsyncioTestCase):
    """使用主图真实路由、失败收口和 checkpoint 检查公开恢复动作。"""

    async def test_small_task_failure_restarts_testing(self):
        """返回失败及抛出异常都从测试入口重开，不再次执行旧 SmallTask。"""
        for raises in (False, True):
            with self.subTest(raises=raises), tempfile.TemporaryDirectory() as root:
                _write_ready_workbench_lifecycle(root)
                start_workbench_execution(root, scope="page", target_id="page-a", page_id="page-a",
                    thread_id="thread", run_id="source", phase="integration_test", owner_session_id="session")
                calls = []
                changes = [{"id": "already-written", "files": [{"path": "frontend/src/Page.tsx"}]}]

                def testing(state):
                    """重试必须重新检查当前代码，不沿用任务、检查结果、确认或旧预算。"""
                    calls.append("integration_test")
                    self.assertEqual(state["request"], "原测试请求")
                    self.assertEqual(state["repair_iteration"], 0)
                    self.assertEqual(state["max_repair_iterations"], 3)
                    self.assertFalse(state["integration_build_checks_completed"])
                    self.assertFalse(state["quality_gate_passed"])
                    for key in ("repair_tasks", "small_task_tasks", "small_task_results", "test_results", "integration_build_results"):
                        self.assertEqual(state[key], [])
                    self.assertEqual(state["repair_task_plan"], {})
                    self.assertEqual(state["small_task_handoff"], {})
                    self.assertEqual(state["test_report"], {})
                    self.assertEqual(state["frontend_performance_decision"], "")
                    self.assertEqual(state["review_phase_confirmation"], {})
                    self.assertEqual(state["small_task_code_change_sets"], changes)
                    self.assertEqual(state["unit_test_decision"], "skip")
                    self.assertEqual(state["workspace_revision"], "current-disk")
                    # 模拟重新检查完成后的审阅门，旧确认不能推进下游。
                    return {"status": "requires_user_input", "integration_next_action": "await_user_input"}

                def repair(state):
                    """模拟测试阶段内部修复失败，记录是否错误地再次调用旧任务。"""
                    calls.append("small_task_repair")
                    if raises:
                        raise RuntimeError("SmallTask 模型连接失败")
                    return {"phase": "small_task_repair", "status": "failed", "message": "SmallTask 修复失败"}

                with patch("app.graph.workflow.nodes.integration_test", testing), \
                     patch("app.graph.workflow.nodes.small_task_repair", repair), \
                     patch("app.services.stage_prerequisites.require_stage_inputs"), \
                     patch("app.services.stage_prerequisites.regeneration_for_retry", return_value=None), \
                     patch("app.graph.nodes.workspace_inspection.inspect_workspace", return_value={"workspace_revision": "current-disk"}):
                    graph = build_graph(checkpointer=InMemorySaver())
                    initial = {"workspace": root, "active_run_id": "source", "active_thread_id": "thread",
                        "resume_from": "small_task_repair", "request": "原测试请求", "repair_iteration": 3,
                        "max_repair_iterations": 3, "integration_build_checks_completed": True,
                        "integration_build_results": [{"passed": True}], "quality_gate_passed": True,
                        "test_results": [{"passed": True}], "test_report": {"old": True},
                        "repair_tasks": [{"id": "old"}], "small_task_tasks": [{"id": "old", "status": "failed"}],
                        "small_task_results": [{"status": "failed"}], "repair_task_plan": {"old": True},
                        "small_task_handoff": {"old": True}, "small_task_code_change_sets": changes,
                        "frontend_performance_decision": "skip", "unit_test_decision": "skip",
                        "review_phase_confirmation": {"action": "confirm"}}
                    config = {"configurable": {"thread_id": "thread"}}
                    if raises:
                        with self.assertRaisesRegex(RuntimeError, "SmallTask"):
                            await graph.ainvoke(initial, config)
                    else:
                        self.assertEqual((await graph.ainvoke(initial, config))["status"], "failed")
                    source = BusinessNodeRetryTests().source(root)
                    plan = await retry_action_plan(source, graph)
                    self.assertEqual(plan.primary_action.target_node, "integration_test")
                    context = await prepare_business_node_retry(source, graph)
                    self.addAsyncCleanup(abandon_prepared_retry, workspace=root, source=source,
                        before=context.retry_lifecycle_before_handoff, new_run_id=context.new_run_id, lease=context.workspace_lease)
                    self.assertEqual(context.recovery_plan.target_node, "integration_test")
                    result = await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
                    self.assertEqual(result["status"], "requires_user_input")
                    self.assertEqual(calls, ["small_task_repair", "integration_test"])
                    # 暂停续接和二次修改不借用测试阶段的失败映射。
                    for updates in ({"status": "interrupted"}, {"workflow_scope": "conversation"}):
                        hint = await read_retry_hint(source.model_copy(update=updates), graph)
                        self.assertEqual(hint.node, "small_task_repair")
