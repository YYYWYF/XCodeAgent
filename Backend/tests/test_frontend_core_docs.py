"""通过真实 Agent 工具验证核心文档读取去重与任务隔离。"""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from deepagents.backends import StateBackend
from deepagents.backends.protocol import ReadResult
from langchain_core.messages import AIMessage, ToolMessage
from langsmith import tracing_context

from app.agents.frontend.agent import create_frontend_agent, FRONTEND_BUILD_BUILTIN_SKILLS
from app.agents.frontend.execution_scope import (
    FRONTEND_ENGINEERING_DOC as SKILL, FRONTEND_TEMPLATE_AGENTS_DOC as AGENTS,
    FrontendTaskExecutionScope,
)
from app.agents.frontend.file_backend import FrontendScopedFilesystemBackend
from app.agents.frontend.execution_middleware import _core_document_read_error
from app.agents.frontend.task_packet import API, PAGE
from tests.test_build_observability import ToolCapableTestModel
from tests.test_frontend_build_execution import _create_file, _packet


def _call(name, identity, **args):
    """构造真实工具调用及可追踪的消息 ID。"""
    return {"name": name, "id": identity, "args": args}


def _read(path, identity, **args):
    """请求单次完整文档，允许用例覆盖分页参数。"""
    return _call("read_file", identity, file_path=path, limit=2000, **args)


class FrontendCoreDocsTests(unittest.TestCase):
    """保持生产 Agent、文件后端与中间件，只脚本化模型输出。"""

    def setUp(self):
        """建立真实模板文档和独立工作区，避免外部追踪网络调用。"""
        self.enterContext(tracing_context(enabled=False))
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.document = _create_file(self.root, AGENTS, "# 当前模板\n模板能力与归属说明\n")

    def _run(self, scope, turns, asynchronous=False):
        """驱动工具返回并按原调用 ID 收集证据。"""
        responses = [AIMessage(content="", tool_calls=calls) for calls in turns]
        responses.append(AIMessage(content="done"))
        agent = create_frontend_agent(
            ToolCapableTestModel(responses=responses), str(self.root),
            user_skills_backend=StateBackend(), agent_memory_backend=StateBackend(), build_scope=scope,
        )
        payload = {"messages": [{"role": "user", "content": "执行当前任务"}]}
        result = asyncio.run(agent.ainvoke(payload)) if asynchronous else agent.invoke(payload)
        return {message.tool_call_id: message for message in result["messages"]
                if isinstance(message, ToolMessage)}

    def test_api_parallel_duplicates_and_read_only(self):
        """同步和异步并发批次均只交付一次正文，写删搜索及旧 Skill 不可用。"""
        self.assertEqual(FRONTEND_BUILD_BUILTIN_SKILLS, ("frontend-engineering-standards",))
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                trace = Mock()
                scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)], trace)
                turns = [[_read(path, f"{i}-{j}") for i, path in enumerate((SKILL, AGENTS))
                          for j in range(2)], [
                    _call("write_file", "write", file_path=AGENTS, content="overwrite"),
                    _call("edit_file", "edit", file_path=AGENTS, old_string="模板", new_string="changed"),
                    _call("delete_file", "delete", file_path=AGENTS),
                    _call("grep", "grep", path=AGENTS, pattern="模板"),
                    _read("/.devagentstudio/builtin-skills/frontend-template-modification-boundary/SKILL.md", "old"),
                    _read("/.devagentstudio/builtin-skills/frontend-engineering-standards/references/api-request-patterns.md", "example"),
                ]]
                with scope.task_context():
                    tools = self._run(scope, turns, asynchronous)
                    self.assertEqual(len(scope.core_docs_read), 2)
                self.assertEqual(scope.core_docs_read, set())
                for i in range(2):
                    messages = [tools[f"{i}-{j}"] for j in range(2)]
                    self.assertCountEqual([message.status for message in messages], ["success", "error"])
                    self.assertTrue(any("本任务已读取该规范" in message.content for message in messages))
                for identity in ("write", "edit", "delete", "grep", "old"):
                    self.assertEqual(tools[identity].status, "error", tools[identity])
                self.assertEqual(tools["example"].status, "success")
                self.assertIn("当前模板", tools["1-0"].content + tools["1-1"].content)
                self.assertEqual(self.document.read_text(), "# 当前模板\n模板能力与归属说明\n")
                decisions = [event.kwargs for event in trace.call_args_list
                             if event.args == ("frontend_tool_permission",)]
                for path in (SKILL, AGENTS):
                    self.assertEqual(sum(event["virtual_path"] == path and
                                         event["permission_decision"] == "allowed" for event in decisions), 1)

    def test_page_steps_and_task_isolation(self):
        """实际步骤工具激活页面，跨步骤拒读且下一页面可首次读取。"""
        packets = [{**_packet(PAGE), "id": identity} for identity in ("A", "B")]
        scope = FrontendTaskExecutionScope(str(self.root), packets, Mock())
        turns = [[_read(AGENTS, "before")]]
        for task in ("A", "B"):
            turns.append([_call("begin_frontend_task", f"begin-{task}", task_id=task)])
            turns.append([_read(path, f"{task}-{i}") for i, path in enumerate((SKILL, AGENTS))])
            for step in range(2, 5):
                turns.append([_call("advance_frontend_step", f"advance-{task}-{step}",
                                    task_id=task, completion_evidence="已核对", defer_to_repair=True)])
                turns.append([_read(path, f"{task}-{step}-{i}") for i, path in enumerate((SKILL, AGENTS))])
            turns.append([_call("complete_frontend_task", f"complete-{task}", task_id=task, status="failed")])
        turns.append([_read(AGENTS, "after")])
        tools = self._run(scope, turns)
        for task in ("A", "B"):
            for i in range(2):
                self.assertEqual(tools[f"{task}-{i}"].status, "success")
                for step in range(2, 5):
                    self.assertEqual(tools[f"{task}-{step}-{i}"].status, "error")
                    self.assertIn("本任务已读取该规范", tools[f"{task}-{step}-{i}"].content)
        self.assertEqual(tools["before"].status, "error")
        self.assertEqual(tools["after"].status, "error")
        self.assertEqual(scope.outcomes, {"A": "failed", "B": "failed"})

    def test_page_reads_only_requested_example_range_without_expanding_permissions(self):
        """复杂页面只读取必要示例范围，原业务文件与旧 Skill 边界保持不变。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(PAGE)])
        reference = SKILL.removesuffix("SKILL.md") + "references/component-patterns.md"
        from app.services.builtin_skills import resolve_builtin_skills_root

        source = (resolve_builtin_skills_root() / "frontend-engineering-standards"
                  / "references/component-patterns.md").read_text(encoding="utf-8")
        offset = source[:source.index("## ModalForm")].count("\n")
        tools = self._run(scope, [
            [_call("begin_frontend_task", "begin", task_id=PAGE)],
            [_read(SKILL, "core"), _read(AGENTS, "agents")],
            [_call("read_file", "range", file_path=reference, offset=offset, limit=12)],
            [_call("read_file", "unrelated", file_path="/frontend/src/apis/private.ts"),
             _read(SKILL.replace("frontend-engineering-standards", "code-block-template"), "old")],
        ])
        self.assertEqual(tools["range"].status, "success", tools["range"])
        self.assertIn("ModalForm", tools["range"].content)
        self.assertNotIn("export default function OrderSteps", tools["range"].content)
        self.assertEqual(tools["unrelated"].status, "error")
        self.assertEqual(tools["old"].status, "error")
        self.assertEqual(scope.core_docs_read, {(PAGE, SKILL), (PAGE, AGENTS)})

    def test_missing_and_failed_reads_do_not_count(self):
        """缺失文件和后端错误都不登记，恢复后允许首次成功读取。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
        self.document.unlink()
        with scope.task_context():
            tools = self._run(scope, [[_read(AGENTS, "missing")]])
            self.assertEqual(tools["missing"].status, "error")
            self.assertEqual(scope.core_docs_read, set())
            _create_file(self.root, AGENTS, "# 恢复模板说明\n")
            with patch.object(FrontendScopedFilesystemBackend, "read", return_value=ReadResult(error="读取失败")):
                tools = self._run(scope, [[_read(AGENTS, "failed")]])
            self.assertEqual(tools["failed"].status, "error")
            self.assertEqual(scope.core_docs_read, set())
            tools = self._run(scope, [[_read(AGENTS, "ok")], [_read(AGENTS, "duplicate")]])
            self.assertEqual(tools["ok"].status, "success")
            self.assertEqual(tools["duplicate"].status, "error")

    def test_complete_document_with_wrapped_last_line(self):
        """0.6.12 的 Tab 行号及末行续行能完整交付，不将渲染行数误当成源文件行数。"""
        source = "说明\n" * 1998 + "x" * 6000 + "\n"
        self.document.write_text(source)
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
                with scope.task_context():
                    tools = self._run(scope, [[_read(AGENTS, "complete")],
                                              [_read(AGENTS, "duplicate")]], asynchronous)
                    self.assertEqual(tools["complete"].status, "success", tools["complete"])
                    self.assertIn("1999.1\t" + "x" * 1000, tools["complete"].content)
                    self.assertNotIn("[Output was truncated", tools["complete"].content)
                    self.assertEqual(scope.core_docs_read, {(API, AGENTS)})
                    self.assertEqual(tools["duplicate"].status, "error")
                    self.assertIn("本任务已读取该规范", tools["duplicate"].content)

    def test_partial_and_truncated_results_do_not_count(self):
        """同步异步的分页、满页、输出长度截断及空正文均拒绝登记。"""
        for asynchronous in (False, True):
            scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
            with scope.task_context():
                for source, args, reason in (("one\ntwo\n", {"offset": 1}, "从首行"),
                                     ("one\n" * 2000, {}, "无法确认已到末尾"),
                                     ("one\n" * 2100, {}, "无法确认已到末尾"),
                                     ("x" * 150000, {}, "截断"), ("", {}, "正文为空")):
                    with self.subTest(asynchronous=asynchronous, args=args, length=len(source)):
                        self.document.write_text(source)
                        tools = self._run(scope, [[_read(AGENTS, "incomplete", **args)]], asynchronous)
                        self.assertEqual(tools["incomplete"].status, "error")
                        self.assertIn(reason, tools["incomplete"].content)
                        self.assertIn("tool_error", tools["incomplete"].content)
                        self.assertEqual(scope.core_docs_read, set())

    def test_unrecognized_format_is_not_reported_as_truncation(self):
        """未知显示格式明确报告协议不符，不伪称已发生输出截断。"""
        for content in ("1  text", "@@ lines 1-1 of 1 @@\ntext", "plain text"):
            with self.subTest(content=content):
                error = _core_document_read_error(content, {"offset": 0, "limit": 2000})
                self.assertIn("Tab 行号格式", error)
                self.assertNotIn("截断", error)

    def test_exact_paths_and_physical_aliases(self):
        """大小写、点段、宿主路径以及符号与硬链接不能绕过精确授权。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
        with scope.task_context():
            paths = (AGENTS.lower(), SKILL.replace("SKILL.md", "skill.md"),
                     "/frontend/../frontend/AGENTS.md", str(self.document))
            tools = self._run(scope, [[_read(path, str(i)) for i, path in enumerate(paths)]])
            self.assertTrue(all(message.status == "error" for message in tools.values()))
            other = _create_file(self.root, "/elsewhere.md", "private template")
            self.document.unlink()
            self.document.symlink_to(other)
            tools = self._run(scope, [[_read(AGENTS, "symbolic")]])
            self.assertEqual(tools["symbolic"].status, "error")
            self.document.unlink()
            os.link(other, self.document)
            tools = self._run(scope, [[_read(AGENTS, "hard")]])
            self.assertEqual(tools["hard"].status, "error")
            self.assertEqual(scope.core_docs_read, set())

    def test_unread_page_docs_are_still_step_one_only(self):
        """没有已读标记也不能在后续步骤首次读取，任务外同样无权限。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(PAGE)])
        turns = [[_call("begin_frontend_task", "begin", task_id=PAGE)]]
        for step in range(2, 5):
            turns.append([_call("advance_frontend_step", f"advance-{step}", task_id=PAGE,
                                completion_evidence="已核对", defer_to_repair=True)])
            turns.append([_read(path, f"{step}-{i}") for i, path in enumerate((SKILL, AGENTS))])
        tools = self._run(scope, turns)
        for step in range(2, 5):
            for i in range(2):
                self.assertEqual(tools[f"{step}-{i}"].status, "error")
                self.assertIn("仅允许在 Step 1", tools[f"{step}-{i}"].content)
        self.assertEqual(scope.core_docs_read, set())
        inactive = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
        tools = self._run(inactive, [[_read(SKILL, "outside")]])
        self.assertEqual(tools["outside"].status, "error")
