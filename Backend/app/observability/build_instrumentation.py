"""从真实 Build 边界生成标识、耗时和大小统计。"""

from asyncio import CancelledError
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import os
from threading import Lock
from time import monotonic_ns
from uuid import uuid4

from langgraph.config import get_config

from app.agents.model_business_context import current_model_context
from app.observability import telemetry
from app.services.workspace_process_registry import workspace_process_registry

_scope = ContextVar("build_telemetry_span", default=None)
_SUCCESS = {"completed", "already_satisfied"}


@dataclass
class BuildTrace:
    """共享同次调度的身份和已产生的子 Span 数量。"""

    identity: dict
    extra: dict
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    spans: Counter = field(default_factory=Counter)
    planned_ids: set[str] | None = None
    first_completed: set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)


@dataclass
class Span:
    """保存单个调用边界的最小元数据。"""

    trace: BuildTrace
    name: str
    kind: str
    parent: str | None = None
    extra: dict = field(default_factory=dict)
    operation: str | None = None
    span_id: str = field(default_factory=lambda: uuid4().hex)
    started: int = field(default_factory=monotonic_ns)


def _start(name, kind, extra=None):
    """继承当前协程或任务线程的父片段，不修改模型服务上下文。"""
    try:
        parent = _scope.get()
        if parent is None:
            return None
        operation = current_model_context({}).get("business_operation") or parent.operation
        return Span(parent.trace, name, kind, parent.span_id,
                    {**parent.extra, **(extra or {})}, operation)
    except Exception:
        return None


def finish(span, status, *, metrics=None, model=None, timed=True):
    """终态只发送结构化统计，任何观测失败留在此边界。"""
    try:
        if span is None:
            return
        trace = span.trace
        if span.parent is not None:
            with trace.lock:
                trace.spans[span.kind] += 1
        telemetry.publish({
            "producer": {"source": "backend"},
            "identity": trace.identity,
            "context": {
                "business_stage": "build", "business_operation": span.operation,
                "node_name": "build", "extra": {**trace.extra, **span.extra},
            },
            "data": {
                "trace_id": trace.trace_id, "span_id": span.span_id,
                "parent_span_id": span.parent, "span_kind": span.kind, "run_name": span.name,
                "duration_ms": (monotonic_ns() - span.started) // 1_000_000 if timed else None,
                "status": status, "metrics": metrics, "model": model,
            },
        })
    except Exception:
        pass


def _acceptance(results):
    """统计有明确终态的验收证据，跳过、警告和缺失证据保持未知。"""
    passed = failed = 0
    for result in results:
        evidence = result.get("acceptance_status") or {}
        values = set(evidence.values())
        if values & {"failed", "blocked"}:
            failed += 1
        elif ("passed" in values and "warning" not in values
              and (result.get("acceptance_evidence") or
                   (result.get("business_acceptance_summary") or {}).get("total", 1))):
            passed += 1
    return {"acceptance_passed": passed, "acceptance_failed": failed}


def _build_result(span, result):
    """从最终执行切片统计原始 DAG 任务和验收结果。"""
    trace = span.trace
    metrics = {
        "model_spans": trace.spans["model_started"],
        "tool_spans": trace.spans["tool"],
        "node_spans": trace.spans["node"],
    }
    if trace.planned_ids is not None:
        planned = trace.planned_ids
        final_tasks = result.get("tasks")
        results = [item for item in result.get("build_results", []) if item.get("task_id") in planned]
        metrics.update(
            _acceptance(results),
            planned_tasks=len(planned),
            first_completed_tasks=len(trace.first_completed & planned),
            completed_tasks=sum(task.get("id") in planned and task.get("status") in _SUCCESS
                                for task in final_tasks) if final_tasks is not None else None,
        )
    status = result.get("status")
    if _is_cancelled(span) or "scheduler:cancelled" in result.get("build_events", []):
        return "cancelled", metrics
    return ("awaiting_user" if status == "requires_user_input" else
            "completed" if status == "completed" else "failed"), metrics


def _is_cancelled(span):
    """复用工作流已登记的停止事实，涵盖同步线程的普通异常。"""
    try:
        run_id = span.trace.identity.get("run_id")
        return bool(run_id and workspace_process_registry.is_run_cancelled(run_id))
    except Exception:
        return False


def _exception_status(error):
    """将真实异常分类，避免记录异常消息或堆栈。"""
    return "cancelled" if isinstance(error, (CancelledError, KeyboardInterrupt, GeneratorExit)) else "failed"


def observe_build(action):
    """覆盖整个调度调用，以单调时钟计算墙钟而非相加并发任务。"""
    @wraps(action)
    def observe(state, *args, **kwargs):
        """在关闭时原样调用，并在启用时于执行前建立隔离的根片段。"""
        if not telemetry.enabled():
            return action(state, *args, **kwargs)
        try:
            try:
                metadata = get_config().get("metadata", {})
            except RuntimeError:
                metadata = {}
            trace = BuildTrace(
                {"project_id": state.get("project_id") or metadata.get("project_id"),
                 "run_id": state.get("active_run_id") or metadata.get("run_id"),
                 "operation_id": state.get("operation_id") or metadata.get("operation_id")},
                {"experiment_tag": os.getenv("DEVAGENTSTUDIO_OBSERVABILITY_EXPERIMENT", "default"),
                 "scenario_id": os.getenv("DEVAGENTSTUDIO_OBSERVABILITY_SCENARIO", "default")},
            )
            for key in ("build_run_id", "build_run_plan_sha256"):
                if state.get(key):
                    bind_key = "task_plan_sha256" if key == "build_run_plan_sha256" else key
                    trace.extra[bind_key] = state[key]
            span = Span(trace, "build", "workflow")
            token = _scope.set(span)
        except Exception:
            return action(state, *args, **kwargs)
        try:
            result = action(state, *args, **kwargs)
        except BaseException as error:
            finish(span, "cancelled" if _is_cancelled(span) else _exception_status(error))
            raise
        else:
            try:
                status, metrics = _build_result(span, result)
                finish(span, status, metrics=metrics)
            except Exception:
                pass
            return result
        finally:
            _scope.reset(token)

    return observe


def bind_build_run(binding):
    """采用调度器已创建或恢复的真绑定，供此调用的所有子片段继承。"""
    span = _scope.get()
    if span is not None:
        try:
            span.trace.extra.update(build_run_id=binding["build_run_id"],
                                    task_plan_sha256=binding["build_run_plan_sha256"])
        except Exception:
            pass


def bind_build_tasks(tasks, task_ids):
    """锁定首次执行切片的原始 DAG 任务，避免终态裁剪改变统计分母。"""
    span = _scope.get()
    if span is not None:
        try:
            selected = set(task_ids)
            span.trace.planned_ids = {
                task["id"] for task in tasks if task["id"] in selected and task.get("kind") != "repair"
            }
        except Exception:
            pass


def _task_status(results):
    """把业务结果映射为统计终态，审批暂停独立保留。"""
    statuses = {result.get("status") for result in results}
    if statuses and statuses <= _SUCCESS:
        return "completed"
    if statuses & {"approval_required", "requires_confirmation", "requires_user_input"} or any(
        result.get("failure_category") == "database_approval_required" for result in results
    ):
        return "awaiting_user"
    return "failed"


def observe_task(action):
    """为每次真实执行分配独立统计 attempt_id。"""
    @wraps(action)
    def observe(state, task, *args, **kwargs):
        """返回原任务结果，包括业务失败和暂停。"""
        if _scope.get() is None:
            return action(state, task, *args, **kwargs)
        try:
            span = _start("task_attempt", "node", {
                "task_id": task.get("id"), "task_owner": task.get("owner"),
                "attempt_id": uuid4().hex, "retry": bool(task.get("retry_count")),
                "repair": task.get("kind") == "repair",
            })
        except Exception:
            span = None
        if span is None:
            return action(state, task, *args, **kwargs)
        with activate(span):
            result = action(state, task, *args, **kwargs)
            try:
                status = "cancelled" if _is_cancelled(span) else _task_status(result[0])
                if status == "completed" and not span.extra["retry"]:
                    with span.trace.lock:
                        span.trace.first_completed.add(span.extra["task_id"])
                finish(span, status, metrics=_acceptance(result[0]))
            except Exception:
                pass
            return result

    return observe


@contextmanager
def activate(span):
    """绑定子片段并在业务异常或取消时记录终态后继续传播。"""
    token = _scope.set(span)
    try:
        yield
    except BaseException as error:
        finish(span, "cancelled" if _is_cancelled(span) else _exception_status(error))
        raise
    finally:
        _scope.reset(token)


def observe_validation(action, *args, **kwargs):
    """读取现有验证器返回的检查状态，保留返回值及异常。"""
    span = _start("validation", "node")
    if span is None:
        return action(*args, **kwargs)
    with activate(span):
        result = action(*args, **kwargs)
        try:
            metrics = _acceptance(result)
            finish(span, "failed" if metrics["acceptance_failed"] else "completed", metrics=metrics)
        except Exception:
            pass
        return result


def observe_repair(action, *args, **kwargs):
    """保留原修复决策对象，让 RepairPlanner 模型耗用继承修复标记。"""
    span = _start("repair_planning", "node", {"repair": True})
    if span is None:
        return action(*args, **kwargs)
    with activate(span):
        result = action(*args, **kwargs)
        try:
            statuses = {"terminal_failure": "failed", "requires_user_confirmation": "awaiting_user"}
            finish(span, statuses.get(result.get("decision"), "completed"))
        except Exception:
            pass
        return result


def utf8_size(text):
    """计算实际 UTF-8 体积，限制临时编码缓冲区大小。"""
    return sum(len(text[offset:offset + 8192].encode("utf-8"))
               for offset in range(0, len(text), 8192))


def record_prompt(prompt, *, task_context, project_plan=None, workspace_context=None):
    """只统计已拼接的真实文本，不二次序列化或保存内容。"""
    span = _start("initial_prompt", "node")
    if span is None:
        return
    try:
        metrics = {}
        for name, text in (("initial_prompt", prompt), ("task_context", task_context),
                           ("project_plan", project_plan), ("workspace_context", workspace_context)):
            if text is not None:
                metrics[f"{name}_chars"] = len(text)
                metrics[f"{name}_bytes"] = utf8_size(text)
        finish(span, "completed", metrics=metrics, timed=False)
    except Exception:
        pass


def start_model():
    """捕获模型调用开始时的 Build 归属。"""
    span = _start("model_inference", "model")
    if span is not None:
        with span.trace.lock:
            span.trace.spans["model_started"] += 1
    return span


def start_tool(request):
    """从实际调用读取工具名，拒绝记录工具参数。"""
    try:
        if _scope.get() is not None:
            return _start(request.tool_call["name"], "tool")
    except Exception:
        pass
    return None


def finish_tool(span, result):
    """统计工具返回的文本或文本块，其他返回形态保留未知。"""
    if span is None:
        return
    status = "completed"
    try:
        status = "failed" if getattr(result, "status", None) == "error" else "completed"
        content = getattr(result, "content", None)
        size = None
        if isinstance(content, str):
            size = utf8_size(content)
        elif isinstance(content, list):
            size = 0
            for part in content:
                text = part if isinstance(part, str) else part.get("text") if isinstance(part, dict) else None
                if not isinstance(text, str):
                    size = None
                    break
                size += utf8_size(text)
    except Exception:
        size = None
    finish(span, status, metrics={"tool_result_bytes": size})
