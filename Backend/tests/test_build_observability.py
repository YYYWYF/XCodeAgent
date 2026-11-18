"""验证真实 Agent 回调边界与本地实验报表。"""

from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, LLMResult
from langchain_core.tools import tool
from langsmith import tracing_context

from app.agents.execution_control import RunCancellationMiddleware
from app.agents.model_operations import model_operation
from app.observability import build_instrumentation as observe
from app.observability.build_baseline_report import render_report
from app.observability.model_usage_callback import BuildModelUsageCallback
from app.observability.telemetry_models import ExecutionTraceRecord


class ToolCapableTestModel(FakeMessagesListChatModel):
    """仍走 LangChain 标准调用流程的离线工具模型。"""

    def bind_tools(self, tools, **kwargs):
        """允许 Agent 按真实路径绑定工具规格。"""
        return self


class BuildInstrumentationTests(unittest.TestCase):
    """使用实际 LangGraph 并发和模型生命周期验证归属。"""

    def test_integration_trace_links_calls_and_redacts_evidence(self):
        """同一 Run 的规划、编码和门禁可配对，真实 Usage 不包含正文。"""
        state = {"active_run_id": "integration-run", "active_thread_id": "thread"}
        callback = BuildModelUsageCallback("fake-test", "fake-model")
        model = ToolCapableTestModel(callbacks=[callback], responses=[AIMessage(
            content="private reply", usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )])
        with tracing_context(enabled=False), patch.object(observe.telemetry, "enabled", return_value=True), patch.object(
            observe.telemetry, "publish",
        ) as publish:
            with observe.integration_repair_trace("integration_test", state=state) as gate:
                with model_operation("plan_integration_repairs", business_stage="integration_test"), observe.integration_repair_trace(
                    "repair_planning", repair_round=1,
                ) as plan:
                    observe.record_prompt("private prompt", task_context="private evidence")
                    model.invoke("private evidence")
                    model.invoke("private evidence")
                    plan.reason = "additional_evidence"
                gate.quality_gate_passed = False
            with model_operation("repair_frontend_integration_code", business_stage="integration_test"), observe.integration_repair_trace(
                "small_task_repair", state=state, task_id="repair-task", repair_round=1,
            ):
                model.invoke("private source")
        rows = [call.args[0] for call in publish.call_args_list]
        for row in rows:
            ExecutionTraceRecord.model_validate(row)
            self.assertEqual(row["identity"]["run_id"], "integration-run")
            self.assertEqual(row["context"]["business_stage"], "integration_test")
        self.assertNotIn("private", json.dumps(rows))
        models = [row for row in rows if row["data"]["span_kind"] == "model"]
        self.assertEqual([row["context"]["extra"]["model_round"] for row in models], [1, 2, 1])
        self.assertEqual([row["data"]["model"]["total_tokens"] for row in models], [6, 6, 6])
        self.assertEqual(len({row["context"]["extra"]["attempt_id"] for row in models}), 2)

    def test_parallel_tasks_model_and_tool(self):
        """并发任务、真实 LangChain 工具和模型回调保留各自 Attempt。"""
        calls = []

        @tool
        def echo() -> str:
            """提供一个可统计且不应出现在埋点中的文本结果。"""
            calls.append("echo")
            return "私密🙂"

        @observe.observe_task
        def run_task(state, task):
            """模拟调度执行器中的模型、工具和主管道边界。"""
            observe.record_prompt("提示🙂 secret", task_context="线程")
            callback = BuildModelUsageCallback("fake-test", "fake-test")
            usage = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
            agent = create_agent(
                ToolCapableTestModel(callbacks=[callback], responses=[
                    AIMessage(content="", usage_metadata=usage, tool_calls=[
                        {"name": "echo", "args": {}, "id": f"call-{task['id']}"},
                    ]),
                    AIMessage(content="private model output", usage_metadata=usage),
                ]),
                tools=[echo], middleware=[RunCancellationMiddleware()],
            )
            agent.invoke({"messages": [{"role": "user", "content": "private input"}]})
            return [{"task_id": task["id"], "status": "completed",
                     "acceptance_status": {"business": "passed"}}], None

        @observe.observe_build
        def run(state):
            """并发派发并模拟真实 Build binding 与业务返回。"""
            observe.bind_build_run({"build_run_id": "build-real", "build_run_plan_sha256": "hash"})
            tasks = [{"id": "task-a", "owner": "frontend", "status": "completed"},
                     {"id": "task-b", "owner": "backend", "status": "completed", "retry_count": 1}]
            observe.bind_build_tasks(tasks, ["task-a", "task-b"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(copy_context().run, run_task, state, task) for task in tasks]
                results = [future.result()[0][0] for future in pending]
            return {"status": "completed", "tasks": tasks, "build_execution_slice": {"tasks": tasks[:1]},
                    "build_results": results}

        with tracing_context(enabled=False), patch.object(observe.telemetry, "enabled", return_value=True), patch.object(
            observe.telemetry, "publish",
        ) as publish:
            result = run({"project_id": "app", "active_run_id": "run-real", "operation_id": "op-real"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, ["echo", "echo"])
        rows = [call.args[0] for call in publish.call_args_list]
        serialized = json.dumps(rows, ensure_ascii=False)
        for secret in ("私密", "private model output", "private input", "提示", "线程"):
            self.assertNotIn(secret, serialized)
        root = rows[-1]["data"]
        self.assertEqual(root["metrics"]["model_spans"], 4)
        self.assertEqual(root["metrics"]["tool_spans"], 2)
        self.assertEqual(root["metrics"]["planned_tasks"], 2)
        self.assertEqual(root["metrics"]["first_completed_tasks"], 1)
        attempts = {row["context"]["extra"]["task_id"]: row for row in rows
                    if row["data"]["run_name"] == "task_attempt"}
        self.assertEqual(len({row["context"]["extra"]["attempt_id"] for row in attempts.values()}), 2)
        self.assertTrue(attempts["task-b"]["context"]["extra"]["retry"])
        for row in rows:
            extra, data = row["context"]["extra"], row["data"]
            self.assertEqual(extra["build_run_id"], "build-real")
            self.assertEqual(row["identity"]["operation_id"], "op-real")
            if data["span_kind"] in {"model", "tool"}:
                task = attempts[extra["task_id"]]
                self.assertEqual(data["parent_span_id"], task["data"]["span_id"])
                self.assertEqual(extra["attempt_id"], task["context"]["extra"]["attempt_id"])
            if data["span_kind"] == "model":
                self.assertEqual(data["model"]["total_tokens"], 7)
            if data["span_kind"] == "tool":
                self.assertEqual(data["metrics"]["tool_result_bytes"], len("私密🙂".encode()))

    def test_error_partial_usage_and_cancellation(self):
        """重复终态不增加用量，取消和缺失字段不改业务异常。"""
        @observe.observe_build
        def run(state):
            """触发一次取消的模型回调与一次重复结束通知。"""
            observe.bind_build_run({"build_run_id": "build", "build_run_plan_sha256": "hash"})
            callback = BuildModelUsageCallback(None, None)
            run_id = uuid4()
            callback.on_chat_model_start({}, [], run_id=run_id)
            error = CancelledError("private error")
            tail = ChatGenerationChunk(message=AIMessageChunk(
                content="", response_metadata={"token_usage": {"prompt_tokens": 5}},
            ))
            callback.on_llm_new_token("", run_id=run_id, chunk=tail)
            callback.on_llm_new_token("", run_id=run_id, chunk=tail)
            response = LLMResult(generations=[[ChatGeneration(
                message=AIMessage(content="", response_metadata={"token_usage": {"prompt_tokens": 10}}),
            )]])
            callback.on_llm_error(error, run_id=run_id, response=response)
            callback.on_llm_end(response, run_id=run_id)
            raise error

        with tracing_context(enabled=False), patch.object(observe.telemetry, "enabled", return_value=True), patch.object(
            observe.telemetry, "publish",
        ) as publish, self.assertRaises(CancelledError):
            run({})
        self.assertEqual(publish.call_count, 2)
        model = publish.call_args_list[0].args[0]["data"]
        self.assertEqual(model["status"], "cancelled")
        self.assertEqual(model["model"]["input_tokens"], 5)
        self.assertIsNone(model["model"]["total_tokens"])
        self.assertNotIn("private error", str(publish.call_args_list))

    def test_off_and_pipeline_failure_preserve_return_value(self):
        """关闭时不扫描正文，启用后提交失败仍返回原对象。"""
        original = {"status": "completed"}

        @observe.observe_build
        def run(state):
            """执行实际观测入口而不依赖业务工具。"""
            observe.record_prompt("secret", task_context="secret")
            return original

        with patch.object(observe.telemetry, "enabled", return_value=False), patch.object(
            observe, "utf8_size", side_effect=AssertionError,
        ) as size:
            self.assertIs(run({}), original)
            size.assert_not_called()
        with tracing_context(enabled=False), patch.object(observe.telemetry, "enabled", return_value=True), patch.object(
            observe.telemetry, "publish", side_effect=OSError,
        ):
            self.assertIs(run({}), original)


def write_sample(path, tag, *, tokens=100, checked=2, cache="cold", incomplete=False):
    """写固定结构的 BuildRun 测试 JSONL 与实验清单。"""
    path.mkdir()
    build_id = f"build-{tag}"
    manifest = {
        "build_run_id": build_id, "project_id": "demo", "scenario_id": "crud",
        "experiment_tag": tag, "code_revision": tag, "requirement_revision": "requirements-v1",
        "workspace_snapshot": "repo-tree-hash", "task_plan_sha256": "dag-sha",
        "model": {"name": "test", "provider": "test", "transport": "direct",
                  "parameters": {"temperature": 0, "max_tokens": 500}},
        "template_version": "template-v1", "tool_environment": "env-sha",
        "validation_suite": ["file-check"], "cache_condition": cache, "repetitions": 1,
        "quality_gate": {"minimum_final_rate": 1, "minimum_acceptance_rate": 1,
                         "wall_time_tolerance": 0.05, "repair_count_tolerance": 0},
    }
    (path / "manifest.json").write_text(json.dumps([manifest]), encoding="utf-8")

    def span(name, key, kind, *, parent="root", metrics=None, model=None, task=None):
        """构造最小可归属的严格记录。"""
        return {
            "type": "execution_trace", "identity": {"project_id": "demo"},
            "context": {"business_stage": "build", "extra": {
                "build_run_id": build_id, "scenario_id": "crud", "experiment_tag": tag,
                "task_plan_sha256": "dag-sha", "task_id": task,
                "attempt_id": f"{task}-attempt" if task else None,
            }},
            "data": {"trace_id": "trace", "span_id": key, "parent_span_id": parent, "span_kind": kind,
                     "run_name": name, "duration_ms": 120, "status": "completed", "metrics": metrics, "model": model},
        }
    rows = [
        span("build", "root", "workflow", parent=None, metrics={
            "model_spans": 1, "tool_spans": 1, "node_spans": 3, "planned_tasks": 2,
            "completed_tasks": 2, "first_completed_tasks": 2, "acceptance_passed": checked, "acceptance_failed": 2 - checked,
        }),
        span("task_attempt", "a", "node", task="a"),
        span("task_attempt", "b", "node", task="b"),
        span("initial_prompt", "p", "node", parent="a", task="a",
             metrics={"project_plan_bytes": tokens * 2, "workspace_context_bytes": 6,
                      "task_context_bytes": 30, "initial_prompt_bytes": tokens * 3}),
        span("model_inference", "m", "model", parent="a", task="a", model={
            "usage_source": "provider", "input_tokens": tokens - 10, "output_tokens": 10,
            "total_tokens": None if incomplete else tokens,
        }),
        span("read_file", "t", "tool", parent="a", task="a", metrics={"tool_result_bytes": 40}),
    ]
    (path / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class BuildReportTests(unittest.TestCase):
    """覆盖 Baseline、可比样本、质量回归和损坏输入。"""

    def test_summary_and_compare(self):
        """汇总只列实测值，条件一致时计算变化率和通过结论。"""
        with TemporaryDirectory() as tmp:
            baseline, after = Path(tmp) / "baseline", Path(tmp) / "after"
            write_sample(baseline, "baseline")
            write_sample(after, "after", tokens=70)
            report = render_report(baseline)
            self.assertIn("| Total Token | 100（100） | N/A | N/A | N/A |", report)
            report = render_report(baseline, after)
            self.assertIn("| ProjectPlan 注入（UTF-8 字节） | 200（200） | 140（140） | -60 | 30.0% |", report)
            self.assertIn("| Total Token | 100（100） | 70（70） | -30 | 30.0% |", report)
            self.assertIn("| Task 首轮完成率 | 1（1） | 1（1） | 0 | 0.0% |", report)
            self.assertIn("符合预设门禁", report)
            self.assertIn("a-attempt", report)

    def test_quality_regression_and_incomplete_input(self):
        """质量回归、Usage 缺失、条件差异、坏行和丢片均限制结论。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            write_sample(baseline, "baseline")
            for name, options, expected in (
                ("quality", {"checked": 1}, "质量门禁未通过"),
                ("unknown", {"incomplete": True}, "不可比较/数据不足"),
                ("cache", {"cache": "warm"}, "不可比较/数据不足"),
            ):
                write_sample(root / name, name, tokens=70, **options)
                report = render_report(baseline, root / name)
                self.assertIn(expected, report)
                self.assertNotIn("符合预设门禁", report)
            sample = root / "lost"
            write_sample(sample, "lost")
            file = sample / "events.jsonl"
            file.write_text("\n".join(file.read_text().splitlines()[:-1]) + "\n{broken\n")
            report = render_report(baseline, sample)
            self.assertIn("Span 不完整", report)
            self.assertIn("有无效、重复或未绑定", report)
            self.assertIn("未获得真实 Baseline", render_report(root / "missing"))
