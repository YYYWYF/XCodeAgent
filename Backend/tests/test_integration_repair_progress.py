"""回归源码报错修复方向丢失、修复中的失败投影以及取消后的迟到写入。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agents.small_task.agent import create_small_task_agent
from app.agents.small_task.runner import build_small_task_prompt
from app.agents.small_task.scope import ScopedSmallTaskBackend, small_task_path_scope
from app.graph.subgraphs.testing import integration_test
from app.middleware.small_task_cancellation import SmallTaskCancellationMiddleware
from app.protocols.workflow.lifecycle import project_workflow_lifecycle_boundary
from app.services.application_lifecycle import start_workbench_execution
from app.services.small_task import build_small_task_packet, execute_small_task_batch
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class ReadLoopModel(FakeMessagesListChatModel):
    """确定性复现只调用 read_file、始终不修改文件的模型。"""

    def bind_tools(self, tools: object, **kwargs: object) -> "ReadLoopModel":
        """保留假模型的固定工具响应，供真实 Deep Agent 执行循环。"""
        return self


class IntegrationRepairProgressTests(unittest.TestCase):
    """通过真实任务包、生命周期持久化和文件工具边界验证修复流程。"""

    def test_repeated_reads_stop_at_model_budget_without_protocol_retries(self) -> None:
        """真实 Agent 反复读文件时应明确失败，不能无限调用或启动三次输出重试。"""
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend/src/App.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("const App = ( => {}")
            model = ReadLoopModel(responses=[AIMessage(content="", tool_calls=[{
                "name": "read_file", "id": "read-loop",
                "args": {"file_path": "/frontend/src/App.tsx"},
            }])])
            agent = create_small_task_agent(
                model, workspace, user_skills_backend=StateBackend(), agent_memory_backend=StateBackend(),
            )
            def invoke_loop(**kwargs: object) -> str:
                """通过生产 Prompt 调用真实 Agent，保留原生调用上限异常。"""
                agent.invoke({"messages": [{"role": "user", "content": build_small_task_prompt(kwargs["packet"])}]})
                return ""

            with patch("app.services.small_task.invoke_small_task_agent", side_effect=invoke_loop) as invoke:
                result = execute_small_task_batch(state={"workspace": workspace}, tasks=[{
                    "id": "loop", "owner": "frontend", "allowed_paths": ["frontend"],
                }])
            invoke.assert_called_once()
            self.assertEqual(result["results"][0]["status"], "failed")
            self.assertIn("24 轮模型调用", result["results"][0]["failureReason"])
            self.assertEqual(target.read_text(), "const App = ( => {}")

    def test_packet_prioritizes_real_source_diagnostics_and_keeps_planner_strategy(self) -> None:
        """App.tsx 的语法错误应先读源码，不能丢失规划方向或扩大授权范围。"""
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "frontend/src").mkdir(parents=True)
            (root / "frontend/src/App.tsx").write_text("const App = ( => {}")
            (root / "outside.ts").write_text("outside")
            task = {
                "id": "frontend-repair", "allowed_paths": ["frontend"],
                "target_files": ["frontend/package.json", "frontend/vite.config.ts"],
                "failure_evidence": {"failed_attempt": {"execution": {
                    "cwd": "frontend",
                    "stdout_tail": "src/App.tsx(11,25): error TS1005: '(' expected.\n"
                    "src/App.tsx(27,2): error TS1005: ')' expected.\n"
                    f"{root}/outside.ts(1,1): error TS1005: rejected.\n"
                    "../../outside.ts(1,1): error TS1005: rejected.\n",
                }}},
            }
            packet = build_small_task_packet(task, {
                "workspace": workspace, "active_run_id": "run-repair",
                "repair_task_plan": {"strategy": "修复 App.tsx 的箭头函数括号"},
            })
            self.assertEqual(packet["candidateFiles"], [
                "frontend/src/App.tsx", "frontend/package.json", "frontend/vite.config.ts",
            ])
            self.assertEqual(packet["repairStrategy"], "修复 App.tsx 的箭头函数括号")
            self.assertEqual(packet["allowedPaths"], task["allowed_paths"] + task["target_files"])

    def test_repair_dispatch_projects_running_but_terminal_failure_stays_failed(self) -> None:
        """仅已派发局部修复的检查失败保持运行，终止性失败仍能进入恢复入口。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(workspace, scope="application", target_id="application", page_id=None,
                                     thread_id="test-thread", run_id="test-run", phase="integration_test")
            for action in ("small_task_repair", "handle_failure"):
                with self.subTest(action=action), patch(
                    "app.services.development_artifacts.require_test_entry",
                ), patch("app.graph.subgraphs.testing._testing_subgraph.invoke", return_value={
                    "quality_gate_passed": False, "integration_next_action": action,
                    "repair_tasks": [{"id": "repair"}] if action == "small_task_repair" else [],
                    "test_results": [{"id": "frontend_build", "passed": False}],
                }):
                    update = integration_test({"workspace": workspace})
                payload = project_workflow_lifecycle_boundary(
                    workspace, run_id="test-run", node_name="integration_test", update=update,
                )
                expected = "running" if action == "small_task_repair" else "failed"
                self.assertEqual(payload["activeExecutions"]["test-run"]["status"], expected)
                self.assertFalse(update["test_results"][0]["passed"])

    def test_cancelled_small_task_cannot_call_model_or_touch_workspace(self) -> None:
        """模拟取消发生在同步修复线程仍存活时，后续模型/工具/写入必须被拦截。"""
        run_id = "cancelled-small-task-test"
        delegate = Mock()
        backend = ScopedSmallTaskBackend(delegate)
        middleware = SmallTaskCancellationMiddleware()
        handler = Mock()
        workspace_process_registry.cancel_run(run_id)
        try:
            with small_task_path_scope(["frontend"], run_id=run_id):
                for operation in (
                    lambda: backend.read("/frontend/src/App.tsx"),
                    lambda: backend.edit("/frontend/src/App.tsx", "before", "after"),
                    lambda: middleware.wrap_model_call(Mock(), handler),
                    lambda: middleware.wrap_tool_call(Mock(), handler),
                ):
                    with self.assertRaisesRegex(RuntimeError, "当前运行已停止"):
                        operation()
            handler.assert_not_called()
            self.assertEqual(delegate.mock_calls, [])
        finally:
            workspace_process_registry.allow_run(run_id)
