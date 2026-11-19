"""覆盖真实前端 Build Agent 的四步状态、工具限制和 AST 工程验收。"""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.execution_scope import FrontendTaskExecutionScope
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.frontend.task_packet import API, PAGE, SERVICE
from app.services.build_scheduler import verify_task_file_changes
from app.services.frontend_request_acceptance import page_request_errors
from tests.test_build_observability import ToolCapableTestModel
from tests.test_frontend_project_plan_context import _api_task, _plan


_PAGE = "/frontend/src/pages/Orders/index.tsx"
_API = "/frontend/src/apis/ordersApi.ts"
_DESIGN = "/.devagentstudio/ui-design/pages/Orders/index.tsx"
_OTHER = "/frontend/src/pages/Other/index.tsx"
_DELETED = "/frontend/src/pages/Orders/Obsolete.tsx"
_SKILL = "/.devagentstudio/builtin-skills/frontend-engineering-standards/SKILL.md"
_PERMISSION = "/frontend/src/components/authorization/Permission/index.tsx"
_RESOURCES = "/frontend/src/constants/resources.ts"
_TYPE = "/frontend/src/typings/orders.ts"
_CONSTANT = "/frontend/src/constants/orders.ts"


def _packet(kind: str) -> dict:
    """构造两个不同职责的正式工具授权。"""
    path = _API if kind == API else _PAGE
    scope = [{"path": path, "operation": "add"}]
    if kind == PAGE:
        scope.append({"path": _DELETED, "operation": "delete"})
    return {"id": kind, "unit_id": kind, "kind": kind,
            "target_files": [item["path"] for item in scope], "change_scope": scope,
            "implementation_contract": ({"confirmed_design": _DESIGN, "page_entry": _PAGE,
                    "api_dependencies": [{"path": _API, "kind": API, "endpoints": []}],
                    "template_read_dependencies": [_PERMISSION, _RESOURCES],
                    "type_and_constant_files": [_TYPE, _CONSTANT]} if kind == PAGE else {})}


def _create_file(root: Path, path: str, source: str) -> Path:
    """创建测试工程中的普通文件。"""
    real = root / path.lstrip("/")
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(source, encoding="utf-8")
    return real


class FrontendBuildExecutionTests(unittest.TestCase):
    """通过真实 DeepAgents sync 和 async 工具入口验证边界。"""

    def test_template_dependencies_never_expand_writes_or_api_reads(self):
        """四步均拒绝受管写入，业务 API 仍仅在原定步骤可读。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for path in (_PERMISSION, _RESOURCES, _TYPE, _CONSTANT, _API, _PAGE, _DELETED, _DESIGN):
                _create_file(root, path, "unchanged")
            packet = _packet(PAGE)
            scope = FrontendTaskExecutionScope(folder, [packet])
            for step in range(1, 5):
                scope.step = step
                for path in (_PERMISSION, _RESOURCES, _TYPE, _CONSTANT):
                    self.assertNotIn(path, scope.writable())
                    for operation in ("write_file", "edit_file", "delete_file"):
                        with self.assertRaises(ValueError):
                            scope.authorize(operation, path)
                    if step in {1, 3}:
                        self.assertEqual(scope.authorize("read_file", path)[0], path)
                    else:
                        with self.assertRaises(ValueError):
                            scope.authorize("read_file", path)
                    if step == 1:
                        scope.authorize("grep", path)
                    else:
                        with self.assertRaises(ValueError):
                            scope.authorize("grep", path)
                self.assertEqual(scope.writable(), {} if step in {1, 4} else
                                 {_PAGE: "add", _DELETED: "delete"})
                if step in {1, 4}:
                    for path in packet["target_files"]:
                        for operation in ("write_file", "edit_file", "delete_file"):
                            with self.assertRaises(ValueError):
                                scope.authorize(operation, path)
                self.assertEqual(_DESIGN in scope.readable(), step in {1, 2})
                if step == 2:
                    with self.assertRaises(ValueError):
                        scope.authorize("read_file", _API)
                else:
                    self.assertEqual(scope.authorize("read_file", _API)[0], _API)

    def test_ordered_tools_and_permissions(self):
        """夹带并发切换、目录工具、跨步写入和 Shell 均无法执行。"""
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous), TemporaryDirectory() as folder:
                self._replay(Path(folder), asynchronous)

    def _replay(self, root: Path, asynchronous: bool):
        """脚本化模型仅驱动工具，真正的后端执行读写和权限检查。"""
        _create_file(root, _DESIGN, "// confirmed design")
        _create_file(root, SERVICE, "export const service = 1;")
        _create_file(root, _API, "export const list = () => [];")
        _create_file(root, "/.devagentstudio/ui-design/pages/Other/index.tsx", "// unrelated design")
        _create_file(root, _OTHER, "// unrelated")
        dependencies = {_PERMISSION: "// permission actual source", _RESOURCES: "// resources actual source",
                        _TYPE: "export type Order = { id: string };",
                        _CONSTANT: "export const ORDER_STATUS = 'pending';"}
        for path, source in dependencies.items():
            _create_file(root, path, source)
        other_component = "/frontend/src/components/authorization/Other.tsx"
        _create_file(root, other_component, "// undeclared managed component")
        deleted = _create_file(root, _DELETED, "// approved delete")
        _create_file(root, "/.devagentstudio/template-state.json", "private")
        responses = []
        call_id = 0

        def turn(*calls):
            """以一个模型消息提交若干可并发的工具调用。"""
            nonlocal call_id
            items = []
            for name, args in calls:
                call_id += 1
                items.append({"name": name, "args": args, "id": str(call_id)})
            responses.append(AIMessage(content="", tool_calls=items))

        def path_call(name, path, **kwargs):
            """构造精确文件工具参数。"""
            return name, {"file_path": path, **kwargs}

        def advance(kind):
            """单独提交一步完成记录。"""
            turn(("advance_frontend_step", {"task_id": kind, "completion_evidence": "checked"}))

        turn(path_call("read_file", SERVICE))
        turn(("begin_frontend_task", {"task_id": API}))
        turn(("begin_frontend_task", {"task_id": PAGE}))
        for path in dependencies:
            turn(path_call("read_file", path))
        turn(("code_graph_context", {"paths": list(dependencies)}))
        turn(path_call("read_file", _SKILL), ("execute", {"command": "printf scope-check"}))
        turn(("advance_frontend_step", {"task_id": PAGE, "completion_evidence": "checked"}),
             path_call("write_file", _PAGE, content="forbidden"))
        turn(path_call("read_file", _DESIGN), path_call("read_file", SERVICE),
             path_call("read_file", "/.devagentstudio/ui-design/pages/Other/index.tsx"),
             path_call("read_file", "/.devagentstudio/template-state.json"))
        turn(("code_graph_context", {"paths": [_API]}), path_call("read_file", _API))
        turn(("code_graph_context", {"paths": [_API]}),
             ("grep", {"pattern": "export", "path": _API}),
             ("ls", {"path": "/frontend/src"}), ("glob", {"pattern": "**/*.ts", "path": "/frontend"}))
        turn(path_call("read_file", _OTHER), path_call("read_file", _API.upper()),
             path_call("read_file", "frontend/src/apis/ordersApi.ts"),
             path_call("read_file", "/frontend/src/apis/../apis/ordersApi.ts"),
             path_call("read_file", str(root / _API[1:])))
        advance(PAGE)
        for path in dependencies:
            turn(path_call("read_file", path))
        page_content = "export default function Orders() { return null; }"
        turn(path_call("write_file", _PAGE, content=page_content),
             path_call("delete_file", _API))
        turn(path_call("read_file", _PAGE))
        turn(path_call("write_file", _PAGE, content=page_content),
             path_call("edit_file", _PAGE, old_string="return null;", new_string="return null;"))
        advance(PAGE)
        for path in dependencies:
            turn(path_call("read_file", path))
            turn(path_call("write_file", path, content="forbidden"),
                 path_call("edit_file", path, old_string="actual", new_string="forbidden"),
                 path_call("delete_file", path))
        turn(path_call("read_file", other_component),
             ("grep", {"pattern": "source", "path": "/frontend/src/components"}),
             ("glob", {"pattern": "**/*.tsx", "path": "/frontend/src/components"}))
        turn(path_call("delete_file", _DELETED), path_call("read_file", _API))
        advance(PAGE)
        for path in dependencies:
            turn(path_call("read_file", path))
        turn(path_call("delete_file", _PAGE), path_call("read_file", _API))
        turn(("complete_frontend_task", {"task_id": PAGE, "status": "completed"}))
        turn(path_call("read_file", _PAGE))
        responses.append(AIMessage(content="done"))
        trace = Mock()
        scope = FrontendTaskExecutionScope(str(root), [_packet(PAGE)], trace)
        agent = create_frontend_agent(
            ToolCapableTestModel(responses=responses), str(root),
            user_skills_backend=StateBackend(), agent_memory_backend=StateBackend(), build_scope=scope,
        )
        payload = {"messages": [{"role": "user", "content": "Run the scripted tasks."}]}
        with patch("app.tools.execute.workspace_process_registry.run") as execute:
            result = asyncio.run(agent.ainvoke(payload)) if asynchronous else agent.invoke(payload)
        self.assertFalse(any(call.kwargs.get("shell") for call in execute.call_args_list))
        tools = {message.tool_call_id: message for message in result["messages"]
                 if isinstance(message, ToolMessage)}
        tool_text = "\n".join(str(message.content) for message in tools.values())
        self.assertIn("confirmed design", tool_text)
        self.assertIn("FE-API-BOUNDARY", tool_text)
        self.assertNotIn("export const service", tool_text)
        self.assertEqual(sum(message.additional_kwargs.get("no_change", False)
                             for message in tools.values()), 2)
        self.assertIn("已经查询", tool_text)
        self.assertIn("export const list", tool_text)
        self.assertNotIn("private", tool_text)
        self.assertNotIn("unrelated design", tool_text)
        self.assertNotIn("undeclared managed component", tool_text)
        for path, source in dependencies.items():
            self.assertEqual((root / path[1:]).read_text(), source)
            self.assertIn(source, tool_text)
            reads = [event.kwargs for event in trace.call_args_list
                     if event.args == ("frontend_tool_permission",)
                     and event.kwargs.get("virtual_path") == path
                     and event.kwargs.get("tool_name") == "read_file"]
            self.assertEqual([(event["step_id"], event["permission_decision"]) for event in reads],
                             [("step_1", "allowed"), ("step_2", "denied"),
                              ("step_3", "allowed"), ("step_4", "denied")])
        receipts = [message for message in tools.values() if message.name == "advance_frontend_step"]
        graph_events = [event.kwargs for event in trace.call_args_list
                        if event.args == ("frontend_tool_permission",)
                        and event.kwargs.get("tool_name") == "code_graph_context"]
        self.assertTrue(any(event.get("permission_decision") == "allowed"
                            and set(event.get("code_graph_query_scope", [])) == set(dependencies)
                            for event in graph_events))
        self.assertTrue(any(_PERMISSION in str(message.content) and _RESOURCES in str(message.content)
                            and "step_3" in str(message.content) for message in receipts))
        self.assertFalse(deleted.exists())
        self.assertEqual((root / _API[1:]).read_text(), "export const list = () => [];")
        self.assertEqual((root / SERVICE[1:]).read_text(), "export const service = 1;")
        self.assertEqual(scope.outcomes, {PAGE: "completed"})
        self.assertEqual(sum(event.args == ("frontend_step_transition",)
                             for event in trace.call_args_list), 5)
        self.assertTrue(any(event.kwargs.get("duplicate_read") for event in trace.call_args_list))

    def test_physical_aliases_are_rejected(self):
        """已声明文件也不得通过符号链接或硬链接改变物理访问边界。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            outside = _create_file(root, _OTHER, "other")
            target = root / _PAGE.lstrip("/")
            target.parent.mkdir(parents=True)
            scope = FrontendTaskExecutionScope(folder, [_packet(PAGE)])
            scope.transition("begin_frontend_task", {"task_id": PAGE})
            target.symlink_to(outside)
            with self.assertRaises(ValueError):
                scope.authorize("read_file", _PAGE)
            target.unlink()
            os.link(outside, target)
            with self.assertRaises(ValueError):
                scope.authorize("read_file", _PAGE)

    def test_api_result_without_step_receipt_and_static_dispatch(self):
        """API 接受既有结构化候选，静态任务仍由原派发处理，最终验收留给 Build。"""
        with TemporaryDirectory() as folder:
            plan = _plan()
            task = _api_task(plan)
            target = "frontend/src/apis/exampleApi.ts"
            task.update(target_files=[target], allowed_paths=[target])
            static = {"id": "static", "owner": "frontend", "unit_id": "frontend:data:static",
                      "deliverables": [{"kind": "frontend.static_data_module"}]}
            with patch("app.agents.frontend.generator._invoke_live_frontend_agent", side_effect=[
                '{"task_results":[{"task_id":"shared-api","status":"completed","summary":"claim"}]}',
                '{"task_results":[{"task_id":"static","status":"completed","summary":"done"}]}',
            ]) as invoke:
                reports = generate_frontend_with_deep_agent(
                    project_plan=plan, build_task_plan={}, tasks=[task, static], workspace=folder,
                )
            self.assertEqual([report["status"] for report in reports], ["completed", "completed"])
            self.assertIsNotNone(invoke.call_args_list[0].kwargs["scope"])
            self.assertIsNone(invoke.call_args_list[1].kwargs["scope"])


class FrontendRequestAcceptanceTests(unittest.TestCase):
    """区分真实导入绑定、任务内 Hooks、UI request 和 mutation。"""

    def test_query_and_component_patterns(self):
        """页面或可达 Hook 管理查询正常通过，直连和 request 违规阻断。"""
        page, api = _PAGE[1:], _API[1:]
        imports = """
import { listOrders as list, saveOrder } from '@/apis/ordersApi';
import { useRequest as requestState } from 'ahooks';
import { ProTable as OrdersTable, ProFormSelect } from '@ant-design/pro-components';
"""
        symbols = {api: {"listOrders"}}
        cases = [
            ("requestState(() => list()); return <OrdersTable dataSource={[]} loading={false} />;", None),
            ("const run = () => list(); requestState(run); return null;", None),
            ("const run = () => list(); requestState(run); run(); return null;", "FE-QUERY-USE-REQUEST"),
            ("return <OrdersTable request={list} />;", "FE-NO-UI-REQUEST-PROP"),
            ("return <ProFormSelect request={async () => list()} />;", "FE-NO-UI-REQUEST-PROP"),
            ("list(); return null;", "FE-QUERY-USE-REQUEST"),
            ("return <button onClick={() => saveOrder()}/>;", None),
            ("return <ProFormSelect options={[{label:'A',value:'a'}]} />;", None),
            ("const documentation = 'fetch('; return null;", None),
            ("function fetch() { return true; } fetch(); return null;", None),
            ("fetch('/x'); return null;", "FE-NO-DIRECT-HTTP"),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                code = imports + f"export default function Page() {{ {body} }}"
                errors = page_request_errors({page: code}, symbols)
                if expected:
                    self.assertTrue(any(expected in item for item in errors), errors)
                else:
                    self.assertEqual(errors, [])
        hook = "frontend/src/hooks/useOrders.ts"
        hook_source = (
            "import { useRequest } from 'ahooks'; import { listOrders } from '@/apis/ordersApi'; "
            "export function useOrders() { return useRequest(() => listOrders()); }"
        )
        self.assertEqual(page_request_errors(
            {page: "import { useOrders } from '@/hooks/useOrders'; export default function Page() { "
                   "useOrders(); return null; }", hook: hook_source}, symbols,
        ), [])
        self.assertTrue(page_request_errors({
            page: "import http from 'axios'; http.get('/x');"
        }, {}))

    def test_normal_scheduler_runs_scope_specific_check(self):
        """普通 Build 的前端验收可阻断绕过 useRequest 的已知查询。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            api = _API.lstrip("/")
            page = _PAGE.lstrip("/")
            _create_file(root, _API,
                         "import service from './service'; "
                         "export const listOrders = () => service.get('/orders');")
            _create_file(root, _PAGE,
                         "import { listOrders } from '@/apis/ordersApi'; export default function Page() { "
                         "listOrders(); return null; }")
            dependency = {"id": "api", "owner": "frontend", "status": "completed",
                          "source_refs": {"api_contract_ids": ["orders-api"]},
                          "deliverables": [{"kind": API, "paths": [api], "target_id": "list"}]}
            plan = {"task_registry": {"api": dependency}, "task_graph": {"nodes": ["api"]}}
            task = {"id": "page", "owner": "frontend", "unit_id": "page:orders", "dependencies": ["api"],
                    "deliverables": [{"kind": PAGE, "paths": [page]}],
                    "target_files": [page], "allowed_paths": [page],
                    "business_acceptance_checks": [{"kind": "frontend.page_endpoint_usage",
                       "expected": {"endpoints": [{"method": "GET", "path": "/orders", "endpoint_id": "list",
                                                  "api_contract_id": "orders-api"}]}}]}
            result = verify_task_file_changes(
                results=[{"task_id": "page", "status": "completed", "changed_files": [page]}],
                code_change_set=None, tasks=[task], workspace_root=folder,
                authorization_checks_only=True, frontend_build_task_plan=plan,
            )[0]
            self.assertEqual(result["status"], "failed", result)
            self.assertIn("FE-QUERY-USE-REQUEST", result["failure_reason"])
            _create_file(root, _API, "export const listOrders = () => [];")
            unknown = verify_task_file_changes(
                results=[{"task_id": "page", "status": "completed", "changed_files": [page]}],
                code_change_set=None, tasks=[task], workspace_root=folder,
                authorization_checks_only=True, frontend_build_task_plan=plan,
            )[0]
            self.assertEqual(unknown["status"], "completed")
            self.assertEqual(unknown["acceptance_evidence"][0]["status"], "not_evaluable")
