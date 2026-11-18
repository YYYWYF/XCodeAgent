"""回归源码报错修复方向丢失、修复中的失败投影以及取消后的迟到写入。"""

import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from app.agents.small_task.agent import create_frontend_integration_agent
from app.agents.small_task.runner import build_small_task_prompt
from app.agents.small_task.scope import ScopedSmallTaskBackend, small_task_path_scope
from app.graph.workflow import route_test_validation
from app.graph.subgraphs.testing import integration_test
from app.middleware.small_task_cancellation import SmallTaskCancellationMiddleware
from app.protocols.workflow.lifecycle import project_workflow_lifecycle_boundary
from app.services.application_lifecycle import start_workbench_execution
from app.services.small_task import build_small_task_packet, execute_small_task_batch
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class ReadLoopModel(FakeMessagesListChatModel):
    """确定性复现只调用 read_file、始终不修改文件的模型。"""

    tool_sets: list[set[str]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: object) -> "ReadLoopModel":
        """保留假模型的固定工具响应，供真实 Deep Agent 执行循环。"""
        self.tool_sets.append({tool.name for tool in tools})
        return self


class IntegrationRepairProgressTests(unittest.TestCase):
    """通过真实任务包、生命周期持久化和文件工具边界验证修复流程。"""

    def test_scope_confirmation_projects_and_resumes_without_rechecking(self) -> None:
        """已有计划经 AG-UI 生命周期投影后仅消费一次明确答复。"""
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.services.development_artifacts.require_test_entry",
        ), patch("app.graph.subgraphs.testing._testing_subgraph") as testing:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(workspace, scope="application", target_id="application", page_id=None,
                                     thread_id="test-thread", run_id="test-run", phase="integration_test")
            task = {"id": "frontend-build", "owner": "frontend", "allowed_paths": ["frontend"]}
            plan = {"planId": "test-plan", "decision": "requires_user_confirmation",
                    "escalationKind": "code_scope", "candidateTasks": [task], "tasks": []}
            testing.invoke.return_value = {
                "integration_next_action": "await_user_input", "repair_task_plan": plan,
                "clarification": {"mode": "repair_scope_confirmation", "planId": plan["planId"]},
            }
            waiting = integration_test({"workspace": workspace})
            self.assertEqual(route_test_validation(waiting), "await_user_input")
            lifecycle = project_workflow_lifecycle_boundary(
                workspace, run_id="test-run", node_name="integration_test", update=waiting,
            )
            pending = lifecycle["activeExecutions"]["test-run"]["pendingInteraction"]
            self.assertEqual(pending["type"], "repair_scope_confirmation")
            for answer, expected in (
                ("拒绝修复范围", "handle_failure"),
                ("批准修复范围", "small_task_repair"),
                ("是否批准修复范围", "await_user_input"),
            ):
                resumed = integration_test({**waiting, "workspace": workspace,
                                            "request": f"修复范围\n  回答：{answer}"})
                self.assertEqual(route_test_validation({**waiting, **resumed}), expected)
                self.assertEqual(resumed.get("repair_tasks"),
                                 [task] if expected == "small_task_repair" else
                                 [] if expected == "handle_failure" else None)
            testing.invoke.assert_called_once()

    def test_repeated_reads_stop_at_model_budget_without_protocol_retries(self) -> None:
        """真实 Agent 反复读文件时应明确失败，不能无限调用或启动三次输出重试。"""
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend/src/App.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("const App = ( => {}")
            model = ReadLoopModel(responses=[AIMessage(content="", tool_calls=[{
                "name": "read_file", "id": f"read-loop-{index}",
                "args": {"file_path": "/frontend/src/App.tsx"},
            }]) for index in range(20)])
            agent = create_frontend_integration_agent(
                # 生产技能/记忆后端不依赖 Agent 的 files channel；这里同样使用磁盘后端，
                # 避免嵌在范围代理内的 StateBackend 在真正调用模型前就因缺少 channel 失败。
                model, workspace,
                user_skills_backend=FilesystemBackend(root_dir=workspace, virtual_mode=True),
                agent_memory_backend=FilesystemBackend(root_dir=workspace, virtual_mode=True),
                required_user_skills_prompt="",
            )
            def invoke_loop(**kwargs: object) -> str:
                """通过生产 Prompt 调用真实 Agent，保留原生调用上限异常。"""
                agent.invoke({"messages": [{"role": "user", "content": build_small_task_prompt(kwargs["packet"])}]})
                return ""

            with patch("app.services.frontend_integration_repair.invoke_small_task_agent", side_effect=invoke_loop) as invoke, patch(
                "app.services.frontend_integration_repair.finalize_frontend_repair", return_value=None,
            ) as close:
                result = execute_small_task_batch(state={"workspace": workspace}, tasks=[{
                    "id": "loop", "owner": "frontend", "allowed_paths": ["frontend"],
                }])
            invoke.assert_called_once()
            close.assert_called_once()
            self.assertEqual(result["results"][0]["status"], "failed")
            self.assertEqual(result["results"][0]["failureCode"], "invalid_agent_output")
            self.assertEqual(model.i, 12)
            self.assertTrue(model.tool_sets)
            for tools in model.tool_sets:
                self.assertFalse(tools & {"execute", "task", "write_todos"})
            self.assertEqual(target.read_text(), "const App = ( => {}")

    def test_frontend_oversized_evidence_is_valid_bounded_json(self) -> None:
        """只传当前失败，字段级裁剪保留授权和验收项。"""
        task = {
            "id": "frontend-task", "owner": "frontend", "allowed_paths": ["frontend"],
            "description": "Fix the reported diagnostic",
            "failure_evidence": {"evidence": "\"\\\n" * 30_000},
            "acceptance_criteria": ["构建必须通过"],
        }
        packet = build_small_task_packet(task, {"test_report": {"body": "REPORT_ONLY"}},
                                         source="integration_test.small_task")
        prompt = build_small_task_prompt(packet).split("TaskPacket:\n", 1)[1]
        self.assertLessEqual(len(prompt), 12_000)
        self.assertNotIn("REPORT_ONLY", prompt)
        self.assertEqual(json.loads(prompt)["allowedPaths"], ["frontend"])
        self.assertEqual(json.loads(prompt)["engineeringAcceptanceChecks"], ["构建必须通过"])

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
