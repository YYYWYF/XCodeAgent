"""摘要请求异常、取消、任务边界和缓存恢复的真实工厂回归。"""

import asyncio
from contextlib import nullcontext
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import NodeCancelledError

from app.agents.frontend.context_guard import FrontendContextGuard, CONTEXT_ID
from app.agents.frontend.execution_scope import FrontendTaskExecutionScope, FRONTEND_TEMPLATE_AGENTS_DOC as DOC
from app.agents.frontend.retry_guard import FrontendTaskRetryExhausted
from app.agents.frontend.task_packet import API, PAGE
from tests.test_frontend_build_execution import _packet
from tests import test_frontend_build_harness as harness
from tests.test_frontend_build_harness import CapturingModel, call


class RaisingModel(CapturingModel):
    """在实际模型调用内注入异常，不替换摘要控制逻辑。"""

    cancel: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """分别模拟摘要服务异常和原生用户取消。"""
        self.inputs.append(messages)
        if self.cancel:
            raise asyncio.CancelledError()
        raise RuntimeError("摘要服务不可用")


class FrontendContextEdgesTests(unittest.TestCase):
    """复用 fixture 而不继承其测试，避免重复执行同一组用例。"""

    def setUp(self):
        """安装受控工作区及真实工厂辅助入口。"""
        self.fixture = harness.FrontendBuildHarnessTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_summary_request_exceptions_and_cancellation(self):
        """服务异常最多四次调用；用户取消直接传播且不消耗失败预算。"""
        from app.agents.frontend.agent import create_frontend_agent
        from deepagents.backends import StateBackend

        history = [HumanMessage(content=f"历史{i}") for i in range(8)]
        for mode in ("sync", "async"):
            for cancel in (False, True):
                with self.subTest(mode=mode, cancel=cancel):
                    scope = FrontendTaskExecutionScope(str(self.fixture.root), [_packet(API)])
                    model = RaisingModel(responses=[AIMessage(content="unused")], cancel=cancel)
                    agent = create_frontend_agent(model, str(self.fixture.root), user_skills_backend=StateBackend(),
                                                  agent_memory_backend=StateBackend(), build_scope=scope)
                    with scope.task_context(), patch.object(FrontendContextGuard, "_should_summarize", return_value=True), patch.object(
                        FrontendContextGuard, "_determine_cutoff_index", return_value=6
                    ), (patch.object(RaisingModel, "ainvoke" if mode == "async" else "invoke",
                                     side_effect=asyncio.CancelledError()) if cancel else nullcontext()):
                        with self.assertRaises((asyncio.CancelledError, NodeCancelledError) if cancel else FrontendTaskRetryExhausted):
                            self.fixture.invoke(agent, mode, history)
                    self.assertEqual(len(model.inputs), 0 if cancel else 4)
                    if cancel:
                        self.assertFalse(scope.retry.failures)
                        self.assertIsNone(scope.retry.termination)

    def test_compressed_pending_api_and_finalizing_projection(self):
        """begin 前、API 和全部完成后的摘要均不虚构步骤或授权。"""
        for kind, finished in ((PAGE, False), (API, False), (PAGE, True)):
            with self.subTest(kind=kind, finished=finished):
                scope = FrontendTaskExecutionScope(str(self.fixture.root), [_packet(kind)])
                if finished:
                    scope.index = 1
                    scope.outcomes[PAGE] = "completed"
                    scope.evidence[PAGE] = ["已完成四步"]
                agent, model = self.fixture.agent(scope, [AIMessage(content="已压缩历史事实"), AIMessage(content="done")])
                with scope.task_context() if kind == API else nullcontext(), patch.object(
                    FrontendContextGuard, "_should_summarize", return_value=True
                ), patch.object(FrontendContextGuard, "_determine_cutoff_index", return_value=6):
                    self.fixture.invoke(agent, messages=[HumanMessage(content=f"历史{i}") for i in range(8)])
                block, = [item for item in model.inputs[-1][0].content if item.get("id") == CONTEXT_ID]
                state = json.loads(block["text"].split("\n", 1)[1])
                self.assertEqual(state["task_id"], None if finished else kind)
                self.assertEqual(state["task_started"], kind == API)
                self.assertEqual(state["step"], None if kind == API else 0)
                if kind == PAGE:
                    self.assertFalse(state["readable"])
                    self.assertFalse(state["writable"])
                if finished:
                    self.assertNotIn("implementation_contract", state)

    def test_missing_core_body_recovers_only_original_valid_tool_pair(self):
        """正文缓存遗失可从原始成功配对恢复；无原文则失败，不额外读取。"""
        for recover in (True, False):
            with self.subTest(recover=recover):
                scope = FrontendTaskExecutionScope(str(self.fixture.root), [_packet(API)])
                scope.core_docs_read.add((API, DOC))
                agent, model = self.fixture.agent(scope, [AIMessage(content="done")])
                history = [HumanMessage(content="执行")]
                if recover:
                    history += [AIMessage(content="", tool_calls=[call("read_file", "doc", file_path=DOC, limit=2000)]),
                                ToolMessage(content="1\t# 完整模板规范", tool_call_id="doc", name="read_file")]
                with scope.task_context():
                    if recover:
                        self.fixture.invoke(agent, messages=history)
                        self.assertIn((API, DOC), scope.core_docs_body)
                        self.assertEqual(len(model.inputs), 1)
                    else:
                        with self.assertRaises(FrontendTaskRetryExhausted):
                            self.fixture.invoke(agent, messages=history)
                        self.assertEqual(model.inputs, [])

    def test_initial_contract_not_duplicated_and_normal_call_count(self):
        """初始任务包已可见时不重复完整契约，正常任务只调用一次主模型。"""
        packet = _packet(API)
        packet["implementation_contract"]["marker"] = "formal-unique-contract"
        scope = FrontendTaskExecutionScope(str(self.fixture.root), [packet])
        agent, model = self.fixture.agent(scope, [AIMessage(content="done")])
        with scope.task_context():
            self.fixture.invoke(agent, messages=[HumanMessage(id="frontend-build-initial-tasks",
                                content="前端任务包\n" + json.dumps([packet]))])
        self.assertEqual(len(model.inputs), 1)
        self.assertEqual(str(model.inputs[0]).count("formal-unique-contract"), 1)

    def test_clipped_core_tool_message_restores_complete_cached_body(self):
        """框架保留调用 ID 但裁剪正文时仍恢复完整缓存，不能误判正文可见。"""
        scope = FrontendTaskExecutionScope(str(self.fixture.root), [_packet(API)])
        scope.core_docs_read.add((API, DOC))
        scope.core_docs_body[(API, DOC)] = ("1\t完整正文包含末尾规范", "doc")
        history = [HumanMessage(content="执行"),
                   AIMessage(content="", tool_calls=[call("read_file", "doc", file_path=DOC, limit=2000)]),
                   ToolMessage(content="1\t开头\n[Output was truncated due to context window size limits.]",
                               tool_call_id="doc", name="read_file")]
        agent, model = self.fixture.agent(scope, [AIMessage(content="done")])
        with scope.task_context():
            self.fixture.invoke(agent, messages=history)
        block, = [item for item in model.inputs[0][0].content if item.get("id") == CONTEXT_ID]
        self.assertIn("完整正文包含末尾规范", block["text"])
