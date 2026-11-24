"""验证 Build Agent 的本地回放和调用隔离。"""

from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import os
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.types import Command
from langsmith import tracing_context

from app.agents.data_source.generator import _invoke_live_data_source_agent
from app.agents.data_source.trace import data_source_trace
from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.generator import _invoke_live_frontend_agent
from app.agents.frontend.trace import frontend_agent_trace
from app.observability import build_instrumentation as observe
from app.observability.agent_execution_trace import AgentExecutionTrace
from app.observability.model_usage_callback import BuildModelUsageCallback
from app.services.model_output_logger import ModelOutputLogHandler
from app.services.run_workers import bind_run_workers
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_build_observability import ToolCapableTestModel

TRACE_DIR = Path(".devagentstudio/runtime/frontend-agent")
DATA_SOURCE_TRACE_DIR = Path(".devagentstudio/runtime/datasource-agent")
USAGE = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}


def read_events(path):
    """按原 DataSource 格式解析空行分隔的多行 JSON 对象。"""
    return [json.loads(record) for record in path.read_text(encoding="utf-8").strip().split("\n\n")]


def invoke_frontend(workspace, tasks, activity=None):
    """走前端正式调用边界，简化测试中的业务输入。"""
    return _invoke_live_frontend_agent(
        project_plan={}, build_task_plan={}, tasks=tasks, workspace=str(workspace),
        selected_skill_names=None, on_tool_activity=activity,
    )


class FrontendAgentTraceTests(unittest.TestCase):
    """覆盖开关、真实模型工具回调、身份与故障降级。"""

    def test_off_preserves_exact_invocation_and_switches_are_independent(self):
        """默认不创建前端回调，invoke 和 stream 保留原参数。"""
        with TemporaryDirectory() as workspace, patch.dict(os.environ):
            os.environ.pop("FRONTEND_AGENT_TRACE_ENABLED", None)
            os.environ["DATASOURCE_AGENT_TRACE_ENABLED"] = "true"
            agent = Mock()
            agent.invoke.return_value = {"messages": [AIMessage(content="finished")]}
            agent.stream.return_value = iter([
                ((), "values", agent.invoke.return_value),
            ])
            with (
                patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(frontend=agent)),
                patch.object(AgentExecutionTrace, "__init__") as initialize,
            ):
                self.assertEqual(invoke_frontend(workspace, []), "finished")
                self.assertEqual(invoke_frontend(workspace, [], Mock()), "finished")
            initialize.assert_not_called()
            agent.invoke.assert_called_once()
            agent.stream.assert_called_once_with(
                agent.invoke.call_args.args[0], stream_mode=["messages", "values"], subgraphs=True,
            )
            self.assertEqual(agent.invoke.call_args.kwargs, {})
            self.assertFalse((Path(workspace) / TRACE_DIR).exists())
            os.environ.update(FRONTEND_AGENT_TRACE_ENABLED="true", DATASOURCE_AGENT_TRACE_ENABLED="false")
            with data_source_trace(workspace=workspace, tasks=[]) as trace:
                self.assertIsNone(trace)
            self.assertFalse((Path(workspace) / DATA_SOURCE_TRACE_DIR).exists())

    def test_production_agent_replays_model_and_tools_in_both_modes(self):
        """用真实前端 Deep Agent、文件和命令工具确认回调传播及已有统计。"""
        for streamed in (False, True):
            with self.subTest(streamed=streamed), TemporaryDirectory() as folder:
                workspace = Path(folder)
                (workspace / "frontend").mkdir()
                target = workspace / "frontend/Page.tsx"
                write = {"file_path": "/frontend/Page.tsx", "content": "export const Page = 1;\n"}
                read = {"file_path": "/frontend/Page.tsx", "offset": 0, "limit": 12}
                execute = {"command": "printf 'trace-command-output'", "timeout": 5}
                steps = [("write_file", write), ("read_file", read), ("execute", execute)]
                responses = [
                    AIMessage(content="", usage_metadata=USAGE, tool_calls=[
                        {"name": name, "args": arguments, "id": f"call-{name}"},
                    ]) for name, arguments in steps
                ]
                responses.append(AIMessage(content="finished", usage_metadata=USAGE))
                model_log = ModelOutputLogHandler()
                model = ToolCapableTestModel(
                    responses=responses, callbacks=[model_log, BuildModelUsageCallback("fake", "fake")],
                    cache=False,
                )
                agent = create_frontend_agent(
                    model, folder, user_skills_backend=StateBackend(), agent_memory_backend=StateBackend(),
                )
                activity = Mock() if streamed else None
                task = {"id": "page:probe::page", "owner": "frontend"}

                @observe.observe_task
                def dispatch(state, scoped_task):
                    """保留真实 Attempt 执行边界，调用一次正式前端入口。"""
                    self.assertEqual(invoke_frontend(workspace, [scoped_task], activity), "finished")
                    return [{"task_id": scoped_task["id"], "status": "completed"}], None

                @observe.observe_build
                def build(state):
                    """建立真实统计作用域并挂接工作流提供的 BuildRun ID。"""
                    observe.bind_build_run({"build_run_id": "build-trace-test", "build_run_plan_sha256": "hash"})
                    results, _ = dispatch(state, task)
                    return {"status": "completed", "tasks": [task], "build_results": results}

                with (
                    tracing_context(enabled=False),
                    patch.dict(os.environ, {"FRONTEND_AGENT_TRACE_ENABLED": "true"}),
                    patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(frontend=agent)),
                    patch.object(observe.telemetry, "enabled", return_value=True),
                    patch.object(observe.telemetry, "publish") as publish,
                    patch("app.services.model_output_logger._print") as output_log,
                ):
                    build({})
                self.assertTrue(output_log.called)
                self.assertEqual(target.read_text(), write["content"])
                log, = (workspace / TRACE_DIR).glob("*.log")
                records = read_events(log)
                self.assertEqual(S_IMODE(log.stat().st_mode), 0o600)
                self.assertEqual([row["sequence"] for row in records], list(range(1, len(records) + 1)))
                models = [row for row in records if row["event"] == "model_start"]
                model_ends = [row for row in records if row["event"] == "model_end"]
                messages = {row["ref"]: row["message"] for row in records if row["event"] == "message"}
                self.assertEqual(len(models), 4)
                self.assertEqual([row["runId"] for row in model_ends], [row["runId"] for row in models])
                self.assertEqual([row["context"] for row in models], ["initial", "append", "append", "append"])
                self.assertTrue(all(row["metadata"][0]["usage"] == USAGE for row in model_ends))
                self.assertTrue(all(row["inputRefs"][0][:len(models[0]["inputRefs"][0])]
                                    == models[0]["inputRefs"][0] for row in models))
                self.assertEqual(sum(message["type"] == "human" for message in messages.values()), 1)
                tool_starts = [row for row in records if row["event"] == "tool_start"]
                tool_ends = [row for row in records if row["event"] == "tool_end"]
                self.assertEqual([(row["name"], row["arguments"]) for row in tool_starts], steps)
                for index, (start, end) in enumerate(zip(tool_starts, tool_ends, strict=True)):
                    self.assertEqual(start["toolCallId"], f"call-{steps[index][0]}")
                    self.assertEqual(start["runId"], end["runId"])
                    self.assertLess(model_ends[index]["sequence"], start["sequence"])
                    self.assertLess(start["sequence"], end["sequence"])
                    self.assertLess(end["sequence"], models[index + 1]["sequence"])
                    self.assertIn(end["outputRefs"][0], models[index + 1]["inputRefs"][0])
                self.assertIn("export const Page", messages[tool_ends[1]["outputRefs"][0]]["content"])
                self.assertEqual(json.loads(messages[tool_ends[2]["outputRefs"][0]]["content"])["stdout"],
                                 "trace-command-output")
                self.assertEqual(records[-1]["event"], "agent_end")
                if activity is not None:
                    self.assertTrue(activity.called)

                rows = [call.args[0] for call in publish.call_args_list]
                spans = [row["data"] for row in rows]
                attempt, = [row for row in rows if row["data"]["run_name"] == "task_attempt"]
                identity = records[0]
                self.assertEqual(identity["taskIds"], [task["id"]])
                self.assertEqual(identity["buildRunId"], "build-trace-test")
                self.assertEqual(identity["attemptId"], attempt["context"]["extra"]["attempt_id"])
                self.assertEqual(sum(span["span_kind"] == "model" for span in spans), 4)
                self.assertEqual(sum(span["span_kind"] == "tool" for span in spans), 3)
                self.assertNotIn("trace-command-output", json.dumps(rows))

    def test_parallel_invocations_and_batch_retry_keep_separate_files(self):
        """共享 Runnable 的并发、合批和再次调用均独占文件及消息编号。"""
        def run(payload, *, config):
            """为共享 Runnable 模拟当前调用的独立回调事件。"""
            trace = config["callbacks"][0]
            trace.on_chat_model_start({}, [[AIMessage(content=trace.path.stem)]], run_id=uuid4())
            return {"messages": [AIMessage(content="finished")]}

        with (
            TemporaryDirectory() as workspace,
            patch.dict(os.environ, {"FRONTEND_AGENT_TRACE_ENABLED": "true"}),
            patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(frontend=Mock(
                invoke=Mock(side_effect=run),
            ))),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            tasks = [{"id": "page-a"}, {"id": "page-b"}]
            pending = [executor.submit(copy_context().run, invoke_frontend, workspace, [task]) for task in tasks]
            self.assertEqual([future.result() for future in pending], ["finished", "finished"])
            for _ in range(2):
                self.assertEqual(invoke_frontend(workspace, tasks), "finished")
            logs = list((Path(workspace) / TRACE_DIR).glob("*.log"))
            self.assertEqual(len(logs), 4)
            starts, run_ids = [], set()
            for log in logs:
                start, message, model, end = read_events(log)
                self.assertEqual(log.stem.split("_")[-1], start["invocationId"])
                self.assertEqual(start["agentKind"], "frontend")
                self.assertIsNone(start["buildRunId"])
                self.assertIsNone(start["attemptId"])
                self.assertEqual(message["ref"], "m1")
                self.assertEqual(message["message"]["content"], log.stem)
                self.assertEqual(model["inputRefs"], [["m1"]])
                self.assertEqual(end["event"], "agent_end")
                starts.append(tuple(start["taskIds"]))
                run_ids.add(model["runId"])
            self.assertCountEqual(starts, [("page-a",), ("page-b",), ("page-a", "page-b"), ("page-a", "page-b")])
            self.assertEqual(len(run_ids), 4)

    def test_logging_failures_and_business_errors_are_isolated(self):
        """目录、内容、写入和关闭故障均在观测边界内处理，取消继续传播。"""
        with TemporaryDirectory() as workspace, patch.dict(os.environ, {"FRONTEND_AGENT_TRACE_ENABLED": "true"}):
            for error in (ValueError("business failure"), CancelledError("stop")):
                with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(
                    frontend=Mock(invoke=Mock(side_effect=error)),
                )), self.assertRaises(type(error)) as raised:
                    invoke_frontend(workspace, [{"id": "page"}])
                self.assertIs(raised.exception, error)
            logs = list((Path(workspace) / TRACE_DIR).glob("*.log"))
            self.assertCountEqual([read_events(log)[-1]["errorType"] for log in logs],
                                  ["ValueError", "CancelledError"])

            with patch("app.observability.agent_execution_trace.os.open", side_effect=PermissionError):
                with frontend_agent_trace(workspace=workspace, tasks=[]) as trace:
                    self.assertIsNone(trace)
            for broken_method in ("_refs", "_write"):
                with frontend_agent_trace(workspace=workspace, tasks=[]) as trace:
                    with patch.object(trace, broken_method, side_effect=ValueError("private content")), \
                         self.assertLogs("app.observability.agent_execution_trace", level="WARNING") as warnings:
                        for _ in range(2):
                            trace.on_chat_model_start({}, [[]], run_id=uuid4())
                    self.assertEqual(len(warnings.output), 1)
                    self.assertNotIn("private content", warnings.output[0])
                self.assertTrue(trace._file.closed)
            with frontend_agent_trace(workspace=workspace, tasks=[]) as trace:
                # 先保全真实文件句柄，再用桩模拟关闭失败。
                trace._file.close()
                trace._file = Mock()
                trace._file.close.side_effect = OSError("close failed")

    def test_existing_cancellation_middleware_stops_the_real_agent(self):
        """开启 Trace 后，被停止的运行仍在生产中间件处拒绝调用模型。"""
        with TemporaryDirectory() as workspace:
            model = ToolCapableTestModel(responses=[AIMessage(content="unexpected")])
            agent = create_frontend_agent(
                model, workspace, user_skills_backend=StateBackend(), agent_memory_backend=StateBackend(),
            )
            with (
                tracing_context(enabled=False),
                bind_run_workers("trace-cancelled-run", workspace),
                patch.dict(os.environ, {"FRONTEND_AGENT_TRACE_ENABLED": "true"}),
                patch.object(workspace_process_registry, "is_run_cancelled", return_value=True),
                patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(frontend=agent)),
                self.assertRaisesRegex(RuntimeError, "当前运行已停止"),
            ):
                invoke_frontend(workspace, [{"id": "page"}])
            log, = (Path(workspace) / TRACE_DIR).glob("*.log")
            self.assertEqual([row["event"] for row in read_events(log)], ["agent_start", "agent_error"])
            self.assertEqual(model.i, 0)

    def test_fallback_and_redaction(self):
        """无合法工作区时走用户目录，结构化和文本凭据均应脱敏。"""
        with (
            TemporaryDirectory() as home,
            patch.dict(os.environ, {"FRONTEND_AGENT_TRACE_ENABLED": "true", "SAMPLE_TOKEN": "sample-from-env"}),
            patch("app.observability.agent_execution_trace.Path.home", return_value=Path(home)),
        ):
            with frontend_agent_trace(workspace=str(Path(home) / "missing"), tasks=[]) as trace:
                tool_id = uuid4()
                trace.on_tool_start({"name": "execute"}, "", run_id=tool_id, inputs={
                    "password": "plain-password", "api_key": "plain-api-key",
                    "headers": {"Authorization": "plain-header"},
                    "command": "printf sample-from-env",
                })
                returned = ToolMessage(content="probe result", tool_call_id="probe-call")
                trace.on_tool_end(Command(update={"messages": [returned], "secret": "plain-state"}), run_id=tool_id)
                trace.on_chat_model_start({}, [[AIMessage(
                    content="Authorization: Bearer plain-bearer\npassword=plain-scalar",
                ), returned]], run_id=uuid4())
            self.assertEqual(trace.path.parent.name, "frontend-agent")
            self.assertEqual(trace.path.parent.parent.name, "logs")
            self.assertTrue(trace.path.is_relative_to(home))
            content = trace.path.read_text()
            for value in ("plain-password", "plain-api-key", "plain-header", "sample-from-env",
                          "plain-bearer", "plain-scalar", "plain-state"):
                self.assertNotIn(value, content)
            self.assertIn("[REDACTED]", content)
            events = {record["event"]: record for record in read_events(trace.path)}
            self.assertEqual(events["tool_end"]["outputRefs"], [events["model_start"]["inputRefs"][0][-1]])


class DataSourceAgentTraceTests(unittest.TestCase):
    """验证数据源 Agent 入口的追踪回调。"""

    def test_data_source_trace_records_nested_model_and_tool_in_both_execution_modes(self) -> None:
        """验证正式 Agent 入口的同步与流式调用都会生成实际模型和工具事件。"""

        @tool
        def read_probe() -> str:
            """返回固定的工具结果，不读取工作区。"""
            return "probe result"

        def execute(state: MessagesState) -> dict:
            """通过真实 LangGraph 和 LangChain 调用链触发嵌套回调。"""
            result = read_probe.invoke({"type": "tool_call", "name": "read_probe",
                                        "args": {}, "id": "probe-call"})
            assert isinstance(result, ToolMessage)
            model = FakeMessagesListChatModel(responses=[AIMessage(content="completed")])
            return {"messages": [result, model.invoke([*state["messages"], result])]}

        graph = StateGraph(MessagesState)
        graph.add_node("execute", execute)
        graph.add_edge(START, "execute")
        agent = graph.compile()

        for enabled, streamed in (("false", False), ("true", False), ("true", True)):
            with self.subTest(enabled=enabled, streamed=streamed), TemporaryDirectory() as workspace:
                activity = []
                with (
                    tracing_context(enabled=False),
                    patch.dict("os.environ", {"DATASOURCE_AGENT_TRACE_ENABLED": enabled}),
                    patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(data_source=agent)),
                ):
                    result = _invoke_live_data_source_agent(
                        project_plan={},
                        workspace_snapshot={"backend": {"dir_structure": "backend/\n└── pom.xml"}},
                        tasks=[{"id": "build-task", "unit_id": "backend:bootstrap"}],
                        workspace=workspace,
                        selected_skill_names=None,
                        on_tool_activity=activity.append if streamed else None,
                    )
                self.assertEqual(result, "completed")
                logs = list((Path(workspace) / DATA_SOURCE_TRACE_DIR).glob("*.log"))
                if enabled == "false":
                    self.assertEqual(logs, [])
                    continue
                self.assertEqual(len(logs), 1)
                records = read_events(logs[0])
                events = {record["event"]: record for record in records if record["event"] != "message"}
                self.assertEqual(events["agent_start"]["taskIds"], ["build-task"])
                self.assertEqual(events["tool_start"]["name"], "read_probe")
                self.assertEqual(events["tool_start"]["runId"], events["tool_end"]["runId"])
                self.assertEqual(events["model_start"]["runId"], events["model_end"]["runId"])
                self.assertIn(events["tool_end"]["outputRefs"][0], events["model_start"]["inputRefs"][0])
                self.assertEqual(records[-1]["event"], "agent_end")


class ModelTimingTraceTests(unittest.TestCase):
    """验证详细日志独立采集，不新增逐 Token 事件或工具字段。"""

    def test_timing_boundaries_and_no_token_io(self):
        """固定纳秒时间保持 round 口径，消息引用继续复用。"""
        from langchain_core.outputs import ChatGeneration, LLMResult
        with TemporaryDirectory() as root:
            trace = AgentExecutionTrace(Path(root) / "timing.log")
            response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="same"))]])
            with patch("app.observability.agent_execution_trace.time.monotonic_ns", return_value=0) as clock:
                trace.on_chat_model_start({}, [[AIMessage(content="same")]], run_id="a")
                trace.on_chat_model_start({}, [[]], run_id="b")
                clock.return_value = 2_600_000
                with patch.object(trace, "_write") as write:
                    trace.on_llm_new_token("", run_id="b")
                    trace.on_llm_new_token("a", run_id="a")
                    clock.return_value = 8_000_000
                    trace.on_llm_new_token("b", run_id="a")
                    write.assert_not_called()
                clock.return_value = 10_900_000
                trace.on_llm_end(response, run_id="a")
                trace.on_llm_error(CancelledError(), run_id="b")
                trace.on_llm_new_token("late", run_id="a")
                trace.on_tool_start({}, "", run_id="tool")
                trace.on_tool_error(ValueError(), run_id="tool")
            self.assertFalse(trace._model_timings)
            trace.close()
            events = read_events(trace.path)
            end = next(row for row in events if row["event"] == "model_end")
            self.assertEqual((end["durationMs"], end["ttftMs"], end["outputDurationMs"]), (11, 3, 8))
            self.assertEqual(end["outputRefs"], ["m1"])
            error = next(row for row in events if row["event"] == "model_error")
            self.assertEqual(error["durationMs"], 11)
            self.assertIsNone(error["ttftMs"])
            tool_error = next(row for row in events if row["event"] == "tool_error")
            self.assertNotIn("ttftMs", tool_error)
            self.assertFalse(any(row["event"] == "model_token" for row in events))

    def test_timing_failure_preserves_log_and_close_clears_state(self):
        """新增采集出错不禁用日志，取消后保留首增量，close 清理在途调用。"""
        with TemporaryDirectory() as root:
            trace = AgentExecutionTrace(Path(root) / "timing.log")
            with patch("app.observability.agent_execution_trace.time.monotonic_ns", return_value=0) as clock:
                trace.on_chat_model_start({}, [[]], run_id="a")
                with patch("app.observability.agent_execution_trace.ModelStreamTiming.observe", side_effect=ValueError()):
                    trace.on_llm_new_token("a", run_id="a")
                self.assertFalse(trace._failed)
                clock.return_value = 2_600_000
                trace.on_llm_new_token("a", run_id="a")
                clock.return_value = 10_900_000
                trace.on_llm_error(CancelledError(), run_id="a")
                trace.on_chat_model_start({}, [[]], run_id="unfinished")
            trace.close()
            self.assertFalse(trace._model_timings)
            error = next(row for row in read_events(trace.path) if row["event"] == "model_error")
            self.assertEqual((error["durationMs"], error["ttftMs"], error["outputDurationMs"]), (11, 3, 8))

    def test_stream_timing_switches_independent_and_together(self):
        """两套观测各自开启或同时开启均独立采集，关闭时不产生记录。"""
        for telemetry_on in (True, False):
            for trace_on in (True, False):
                with TemporaryDirectory() as root, patch.dict(os.environ, {"FRONTEND_AGENT_TRACE_ENABLED": str(trace_on)}), patch.object(
                    observe.telemetry, "enabled", return_value=telemetry_on,
                ), patch.object(observe.telemetry, "publish") as publish:
                    parent = observe.Span(observe.BuildTrace({}, {}), "task", "node")
                    token = observe._scope.set(parent if telemetry_on else None)
                    try:
                        with frontend_agent_trace(workspace=root, tasks=[]) as trace:
                            callback = BuildModelUsageCallback("fake", "fake")
                            callback.on_chat_model_start({}, [[]], run_id="a")
                            if trace:
                                trace.on_chat_model_start({}, [[]], run_id="a")
                            callback.on_llm_new_token("a", run_id="a")
                            if trace:
                                trace.on_llm_new_token("a", run_id="a")
                                trace.on_llm_error(CancelledError(), run_id="a")
                            callback.on_llm_error(CancelledError(), run_id="a")
                    finally:
                        observe._scope.reset(token)
                    self.assertEqual(publish.call_count, int(telemetry_on))
                    if telemetry_on:
                        model = publish.call_args.args[0]["data"]
                        self.assertEqual(model["duration_ms"], model["model"]["ttft_ms"] + model["model"]["output_duration_ms"])
                    logs = list((Path(root) / TRACE_DIR).glob("*.log"))
                    self.assertEqual(len(logs), int(trace_on))
                    if trace_on:
                        error = next(item for item in read_events(logs[0]) if item["event"] == "model_error")
                        self.assertEqual(error["durationMs"], error["ttftMs"] + error["outputDurationMs"])
