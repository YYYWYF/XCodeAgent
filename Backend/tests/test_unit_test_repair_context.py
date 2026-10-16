"""覆盖单测修复在普通执行与结束后续接中的计划、报告和工具执行上下文。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.small_task.agent import create_small_task_agent
from app.agents.small_task.runner import build_small_task_prompt
from app.graph.nodes.small_task import unit_test_repair
from app.graph.workflow import build_graph
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import (
    end_workbench_execution,
    load_application_lifecycle,
    start_workbench_execution,
)
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


STRATEGY = "依赖未安装导致 jest 缺失；在 frontend 执行 pnpm install，然后 pnpm run test。"
VERIFIED_RESULT = json.dumps({
    "status": "already_satisfied", "summary": "已安装声明依赖并复测通过，无需源码变更。",
    "changedFiles": [], "verification": ["pnpm install: exit 0", "pnpm run test: exit 0"],
})


def repair_state(workspace: str) -> dict[str, Any]:
    """复现真实 checkpoint：单测上下文完整，通用修复字段为空或属于其他阶段。"""

    task = {
        "id": "repair-frontend-unit", "owner": "frontend", "status": "pending",
        "description": "修复前端单元测试", "dependencies": [],
        "allowed_paths": ["frontend"], "target_files": ["frontend/tests/orders.test.ts"],
        "source_ref": {"failed_check_id": "frontend_unit_tests"},
        "failure_evidence": {"evidence": "sh: jest: command not found; node_modules missing"},
    }
    check = {"id": "frontend_unit_tests", "name": "前端单元测试", "passed": False}
    return {
        "workspace": workspace, "active_run_id": "old", "active_thread_id": "thread",
        "owner_session_id": "session", "build_execution_scope": {"type": "application", "targetId": "application"},
        "unit_test_decision": "run", "unit_test_next_action": "unit_test_repair",
        "repair_return_node": "unit_test", "small_task_tasks": [task], "repair_tasks": [task],
        "unit_test_repair_task_plan": {"strategy": STRATEGY, "tasks": [task]},
        "unit_test_repair_task_plan_path": "unit-test-repair-task-plan.json",
        "unit_test_report": {"checks": [check], "revision_requests": [{"id": "unit-revision"}]},
        "unit_test_results": [check], "unit_test_repair_attempts": {"frontend_unit_tests": 3},
        "unit_test_repair_charged_checks": [],
        "repair_task_plan": {"strategy": "不得使用的集成测试旧策略"},
        "test_report": {"checks": [{"id": "frontend_build"}]},
    }


class RepairContextTests(unittest.TestCase):
    """普通派发必须按单测的权威计划生成真实 SmallTask 任务包。"""

    def test_unit_repair_preserves_strategy_and_report_without_cross_stage_pollution(self) -> None:
        """完整通过节点与批次执行器，检查 Agent 实际收到的上下文而非中间返回值。"""

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.services.small_task.invoke_small_task_agent", return_value=VERIFIED_RESULT,
        ) as invoke:
            state = repair_state(workspace)
            result = unit_test_repair(state)
        packet = invoke.call_args.kwargs["packet"]
        self.assertEqual(packet["repairStrategy"], STRATEGY)
        self.assertIn("frontend_unit_tests", packet["confirmedContext"]["testReport"])
        self.assertNotIn("frontend_build", packet["confirmedContext"]["testReport"])
        self.assertIn("unit-revision", packet["confirmedContext"]["revisionRequests"])
        self.assertEqual(packet["source"], "unit_test.small_task")
        self.assertEqual(packet["allowedPaths"], ["frontend", "frontend/tests/orders.test.ts"])
        self.assertEqual(result["small_task_route"], "unit_test")
        self.assertEqual(result["unit_test_repair_attempts"], {"frontend_unit_tests": 4})
        self.assertEqual(state["repair_task_plan"]["strategy"], "不得使用的集成测试旧策略")

    def test_real_agent_can_install_and_verify_without_inventing_code_changes(self) -> None:
        """真实 Agent 工具流可以先安装既有依赖再复测，并用已满足结果返回单测节点。"""

        class CommandModel(FakeMessagesListChatModel):
            def bind_tools(self, tools: Any, **kwargs: Any) -> CommandModel:
                """绑定生产工具，同时确认命令能力可用且编排能力保持关闭。"""

                names = {tool.name for tool in tools}
                self_test.assertIn("execute", names)
                self_test.assertNotIn("task", names)
                self_test.assertNotIn("write_todos", names)
                return self

        self_test = self
        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text('{"scripts":{"test":"jest"}}', encoding="utf-8")
            model = CommandModel(responses=[
                AIMessage(content="", tool_calls=[{"name": "execute", "id": "install", "args": {"command": "cd frontend && pnpm install"}}]),
                AIMessage(content="", tool_calls=[{"name": "execute", "id": "test", "args": {"command": "cd frontend && pnpm run test"}}]),
                AIMessage(content=VERIFIED_RESULT),
            ])
            backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            agent = create_small_task_agent(model, workspace, user_skills_backend=backend, agent_memory_backend=backend)
            with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(small_task=agent)), patch(
                "app.tools.execute.workspace_process_registry.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="PASS", stderr=""),
            ) as execute:
                result = unit_test_repair(repair_state(workspace))
        self.assertEqual(result["small_task_results"][0]["status"], "already_satisfied", result)
        self.assertEqual([call.args[0] for call in execute.call_args_list], [
            "cd frontend && pnpm install", "cd frontend && pnpm run test",
        ])
        self.assertEqual(result["small_task_results"][0]["status"], "already_satisfied")
        self.assertEqual(result["small_task_route"], "unit_test")
        self.assertEqual(result["code_changes"], {})

    def test_prompt_distinguishes_dependency_failures_from_source_failures(self) -> None:
        """已定位依赖故障应先执行策略并复测，不要求从测试或业务源码重新定位。"""

        prompt = build_small_task_prompt({"repairStrategy": STRATEGY})
        self.assertIn("already diagnosed missing installed dependency", prompt)
        self.assertIn("rerun the exact failed check", prompt)
        self.assertIn("without source edits, return already_satisfied", prompt)
        self.assertIn("For source failures", prompt)


class EndedRepairContextTests(unittest.IsolatedAsyncioTestCase):
    """沿生产 Graph、checkpoint、结束归档和 AG-UI 续接验证策略不丢失。"""

    async def test_end_then_continue_keeps_original_unit_plan_in_actual_agent_packet(self) -> None:
        """恢复新 Run 的任务包读取原单测计划，保留额度并回到真实复测入口。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(
                workspace, scope="application", target_id="application", page_id=None,
                thread_id="thread", owner_session_id="session", run_id="old", phase="unit_test_repair",
            )
            graph = build_graph(checkpointer=InMemorySaver())
            await graph.aupdate_state(
                {"configurable": {"thread_id": "thread"}}, repair_state(workspace), as_node="unit_test",
            )
            end_workbench_execution(workspace, run_id="old", preserve_resume=True)
            with patch("app.services.small_task.invoke_small_task_agent", return_value=VERIFIED_RESULT) as invoke, patch(
                "app.graph.nodes.workspace_inspection.inspect_workspace", return_value={"workspace_revision": "current"},
            ), patch("app.graph.nodes.unit_test", return_value={
                "phase": "unit_test", "status": "requires_user_input", "unit_test_next_action": "await_user_input",
                "clarification": {"mode": "unit_test_confirmation", "status": "requires_user_input"},
            }) as retest:
                frames = "".join([frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                    "threadId": "thread", "runId": "new", "messages": [{"role": "user", "content": "继续已结束的任务。"}],
                    "forwardedProps": {"workspaceRoot": workspace, "sessionId": "session", "resumeExecutionRunId": "old"},
                })])
            self.assertNotIn('"type":"RUN_ERROR"', frames)
            invoke.assert_called_once()
            packet = invoke.call_args.kwargs["packet"]
            self.assertEqual(packet["repairStrategy"], STRATEGY)
            self.assertEqual(packet["executionRunId"], "new")
            self.assertIn("frontend_unit_tests", packet["confirmedContext"]["testReport"])
            retest.assert_called_once()
            saved = await graph.aget_state({"configurable": {"thread_id": "thread"}})
            self.assertEqual(saved.values["unit_test_repair_attempts"], {"frontend_unit_tests": 4})
            self.assertNotIn("old", load_application_lifecycle(workspace).ended_executions)
