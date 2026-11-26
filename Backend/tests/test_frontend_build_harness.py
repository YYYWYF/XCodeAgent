"""使用真实前端工厂、工具和摘要链验证收敛及上下文保护。"""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deepagents.backends import StateBackend
from deepagents.backends.protocol import EditResult, WriteResult
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langsmith import tracing_context
from pydantic import Field

from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.context_guard import FrontendContextGuard, CONTEXT_ID
from app.agents.frontend.execution_scope import (
    FrontendTaskExecutionScope, FRONTEND_TEMPLATE_AGENTS_DOC as DOC, FRONTEND_ENGINEERING_DOC as SKILL,
)
from app.agents.frontend.file_backend import FrontendScopedFilesystemBackend
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.frontend.retry_guard import FRONTEND_MAX_RETRIES, FrontendTaskRetryExhausted
from app.agents.frontend.task_packet import API, PAGE
from app.agents.tool_activity_stream import invoke_agent_with_tool_activity
from tests.test_build_observability import ToolCapableTestModel
from tests.test_frontend_build_execution import _packet, _create_file, _API, _PAGE


class CapturingModel(ToolCapableTestModel):
    """保留真正送入模型的消息，模型响应按脚本返回。"""

    inputs: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """统计同步和异步底层模型调用，而非仅观察独立辅助方法。"""
        self.inputs.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def call(name, identity, **args):
    """生成合法模型工具调用。"""
    return {"name": name, "id": identity, "args": args}


def turn(*calls):
    """构造一轮模型工具批次。"""
    return AIMessage(content="", tool_calls=list(calls))


class FrontendBuildHarnessTests(unittest.TestCase):
    """故障仅通过真实工具返回或摘要模型响应注入。"""

    def setUp(self):
        """为每个场景建立独立工作区和无网络追踪上下文。"""
        self.enterContext(tracing_context(enabled=False))
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        _create_file(self.root, DOC, "# 模板规范\n必须使用紫色主题\n")

    def agent(self, scope, responses):
        """仅替换模型，保留生产工厂及全部中间件。"""
        model = CapturingModel(responses=responses, cache=False)
        agent = create_frontend_agent(model, str(self.root), user_skills_backend=StateBackend(),
                                      agent_memory_backend=StateBackend(), build_scope=scope)
        return agent, model

    def invoke(self, agent, mode="sync", messages=None):
        """覆盖同步、异步和生产工具活动流入口。"""
        payload = {"messages": messages or [HumanMessage(content="执行当前任务")]}
        if mode == "async":
            return asyncio.run(agent.ainvoke(payload))
        if mode == "stream":
            return invoke_agent_with_tool_activity(agent, payload, workspace=str(self.root), on_tool_activity=Mock())
        return agent.invoke(payload)

    def test_version_conflict_stops_fourth_failure_and_queued_write(self):
        """write/edit 合并；读取不清零；第四次失败后批次后续修改及模型调用均停止。"""
        for mode in ("sync", "async", "stream"):
            with self.subTest(mode=mode):
                scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)], Mock())
                responses = [turn(call("write_file", "w0", file_path=_API, content="x")),
                             turn(call("read_file", "r", file_path=DOC)),
                             turn(call("edit_file", "w1", file_path=_API, old_string="x", new_string="y")),
                             turn(call("write_file", "w2", file_path=_API, content="x")),
                             turn(call("edit_file", "w3", file_path=_API, old_string="x", new_string="y"),
                                  call("write_file", "queued", file_path=_API, content="after")),
                             AIMessage(content="must never run")]
                agent, model = self.agent(scope, responses)
                error = "文件已变化，请读取当前版本后重试。"
                with scope.task_context(), patch.object(FrontendScopedFilesystemBackend, "write", return_value=WriteResult(error=error)) as write, patch.object(
                    FrontendScopedFilesystemBackend, "edit", return_value=EditResult(error=error)
                ) as edit:
                    with self.assertRaises(FrontendTaskRetryExhausted):
                        self.invoke(agent, mode)
                    self.assertEqual(write.call_count + edit.call_count, FRONTEND_MAX_RETRIES + 1)
                    self.assertEqual(len(model.inputs), 5)
                    counts = [event.kwargs["retry_count"] for event in scope.trace.call_args_list
                              if event.args == ("frontend_tool_failure",)]
                    self.assertEqual(counts, [0, 1, 2, 3])
                self.assertFalse(scope.active)
                self.assertFalse(scope.core_docs_body)

    def test_real_id_feedback_and_recovery(self):
        """模型猜测不同 ID 仍归属同一链，合法 begin 解除 ID 链。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(PAGE)])
        agent, model = self.agent(scope, [turn(call("begin_frontend_task", f"bad{i}", task_id=f"guess{i}"))
                                         for i in range(3)] + [turn(call("begin_frontend_task", "good", task_id=PAGE)),
                                         AIMessage(content="done")])
        result = self.invoke(agent)
        bad = [message for message in result["messages"] if isinstance(message, ToolMessage) and message.status == "error"]
        for i, message in enumerate(bad):
            feedback = json.loads(message.content.split("\n")[-1])
            self.assertEqual((feedback["task_id"], feedback["retry_count"], feedback["task_started"]), (PAGE, i, False))
        self.assertEqual(scope.retry.failures, {})
        self.assertEqual(scope.step, 1)
        self.assertEqual(len(model.inputs), 5)

    def test_independent_paths_categories_and_corresponding_success(self):
        """不同目标和类别预算独立，成功读取不解除写入链，同目标有效写入才解除。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
        guard = scope.retry
        with scope.task_context():
            for path in (_API, DOC):
                for _ in range(3):
                    guard.fail((API, "file_version_conflict", path), "conflict")
            guard.fail(("@call", "context_compression_failed"), "empty")
            request = SimpleNamespace(tool_call=call("read_file", "read", file_path=_API))
            guard.process(request, ToolMessage(content="1\t正文", tool_call_id="read"))
            self.assertEqual(len(guard.failures), 3)
            request.tool_call = call("edit_file", "edit", file_path=_API)
            guard.process(request, ToolMessage(content="success", tool_call_id="edit"))
            self.assertNotIn((API, "file_version_conflict", _API), guard.failures)
            self.assertIn((API, "file_version_conflict", DOC), guard.failures)
            self.assertIn(("@call", "context_compression_failed"), guard.failures)

    def test_compression_retries_use_real_model_and_do_not_commit_invalid_history(self):
        """真实摘要请求的空白和占位不提交，四次失败后无主模型请求。"""
        history = [HumanMessage(content=f"事实 {i}") for i in range(8)]
        for mode in ("sync", "async", "stream"):
            for content in (" ", "<summary> </summary>", "Error generating summary: unavailable"):
                with self.subTest(mode=mode, content=content):
                    scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
                    agent, model = self.agent(scope, [AIMessage(content=content)] * 4 + [AIMessage(content="never")])
                    with scope.task_context(), patch.object(FrontendContextGuard, "_should_summarize", return_value=True), patch.object(
                        FrontendContextGuard, "_determine_cutoff_index", return_value=6
                    ):
                        with self.assertRaises(FrontendTaskRetryExhausted):
                            self.invoke(agent, mode, history)
                        self.assertEqual(len(model.inputs), 4)
                        self.assertTrue(all("事实 0" in str(messages) for messages in model.inputs))
                        self.assertEqual(scope.retry.termination["code"], "context_compression_failed")
                        self.assertEqual(len(history), 8)

    def test_successful_compression_restores_contract_step_and_core_body(self):
        """Step 2 压缩后恢复完整规范与契约，不重复 read 或 begin，保护块只出现一次。"""
        for mode in ("sync", "async"):
            with self.subTest(mode=mode):
                scope = FrontendTaskExecutionScope(str(self.root), [_packet(PAGE)])
                scope.step = 2
                scope.core_docs_read.add((PAGE, DOC))
                scope.core_docs_body[(PAGE, DOC)] = ("1\t必须使用紫色主题", "old-doc")
                scope.retry.fail((PAGE, "file_version_conflict", _PAGE), "existing failure")
                agent, model = self.agent(scope, [AIMessage(content=" "), AIMessage(content="已完成布局，继续当前实现"),
                                                AIMessage(content="done")])
                history = [HumanMessage(content=f"历史 {i}") for i in range(8)]
                with patch.object(FrontendContextGuard, "_should_summarize", return_value=True), patch.object(
                    FrontendContextGuard, "_determine_cutoff_index", return_value=6
                ):
                    result = self.invoke(agent, mode, history)
                self.assertEqual(len(model.inputs), 3)
                system = model.inputs[-1][0]
                blocks = [block for block in system.content if block.get("id") == CONTEXT_ID]
                self.assertEqual(len(blocks), 1)
                state = json.loads(blocks[0]["text"].split("\n", 1)[1])
                self.assertEqual((state["task_id"], state["step"], state["task_started"]), (PAGE, 2, True))
                self.assertEqual(state["implementation_contract"], scope.packets[0]["implementation_contract"])
                self.assertIn("紫色主题", state["core_documents"][DOC])
                self.assertIn((PAGE, "file_version_conflict", _PAGE), scope.retry.failures)
                self.assertNotIn(("@call", "context_compression_failed"), scope.retry.failures)
                self.assertEqual(len(result["messages"]), 9)  # 摘要事件不覆盖原始历史。

    def test_generator_preserves_completed_pages_and_marks_unexecuted(self):
        """真实完成工具之后后续任务耗尽，结果仍通过严格解析且保留前页回执。"""
        packets = [{**_packet(PAGE), "id": identity} for identity in ("A", "B", "C")]
        tasks = [{"id": identity, "owner": "frontend", "deliverables": [{"kind": PAGE}]} for identity in ("A", "B", "C")]
        responses = [turn(call("begin_frontend_task", "a", task_id="A"))]
        responses += [turn(call("advance_frontend_step", f"step{i}", task_id="A", completion_evidence="已实现", defer_to_repair=True)) for i in range(3)]
        responses += [turn(call("complete_frontend_task", "done-a", task_id="A", status="completed"))]
        responses += [turn(call("begin_frontend_task", f"bad{i}", task_id=f"guess{i}")) for i in range(4)]
        captured = []

        def factory(scope):
            """按生成器实际构建的多页作用域创建真实 Agent。"""
            captured.append(scope)
            return self.agent(scope, responses)[0]

        def packet(*args, **kwargs):
            """仅隔离正式任务编译，执行和结果链保持生产实现。"""
            return next(item for item in packets if item["id"] == args[2]["id"])

        _create_file(self.root, _PAGE, "export default function Orders() { return <div />; }")
        with patch("app.agents.frontend.generator.frontend_task_packet", side_effect=packet), patch(
            "app.agents.create_agent_bundle", return_value=SimpleNamespace(frontend_build_factory=factory)
        ):
            results = generate_frontend_with_deep_agent(project_plan={}, build_task_plan={}, tasks=tasks,
                                                        workspace=str(self.root))
        self.assertEqual([result["task_id"] for result in results], ["A", "B", "C"])
        self.assertEqual([result["status"] for result in results], ["completed", "failed", "failed"])
        self.assertEqual(results[1]["failure_category"], "tool_error")
        self.assertEqual(results[2]["failure_category"], "runner_crash")
        self.assertTrue(all(result["executed_by"]["agent"] == "frontend-generation-agent" for result in results))
        self.assertFalse(captured[0].current)

    def test_protected_budget_prevents_main_model_call(self):
        """无法容纳保护内容时直接标准失败，不发送超窗主模型请求。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(API)])
        agent, model = self.agent(scope, [AIMessage(content="never")])
        with scope.task_context(), patch.object(FrontendContextGuard, "_get_profile_limits", return_value=1), patch.object(
            FrontendContextGuard, "_should_summarize", return_value=False
        ):
            with self.assertRaises(FrontendTaskRetryExhausted):
                self.invoke(agent)
        self.assertEqual(model.inputs, [])
        self.assertEqual(scope.retry.termination["code"], "context_budget_exceeded")

    def test_repeated_compaction_reuses_two_real_core_reads(self):
        """实际读取两份规范后连续压缩，正文保留、工具配对合法且不再读文档。"""
        for mode in ("sync", "async"):
            with self.subTest(mode=mode):
                scope = FrontendTaskExecutionScope(str(self.root), [_packet(PAGE)], Mock())
                responses = [turn(call("begin_frontend_task", "begin", task_id=PAGE)),
                             turn(*[call("read_file", f"doc{i}", file_path=path, limit=2000)
                                    for i, path in enumerate((DOC, SKILL))]),
                             turn(call("advance_frontend_step", "advance", task_id=PAGE, completion_evidence="已核对")),
                             AIMessage(content="布局契约和规范已核对"),
                             turn(call("write_file", "write", file_path=_PAGE, content="export default () => <div />")),
                             AIMessage(content="页面结构已实现"), AIMessage(content="done")]
                agent, model = self.agent(scope, responses)

                def should_summarize(guard, messages, tokens):
                    """只在 Step 2 强制真实摘要，不改配对与提交实现。"""
                    return scope.step == 2 and len(messages) > 3

                with patch.object(FrontendContextGuard, "_should_summarize", should_summarize), patch.object(
                    FrontendContextGuard, "_determine_cutoff_index", lambda guard, messages: len(messages) - 2
                ):
                    result = self.invoke(agent, mode)
                self.assertEqual(len(model.inputs), 7)
                tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
                self.assertEqual(sum(message.name == "read_file" for message in tools), 2)
                self.assertEqual(len(scope.core_docs_body), 2)
                for messages in (model.inputs[4], model.inputs[6]):
                    blocks = [block for block in messages[0].content if block.get("id") == CONTEXT_ID]
                    self.assertEqual(len(blocks), 1)
                    state = json.loads(blocks[0]["text"].split("\n", 1)[1])
                    self.assertEqual(state["step"], 2)
                    self.assertEqual(set(state["core_documents"]), {DOC, SKILL})

    def test_structure_rejection_and_ast_warning_have_distinct_semantics(self):
        """结构拒绝只计一次且 defer 可推进；AST 告警不消耗重试。"""
        scope = FrontendTaskExecutionScope(str(self.root), [_packet(PAGE)])
        scope.step = 2
        scope.evidence[PAGE] = ["已核对"]
        responses = [turn(call("advance_frontend_step", "blocked", task_id=PAGE, completion_evidence="检查")),
                     turn(call("advance_frontend_step", "defer", task_id=PAGE, completion_evidence="交给修复", defer_to_repair=True)),
                     AIMessage(content="done")]
        agent, _ = self.agent(scope, responses)
        with patch.object(scope.checks, "run", return_value=[{"rule_id": "FE-PAGE-STRUCTURE", "status": "failed"}]):
            result = self.invoke(agent)
        failed = [message for message in result["messages"] if isinstance(message, ToolMessage) and message.status == "error"]
        self.assertEqual(len(failed), 1)
        self.assertIn('"retry_count": 0', failed[0].content)
        self.assertEqual(scope.retry.failures, {})
        self.assertEqual(scope.step, 3)
        agent, _ = self.agent(scope, [turn(call("advance_frontend_step", "warning", task_id=PAGE, completion_evidence="自检")), AIMessage(content="done")])
        with patch.object(scope.checks, "run", return_value=[{"rule_id": "FE-API-BOUNDARY", "status": "failed"}]):
            self.invoke(agent)
        self.assertEqual(scope.step, 4)
        self.assertFalse(scope.retry.failures)
