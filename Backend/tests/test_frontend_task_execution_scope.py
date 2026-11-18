"""回归 API 任务授权的真实工具入口、生命周期和并发隔离。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage, ToolMessage
from langsmith import tracing_context

from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.execution_scope import FrontendTaskExecutionScope
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.frontend.task_packet import API, PAGE, SERVICE
from app.services.run_workers import bind_run_workers
from tests.test_frontend_api_execution import RecordingModel, _fixture, _tool
from tests.test_frontend_build_execution import _API, _PAGE, _SKILL, _create_file, _packet


class FrontendTaskExecutionScopeTests(unittest.TestCase):
    """平台先激活任务，所有真实工具继续遵守精确范围及操作类型。"""

    def test_real_api_tools_sync_and_async(self):
        """无需 begin 即可读取规范和目标，多轮读写正常且越界工具被拒绝。"""
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous), TemporaryDirectory() as folder:
                root = Path(folder)
                _create_file(root, SERVICE, "export default {};")
                _create_file(root, _PAGE, "private-marker")
                scope = FrontendTaskExecutionScope(folder, [_packet(API)])
                calls = [
                    _tool("read_file", "skill", file_path=_SKILL),
                    _tool("read_file", "service", file_path=SERVICE),
                    _tool("read_file", "denied-read", file_path=_PAGE),
                    _tool("write_file", "denied-write", file_path=_PAGE, content="forbidden"),
                    _tool("delete_file", "denied-delete", file_path=_PAGE),
                    _tool("write_file", "created", file_path=_API, content="export const load = 1;"),
                ]
                model = RecordingModel(responses=[
                    AIMessage(content="", tool_calls=calls),
                    AIMessage(content="", tool_calls=[
                        _tool("read_file", "target", file_path=_API),
                        _tool("grep", "search", path=_API, pattern="export"),
                        _tool("code_graph_context", "graph", paths=[_API]),
                        _tool("edit_file", "edited", file_path=_API, old_string="= 1", new_string="= 2"),
                        _tool("execute", "shell", command="printf forbidden"),
                    ]),
                    AIMessage(content="done"),
                ])
                with scope.task_context(), tracing_context(enabled=False):
                    agent = create_frontend_agent(model, folder, user_skills_backend=StateBackend(),
                                                  agent_memory_backend=StateBackend(), build_scope=scope)
                    payload = {"messages": [{"role": "user", "content": "执行当前 API 模块"}]}
                    with patch("app.tools.execute.workspace_process_registry.run") as execute:
                        result = asyncio.run(agent.ainvoke(payload)) if asynchronous else agent.invoke(payload)
                    self.assertFalse(any(call.kwargs.get("shell") for call in execute.call_args_list))
                    self.assertTrue(scope.active)
                    self.assertEqual(scope.step, 0)
                messages = {message.tool_call_id: message for message in result["messages"]
                            if isinstance(message, ToolMessage)}
                for call in ("skill", "service", "created", "target", "search", "graph", "edited"):
                    self.assertEqual(messages[call].status, "success", messages[call])
                for call in ("denied-read", "denied-write", "denied-delete"):
                    self.assertEqual(messages[call].status, "error")
                self.assertIn("FE-API-BOUNDARY", str(messages["skill"].content))
                self.assertIn("不允许执行 Shell", str(messages["shell"].content))
                self.assertNotIn("private-marker", str(messages))
                self.assertEqual((root / _API[1:]).read_text(), "export const load = 2;")
                self.assertEqual((root / _PAGE[1:]).read_text(), "private-marker")
                self.assertIsNone(scope.current)
                self.assertFalse(scope.reads.files)
                self.assertFalse(scope.graph_queries)
                self.assertFalse(scope.revisions)
                self.assertNotIn("begin_frontend_task", model.tool_names)

    def test_generator_cleans_exception_and_cancellation(self):
        """真实派发入口在异常和取消后撤销任务权限，不依赖模型完成报告。"""
        for error in (RuntimeError, asyncio.CancelledError):
            with self.subTest(error=error), TemporaryDirectory() as folder:
                task, state = _fixture(Path(folder))
                captured = []

                def fail(**kwargs):
                    """确认首次调用前任务和 Run 身份已绑定，再模拟退出。"""
                    scope = kwargs["scope"]
                    captured.append(scope)
                    self.assertEqual(scope.run_id, "test-run")
                    self.assertEqual(scope.current["id"], task["id"])
                    scope.authorize("read_file", _SKILL)
                    scope.authorize("read_file", SERVICE)
                    raise error("stopped")

                with bind_run_workers("test-run", folder), patch(
                    "app.agents.frontend.generator._invoke_live_frontend_agent", side_effect=fail,
                ), self.assertRaises(error):
                    generate_frontend_with_deep_agent(**state, tasks=[task])
                self.assertIsNone(captured[0].current)
                with self.assertRaises(ValueError):
                    captured[0].authorize("read_file", SERVICE)

    def test_parallel_and_sequential_dispatch_do_not_share_permissions(self):
        """同一 Run 的并发 API 及后续任务使用不同实例，彼此目标始终不可读写。"""
        with TemporaryDirectory() as folder, bind_run_workers("shared-run", folder):
            task, state = _fixture(Path(folder))
            second = {**task, "id": "second", "target_files": ["frontend/src/apis/second.ts"],
                      "allowed_paths": ["frontend/src/apis/second.ts"],
                      "change_scope": [{"path": "frontend/src/apis/second.ts", "operation": "modify"}]}
            barrier = Barrier(2)
            scopes = []
            page_scope = FrontendTaskExecutionScope(folder, [_packet(PAGE)])

            def invoke(**kwargs):
                """两个任务同时保持活动，再核对对方目标和结束后授权撤销。"""
                scope = kwargs["scope"]
                scopes.append(scope)
                self.assertEqual(scope.run_id, "shared-run")
                self.assertEqual(page_scope.step, 1)
                target = scope.current["target_files"][0]
                other = "/frontend/src/apis/second.ts" if target != "/frontend/src/apis/second.ts" else f"/{task['target_files'][0]}"
                if barrier:
                    barrier.wait(timeout=10)
                scope.authorize("read_file", target)
                scope.authorize("edit_file", target)
                for name in ("read_file", "edit_file", "delete_file"):
                    with self.assertRaises(ValueError):
                        scope.authorize(name, other)
                return json.dumps({"task_results": [{"task_id": scope.current["id"],
                                                     "status": "completed", "summary": "候选"}]})

            def dispatch(item):
                """使用与真实调度器相同的 Run 身份绑定各工作线程。"""
                with bind_run_workers("shared-run", folder):
                    return generate_frontend_with_deep_agent(**state, tasks=[item])

            with patch("app.agents.frontend.generator._invoke_live_frontend_agent", side_effect=invoke), page_scope.task_context():
                page_scope.transition("begin_frontend_task", {"task_id": PAGE})
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(dispatch, [task, second]))
                barrier = None
                reports = generate_frontend_with_deep_agent(**state, tasks=[task, second])
                self.assertEqual(page_scope.step, 1)
                self.assertEqual(page_scope.current["id"], PAGE)
            self.assertTrue(all(result[0]["status"] == "completed" for result in results))
            self.assertEqual([report["task_id"] for report in reports], [task["id"], "second"])
            self.assertEqual(len({id(scope) for scope in scopes}), 4)
            self.assertTrue(all(scope.current is None for scope in scopes))

    def test_page_still_requires_completion_receipt(self):
        """页面自报成功无法跳过原四步工具与步骤验收。"""
        with TemporaryDirectory() as folder:
            task = {"id": PAGE, "owner": "frontend", "deliverables": [{"kind": PAGE}]}
            with patch("app.agents.frontend.generator.frontend_task_packet", return_value=_packet(PAGE)), patch(
                "app.agents.frontend.generator._invoke_live_frontend_agent",
                return_value=json.dumps({"task_results": [{"task_id": PAGE, "status": "completed", "summary": "claim"}]}),
            ):
                result, = generate_frontend_with_deep_agent(project_plan={}, build_task_plan={}, tasks=[task], workspace=folder)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "runner_protocol_error")

    def test_page_keeps_step_tools_and_api_cannot_transition(self):
        """页面模型仍收到三个控制工具，API 即使直接调用状态机也无法进入四步。"""
        with TemporaryDirectory() as folder:
            page = FrontendTaskExecutionScope(folder, [_packet(PAGE)])
            model = RecordingModel(responses=[AIMessage(content="done")])
            with page.task_context(), tracing_context(enabled=False):
                agent = create_frontend_agent(model, folder, user_skills_backend=StateBackend(),
                                              agent_memory_backend=StateBackend(), build_scope=page)
                agent.invoke({"messages": [{"role": "user", "content": "检查工具集合"}]})
            for name in ("begin_frontend_task", "advance_frontend_step", "complete_frontend_task"):
                self.assertIn(name, model.tool_names)
            api = FrontendTaskExecutionScope(folder, [_packet(API)])
            with api.task_context(), self.assertRaises(ValueError):
                api.transition("begin_frontend_task", {"task_id": API})
