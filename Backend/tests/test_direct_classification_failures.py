"""验证分类模型故障不会冒充需求澄清，真实执行边界仍保留失败现场。"""

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import openai
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.direct_modification import classify_direct_modification_intent
from app.domain.application_lifecycle import ApplicationInitialization
from app.graph.direct_modification_workflow import build_direct_modification_graph
from app.persistence.execution_recovery import get_execution
from app.protocols.direct_modification import build_conversation_ag_ui_stream
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.model_transmit_client import ModelTransmitError
from app.graph.nodes.direct_modification import execute_frontend_direct_modification, execute_backend_direct_modification


def model_errors() -> list[Exception]:
    """提供 SDK 的真实异常类型，覆盖直连和转发的当前故障合同。"""
    request = httpx.Request("POST", "https://model.example/v1/chat/completions")
    return [
        openai.APIConnectionError(request=request),
        openai.APITimeoutError(request=request),
        httpx.ConnectError("Connection refused", request=request),
        *[
            openai.APIStatusError(
                f"HTTP {status}: model unavailable",
                response=httpx.Response(status, request=request),
                body=None,
            )
            for status in (401, 429, 503)
        ],
        ModelTransmitError("转发服务无法连接上游模型", code="UPSTREAM_CONNECTION_ERROR"),
    ]


class DirectClassificationFailureTests(unittest.TestCase):
    """在真实分类函数的调用和流式边界检查原始异常传播。"""

    def test_writers_without_changes_preserve_model_exception(self) -> None:
        """前后端写入前的模型异常保留原身份，不转成收尾失败或丢失 HTTP 原因。"""
        for execute, invoke in ((execute_frontend_direct_modification, "invoke_frontend_direct_modification"),
                                (execute_backend_direct_modification, "invoke_data_source_direct_modification")):
            for error in model_errors():
                with self.subTest(node=execute.__name__, error=type(error).__name__), tempfile.TemporaryDirectory() as workspace:
                    with patch(f"app.graph.nodes.direct_modification.{invoke}", side_effect=error):
                        with self.assertRaises(type(error)) as caught:
                            execute({"workspace": workspace, "request": "修改现有代码"})
                    self.assertIs(caught.exception, error)

    def test_model_failures_propagate_invoke_and_stream(self) -> None:
        """网络与 HTTP 失败不能返回可继续填写的澄清结果。"""
        for error in model_errors():
            for streaming in (False, True):
                with self.subTest(error=repr(error), streaming=streaming):
                    model = Mock()
                    model.invoke.side_effect = error
                    model.stream.side_effect = error
                    with patch("app.agents.direct_modification.create_chat_model", return_value=model):
                        with self.assertRaises(type(error)) as caught:
                            classify_direct_modification_intent(
                                user_request="修改按钮文字",
                                on_response_delta=Mock() if streaming else None,
                            )
                    self.assertIs(caught.exception, error)

    def test_model_setup_failure_propagates(self) -> None:
        """模型配置失败同样保留原始异常，不要求用户补写业务需求。"""
        error = ValueError("模型配置无效")
        with patch("app.agents.direct_modification.create_chat_model", side_effect=error):
            with self.assertRaises(ValueError) as caught:
                classify_direct_modification_intent(user_request="修改按钮文字")
        self.assertIs(caught.exception, error)

    def test_genuine_clarification_remains_available(self) -> None:
        """空请求与模型明确识别的需求不明仍然进入原澄清流程。"""
        with patch("app.agents.direct_modification.create_chat_model") as factory:
            decision = classify_direct_modification_intent(user_request=" ")
            self.assertEqual(decision.intent, "clarification")
            factory.assert_not_called()
            factory.return_value.invoke.return_value = SimpleNamespace(content=json.dumps({
                "route": "clarification", "owner": "unknown", "confidence": 0.95,
                "reason": "用户未说明修改对象。", "clarificationQuestion": "请说明要修改什么功能？",
            }))
            decision = classify_direct_modification_intent(user_request="帮我改一下")
            self.assertEqual(decision.intent, "clarification")
            self.assertEqual(decision.clarification_question, "请说明要修改什么功能？")


class DirectClassificationStreamFailureTests(unittest.IsolatedAsyncioTestCase):
    """串联真实分类、Graph、checkpoint、lifecycle、DurableExecution 和 AG-UI。"""

    async def test_failure_reaches_summary_and_durable_model_diagnostic(self) -> None:
        """失败原因进入实时摘要及恢复证据，失败节点保留且不生成补充信息确认卡。"""
        for error in model_errors():
            with self.subTest(error=repr(error)), tempfile.TemporaryDirectory() as workspace:
                lifecycle = create_application_lifecycle(application_id="app", application_name="分类故障验证")
                lifecycle = lifecycle.model_copy(update={"initialization": ApplicationInitialization(
                    stage="ready_for_workbench", status="completed", threadId="planning",
                )})
                write_application_lifecycle(workspace, lifecycle, expected_revision=0)
                async def scan(_state):
                    """只替换无关扫描，保留真实分类节点和执行器。"""
                    return {"status": "completed", "phase": "scan_workspace_code"}

                with patch("app.graph.direct_modification_workflow.scan_workspace_code", scan):
                    graph = build_direct_modification_graph(checkpointer=InMemorySaver())
                model = Mock()
                model.stream.side_effect = error
                with (
                    patch("app.agents.direct_modification.create_chat_model", return_value=model),
                    patch("app.protocols.direct_modification.direct_modification_graph_for_request",
                          new=AsyncMock(return_value=graph)),
                    patch("app.services.execution_cleanup.best_effort_checkpoint_housekeeping",
                          new=AsyncMock(return_value=0)),
                ):
                    frames = "".join([frame async for frame in build_conversation_ag_ui_stream(payload={
                        "threadId": "thread", "runId": "source",
                        "messages": [{"role": "user", "content": "修改按钮文字"}],
                        "forwardedProps": {"sessionId": "session", "conversation": {"workspaceRoot": workspace}},
                    })])
                events = [json.loads(line[6:]) for line in frames.splitlines() if line.startswith("data: ")]
                summary = events[-1]["result"]["conversation"]["summary"]
                failed = events[-1]["result"]["conversation"]
                self.assertEqual(failed["processStep"]["status"], "failed")
                self.assertEqual(failed["processStep"]["title"], "执行失败 判断修改类型")
                self.assertEqual(failed["events"][-1]["status"], "failed")
                self.assertEqual(summary["status"], "failed")
                self.assertIn(type(error).__name__, summary["message"])
                self.assertIn(str(error), summary["message"])
                self.assertEqual(events[0]["type"], "RUN_STARTED")
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                self.assertIn("STATE_SNAPSHOT", frames)
                self.assertNotIn("TEXT_MESSAGE_CONTENT", frames)
                self.assertNotIn("direct_modification_clarification", frames)
                self.assertNotIn("补充修改信息", frames)
                source = await get_execution(workspace, "source")
                self.assertEqual(source.status.value, "failed")
                self.assertEqual(source.current_node, "classify_intent")
                self.assertEqual(source.failure.origin.value, "model_call")
                self.assertIn(str(error), source.failure.diagnostic_message)
                current = load_application_lifecycle(workspace)
                self.assertEqual(current.active_executions["source"].status.value, "failed")
                self.assertIsNone(current.resource_locks.application)
                snapshot = await graph.aget_state({"configurable": {"thread_id": "thread"}})
                self.assertIn("classify_intent", snapshot.next)
                self.assertFalse(snapshot.values.get("clarification"))
