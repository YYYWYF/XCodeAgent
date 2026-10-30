"""针对 Runtime 断点验证真实分支边界，不启动 Studio、模型或真实数据库。"""

import ast
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, call

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.agent_runtime_health import runtime_health
from app.services.agent_runtime_repair_scope import runtime_failure_scope, runtime_repair_candidates
from app.services.business_acceptance_verifiers.python_agent import verify_agent_module
from app.services.runtime_agent_resources import agent_resource_files


FACTORY = '''from deepagents import create_deep_agent
from pathlib import Path
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from app.tools.leave import submit_leave
def create(model, context, checkpointer):
    return create_deep_agent(model=model, tools=TOOLS, middleware=MIDDLEWARE)
'''
TOOL = '''from langchain.tools import tool, ToolRuntime
from langgraph.types import interrupt
from uuid import uuid4
from app.agent.context import RuntimeContext
from app.application.leave_service import submit
@tool
async def submit_leave(request: str, runtime: ToolRuntime[RuntimeContext]):
    approved = interrupt({"schemaVersion": 1, "interactionId": uuid4().hex, "kind": "confirmation",
        "message": "确认提交请假？", "payload": {"toolId": "submit_leave", "arguments": {"request": request}}})
    if approved is not True:
        raise ValueError("操作未确认")
    return await submit(runtime.context, request)
'''
ENDPOINT = '''from fastapi import APIRouter
from app.agent.context import RuntimeContext
from app.application.leave_service import submit
router = APIRouter()
@router.post("/api/leave")
async def submit_leave(request: str, context: RuntimeContext):
    return await submit(context, request)
'''


def expected(module: str, configuration: dict) -> dict:
    """构造与当前编译器一致的模块验收事实。"""

    return {"agent_id": "leave", "agent_count": 1, "module": module, "configuration": configuration,
            "tools_configuration": {"enabled": False, "bindings": []},
            "tool_endpoints": [{"api_contract_id": "leave_api", "endpoint_id": "submit", "method": "POST", "path": "/api/leave"}]}


class RuntimeGapClosureTests(unittest.TestCase):
    """覆盖写操作确认、实际资源挂载、修复边界及只读健康身份。"""

    def tool_result(self, source: str) -> dict:
        """使用真实工厂、工具与 Controller AST 检查完整服务委托。"""

        return verify_agent_module({
            "agent-runtime/src/app/agent/factory.py": FACTORY.replace("TOOLS", "[submit_leave]").replace("MIDDLEWARE", "[]"),
            "agent-runtime/src/app/tools/leave.py": source,
            "agent-runtime/src/app/api/leave.py": ENDPOINT,
        }, expected("tools", {"enabled": True, "bindings": [{"toolId": "submit_leave", "apiContractId": "leave_api", "endpointId": "submit"}]}))

    def test_agent_frontend_instructions_do_not_change_java_generation(self):
        """Java 不消费 Runtime 字段；Runtime 仅为当前绑定页面提供正式 AG-UI 会话规则。"""

        from app.agents.frontend.runtime_instruction import runtime_frontend_instruction
        self.assertEqual(runtime_frontend_instruction({"agent_contracts": "不属于 Java 的字段"}, []), "")
        plan = {"topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
                             "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"},
                "pages": [{"pageId": "leave_page"}], "agent_contracts": [{"agentId": "leave", "name": "请假助手", "purpose": "协助请假",
                "pageIds": ["leave_page"], "agentSettings": {"prompt": {"systemPrompt": "请假助手"}, "model": {"source": "project"},
                "memory": {}, "context": {}, "tools": {"enabled": False, "bindings": []}, "skills": {"enabled": False}, "knowledge": {"enabled": False}}}]}
        instruction = runtime_frontend_instruction(plan, [{"unit_id": "page:leave_page"}])
        self.assertIn("/agents/leave/run", instruction)
        self.assertIn("interactionResponse", instruction)
        self.assertIn("@ag-ui/client", instruction)
        self.assertEqual(runtime_frontend_instruction(plan, [{"unit_id": "page:another"}]), "")

    def test_write_tool_passes_only_with_bound_confirmation(self):
        """正确写工具可通过，旁路、宽松确认、参数替换和自报身份均不能通过。"""

        self.assertEqual(self.tool_result(TOOL)["status"], "passed")
        changes = [TOOL.replace("approved is not True", "not approved"),
                   TOOL.replace('"request": request', '"request": "不同参数"'),
                   TOOL.replace('    approved = interrupt', '    await submit(runtime.context, request)\n    approved = interrupt'),
                   TOOL.replace("runtime: ToolRuntime[RuntimeContext]", "runtime: RuntimeContext"),
                   TOOL.replace('raise ValueError("操作未确认")', 'raise submit(runtime.context, request)'),
                   TOOL.replace('    if approved is not True:\n        raise ValueError("操作未确认")', '    pass')]
        for source in changes:
            with self.subTest(source=source):
                self.assertNotEqual(self.tool_result(source)["status"], "passed")

    def test_local_resources_require_actual_middleware_and_delivered_files(self):
        """已交付资源与精确独立 backend 实际挂载才通过，关闭开关不能保留挂载。"""

        for module, middleware, path, sources, content in [
            ("skills", "SkillsMiddleware", "agent-runtime/src/app/skills/hr/leave/SKILL.md", "['/hr']", "---\nname: leave\ndescription: 请假帮助\n---\n请假流程"),
            ("knowledge", "MemoryMiddleware", "agent-runtime/src/app/knowledge/rules.md", "['/rules.md']", "# 请假规定\n按规定提交申请"),
        ]:
            with self.subTest(module=module):
                expression = f"[{middleware}(backend=FilesystemBackend(root_dir=Path(__file__).resolve().parents[1] / '{module}', virtual_mode=True), sources={sources})]"
                files = {"agent-runtime/src/app/agent/factory.py": FACTORY.replace("TOOLS", "[]").replace("MIDDLEWARE", expression), path: content}
                config = {"enabled": True, "files": [path]}
                self.assertEqual(verify_agent_module(files, expected(module, config))["status"], "passed")
                self.assertEqual(verify_agent_module({key: value for key, value in files.items() if key != path}, expected(module, config))["status"], "failed")
                self.assertEqual(verify_agent_module(files, expected(module, {"enabled": False}))["status"], "failed")
                unsafe = {**files, "agent-runtime/src/app/agent/factory.py": files["agent-runtime/src/app/agent/factory.py"].replace("virtual_mode=True", "virtual_mode=False")}
                self.assertEqual(verify_agent_module(unsafe, expected(module, config))["status"], "blocked")

    def test_resource_contract_rejects_hidden_external_and_unbounded_paths(self):
        """正式资源不能读取配置、符号 glob、外部地址或声明不存在的动态检索模式。"""

        for path in ["/etc/passwd", "agent-runtime/src/app/knowledge/../settings.py", "https://host/data.md", "agent-runtime/src/app/knowledge/*.md"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                agent_resource_files("knowledge", {"enabled": True, "files": [path]})

    def test_runtime_repair_never_authorizes_java_or_template_infrastructure(self):
        """只定位 Runtime 业务末帧；Java、依赖、受保护模板以及环境错误保持拒绝。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            business = root / "agent-runtime/src/app/application/leave.py"
            business.parent.mkdir(parents=True)
            business.write_text("broken")
            log = f'  File "{business}", line 2\nSyntaxError: invalid syntax'
            self.assertEqual(runtime_repair_candidates(root, log), [business.relative_to(root).as_posix()])
            self.assertEqual(runtime_failure_scope(root, {"failedStage": "backend_start"}, {"agent_runtime": [{"content": log}]}), [])
            self.assertEqual(runtime_failure_scope(root, {"failedStage": "agent_runtime_install"}, {"agent_runtime": [{"content": log}]}), [])
            for path in [root / "backend/App.java", root / "agent-runtime/src/app/settings.py", root / "agent-runtime/.venv/library.py"]:
                self.assertEqual(runtime_repair_candidates(root, f'File "{path}", line 1\nTypeError: broken'), [])

    def test_python_overwrite_is_limited_to_exact_existing_small_task_file(self):
        """Runtime 修复能用 write 覆盖既有业务文件，原全局 Python 脚本禁写仍生效。"""

        from app.agents.auto_dedup_backend import AutoDedupFilesystemBackend
        from app.agents.small_task.scope import small_task_path_scope
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "agent-runtime/src/app/application/leave.py"
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_text("before = 1")
            backend = AutoDedupFilesystemBackend(root_dir=root, virtual_mode=True)
            with patch("app.agents.auto_dedup_backend.report_inclusion_rate"):
                self.assertIsNotNone(backend.write('/' + name, "after = 1").error)
                with small_task_path_scope([name]):
                    self.assertIsNone(backend.write('/' + name, "after = 1").error)
                    self.assertIsNotNone(backend.write('/agent-runtime/src/app/application/new.py', 'new = 1').error)
                    self.assertIsNotNone(backend.write('/backend/tmp.py', 'new = 1').error)
            self.assertEqual(path.read_text(), "after = 1")

    def test_health_requires_formal_agents_and_current_profile(self):
        """通用 chat 不能冒充正式业务 Agent，Debug 健康不能被普通预览复用。"""

        payload = {"service": "agent-runtime", "launchId": "current", "profile": "preview", "agentIds": ["leave"],
                   "status": "ok", "checks": {"businessApi": "ready", "agUi": "ready", "anonymousSession": "ready", "database": "not_required"}}
        response = Mock(status_code=200)
        response.json.return_value = payload
        with patch("app.services.agent_runtime_health.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            self.assertTrue(runtime_health("http://127.0.0.1:1234", "current", database_required=False, expected_agent_ids=["leave"])[0])
            payload["agentIds"] = ["chat"]
            self.assertFalse(runtime_health("http://127.0.0.1:1234", "current", database_required=False, expected_agent_ids=["leave"])[0])
            payload["agentIds"], payload["profile"] = ["leave"], "debug"
            self.assertFalse(runtime_health("http://127.0.0.1:1234", "current", database_required=False, expected_agent_ids=["leave"])[0])

    def test_debug_credential_is_absent_from_snapshots_and_run_result(self):
        """临时凭据只出现一次 CUSTOM，不进入公共 AG-UI 快照、消息或持久结果。"""

        async def collect():
            """消费官方编码器输出，核对一次性能力事件的隔离。"""
            async def operation():
                """签发测试能力，不启动进程或访问外部服务。"""
                return AgUiActionResult(data={"debug": {"active": True}}, message="完成", transient_events=(("credential", {"token": "test-secret"}),))
            return [frame async for frame in build_ag_ui_action_stream(
                payload={"threadId": "t", "runId": "r"}, event_name="test", state_key="test",
                run_id_prefix="test", operation=operation, error_message_prefix="失败")]
        events = [json.loads(frame.removeprefix("data: ").strip()) for frame in asyncio.run(collect())]
        containing = [event for event in events if "test-secret" in json.dumps(event)]
        self.assertEqual(len(containing), 1)
        self.assertEqual(containing[0]["type"], "CUSTOM")
        self.assertEqual(containing[0]["name"], "credential")

    def test_workbench_list_and_validation_failures_both_finish_agui_lifecycle(self):
        """只读 Java 返回空 Agent，非法身份字段仍发出完整失败生命周期，不启动服务。"""

        from app.protocols.agent_runtime_workbench import build_runtime_workbench_stream
        async def collect(extra):
            """消费实际管理协议的只读或边界拒绝结果。"""
            payload = {"threadId": "workbench", "runId": "r", "forwardedProps": {"agentRuntimeWorkbench": {
                "action": "list", "workspaceRoot": "/workspace", **extra}}}
            return [json.loads(frame.removeprefix("data: ").strip()) async for frame in build_runtime_workbench_stream(payload=payload)]
        with patch("app.protocols.agent_runtime_workbench.runtime_agents", return_value={"agents": [], "runtimeEnabled": False}) as read:
            for fields, status in [({}, "completed"), ({"principal": "自报身份"}, "failed")]:
                events = asyncio.run(collect(fields))
                self.assertEqual(events[0]["type"], "RUN_STARTED")
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                self.assertEqual(events[-1]["result"]["agentRuntimeWorkbench"]["status"], status)
            self.assertEqual(read.call_count, 1)

    def test_debug_stop_keeps_preview_but_preview_stop_revokes_both(self):
        """独立调试不会抢占普通预览；标准停机必须同时撤销调试进程。"""

        from app.services.agent_runtime_process import register_runtime, runtime_directory, stop_runtime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile in ("preview", "debug"):
                runtime_directory(root, profile=profile).mkdir(parents=True)
            preview, debug = Mock(pid=10001), Mock(pid=10002)
            with patch.dict("app.services.agent_runtime_process._processes", {}, clear=True), \
                    patch("app.services.agent_runtime_process.terminate_runtime_process") as terminate:
                register_runtime(root, preview)
                register_runtime(root, debug, profile="debug")
                self.assertEqual(stop_runtime(root, profile="debug")["status"], "stopped")
                terminate.assert_called_once_with(root, debug)
                self.assertTrue((runtime_directory(root) / "agent-runtime.pid").exists())
                register_runtime(root, debug, profile="debug")
                terminate.reset_mock()
                self.assertEqual(stop_runtime(root)["status"], "stopped")
                self.assertEqual(terminate.call_args_list, [call(root, debug), call(root, preview)])
                self.assertFalse((runtime_directory(root) / "agent-runtime.pid").exists())
