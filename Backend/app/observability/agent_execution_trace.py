"""Build Agent 共用的本地增量调用日志。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager, suppress
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

from app.branding import (
    DEFAULT_USER_DATA_DIRECTORY_NAME,
    USER_DATA_DIRECTORY_NAMES,
    WORKSPACE_ARTIFACT_DIR,
)
from app.observability.backend_log_redaction import LogRedactor
from app.observability.build_instrumentation import current_build_attempt_identity

logger = logging.getLogger(__name__)
_SECRET_KEY = re.compile(r"password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie", re.I)


def _message(value: Any) -> dict[str, Any]:
    """只用消息语义去重，忽略运行时附加的消息 ID、用量及响应元数据。"""
    if not hasattr(value, "content"):
        return {"type": "tool", "content": value}
    return {
        "type": str(getattr(value, "type", "")).removesuffix("Chunk"),
        "content": value.content,
        **{key: getattr(value, key) for key in
           ("name", "tool_calls", "invalid_tool_calls", "tool_call_id", "additional_kwargs", "status")
           if getattr(value, key, None)},
    }


def _reasoning(message: Any) -> str | None:
    """从最终消息提取已合并的公开推理，供模型结束事件集中展示。"""
    additional = getattr(message, "additional_kwargs", None) or {}
    reasoning = additional.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    content = getattr(message, "content", None)
    parts = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in {"thinking", "reasoning"}:
                continue
            text = block.get("thinking") or block.get("reasoning")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts) or None


def _trace_directory(workspace: str | None, directory_name: str, *, allow_invalid_workspace: bool) -> Path:
    """优先使用当前项目已忽略的运行时目录，无工作区时退回用户数据目录。"""
    if workspace:
        try:
            root = Path(workspace).expanduser().resolve()
            if root.is_dir():
                return root / WORKSPACE_ARTIFACT_DIR / "runtime" / directory_name
        except (OSError, RuntimeError, ValueError):
            if not allow_invalid_workspace:
                raise
        if not allow_invalid_workspace:
            raise ValueError("Agent 追踪工作区不是有效目录")
    directory = os.getenv("DEVAGENTSTUDIO_WORKING_DIR", DEFAULT_USER_DATA_DIRECTORY_NAME).strip()
    if directory not in USER_DATA_DIRECTORY_NAMES:
        raise ValueError("无效的用户环境目录")
    return Path.home() / directory / "logs" / directory_name


def _warn(message: str, *args: str) -> None:
    """应用日志输出失效时，诊断告警也不能影响 Agent 执行。"""
    with suppress(Exception):
        logger.warning(message, *args)


def _trace_event(action):
    """串行处理单条回调，解析或写入失败后停用本次追踪。"""
    @wraps(action)
    def record(self, *args, **kwargs) -> None:
        """仅捕获观测代码自身的失败，避免向 Agent 传播。"""
        with self._lock:
            if self._failed:
                return
            try:
                action(self, *args, **kwargs)
            except Exception:
                self._failed = True
                _warn("Agent 追踪失败，日志不完整")

    return record


class AgentExecutionTrace(BaseCallbackHandler):
    """每次 Agent 执行独享文件；回调由官方 Runnable 配置向模型和工具传播。"""

    def __init__(self, path: Path) -> None:
        """创建独占文件和串行写锁，缓存脱敏消息指纹而不缓存全文。"""
        self.path = path
        self._lock = threading.Lock()
        self._redactor = LogRedactor()
        self._messages: dict[str, str] = {}
        self._starts: dict[str, float] = {}
        self._last_inputs: list[list[str]] = []
        self._sequence = 0
        self._failed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        # 模型上下文可能包含源码，创建文件时即限制本机读取权限。
        file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            self._file = os.fdopen(file_descriptor, "w", encoding="utf-8")
        except Exception:
            os.close(file_descriptor)
            raise

    def _clean(self, value: Any) -> Any:
        """先递归脱敏再序列化，避免替换凭据时破坏 JSON 引号与结构。"""
        if isinstance(value, dict):
            return {str(key): "[REDACTED]" if _SECRET_KEY.fullmatch(str(key)) else self._clean(item)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(item) for item in value]
        if isinstance(value, str):
            return self._redactor.redact(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._redactor.redact(str(value))

    def _write(self, event: str, **data: Any) -> None:
        """在事件锁内生成顺序号、脱敏并逐事件刷盘。"""
        self._sequence += 1
        record = {"sequence": self._sequence,
                  "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "event": event, **data}
        text = json.dumps(self._clean(record), ensure_ascii=False, indent=2)
        self._file.write(text + "\n\n")
        self._file.flush()

    def _refs(self, messages: list[Any]) -> list[str]:
        """新增消息正文只落盘一次，输入与输出都引用同一消息编号。"""
        refs = []
        for message in messages:
            body = self._clean(_message(message))
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if digest not in self._messages:
                ref = f"m{len(self._messages) + 1}"
                self._messages[digest] = ref
                self._write("message", ref=ref, message=body)
            refs.append(self._messages[digest])
        return refs

    @_trace_event
    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any,
                            parent_run_id: Any = None, **kwargs: Any) -> None:
        """记录实际模型输入顺序，追加、裁剪或重建均可从引用准确还原。"""
        refs = [self._refs(batch) for batch in messages]
        previous = self._last_inputs
        extends = bool(previous) and len(previous) == len(refs) and all(
            current[:len(old)] == old for old, current in zip(previous, refs)
        )
        self._last_inputs = refs
        self._starts[str(run_id)] = time.monotonic()
        self._write("model_start", runId=str(run_id), parentRunId=str(parent_run_id),
                    context="initial" if not previous else "append" if extends else "changed",
                    inputRefs=refs, model=(kwargs.get("invocation_params") or {}).get("model"))

    @_trace_event
    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        """保存完整返回及公开推理，用量和结束原因独立于消息正文记录。"""
        messages = [generation.message for batch in response.generations for generation in batch
                    if hasattr(generation, "message")]
        self._write("model_end", runId=str(run_id), outputRefs=self._refs(messages),
                    metadata=[{"reasoning": _reasoning(m),
                               "usage": getattr(m, "usage_metadata", None),
                               "response": getattr(m, "response_metadata", None)} for m in messages],
                    durationMs=self._elapsed(run_id))

    @_trace_event
    def on_tool_start(self, serialized: Any, input_str: str, *, run_id: Any,
                      parent_run_id: Any = None, inputs: Any = None, **kwargs: Any) -> None:
        """记录实际工具入参及调用身份，包括最终一轮之后执行的工具。"""
        self._starts[str(run_id)] = time.monotonic()
        self._write("tool_start", runId=str(run_id), parentRunId=str(parent_run_id),
                    name=(serialized or {}).get("name"),
                    toolCallId=kwargs.get("tool_call_id"),
                    arguments=inputs if inputs is not None else input_str)

    @_trace_event
    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        """工具返回与下一轮输入共享消息正文，避免重复写入大段文件内容。"""
        # 部分 Deep Agent 工具返回 Command，实际消息位于其状态更新中。
        update = getattr(output, "update", None)
        messages = update.get("messages", []) if isinstance(update, dict) else [output]
        self._write("tool_end", runId=str(run_id), outputRefs=self._refs(messages),
                    stateUpdate=self._clean({k: v for k, v in update.items() if k != "messages"})
                    if isinstance(update, dict) else None,
                    durationMs=self._elapsed(run_id))

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        """记录模型失败；未完成流不冒充完整模型输出。"""
        self._error("model_error", error, run_id)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        """记录工具异常及调用耗时。"""
        self._error("tool_error", error, run_id)

    @_trace_event
    def _error(self, event: str, error: BaseException, run_id: Any) -> None:
        """统一序列化失败原因并保留并发调用身份。"""
        self._write(event, runId=str(run_id), errorType=type(error).__name__,
                    error=str(error), durationMs=self._elapsed(run_id))

    def _elapsed(self, run_id: Any) -> int | None:
        """弹出调用开始时间并计算单调时钟耗时。"""
        start = self._starts.pop(str(run_id), None)
        return round((time.monotonic() - start) * 1000) if start is not None else None

    def close(self) -> None:
        """关闭执行独占的文件句柄，不覆盖原业务结果。"""
        with self._lock:
            self._failed = True
            try:
                self._file.close()
            except Exception:
                _warn("Agent 追踪关闭失败")

    @_trace_event
    def start(self, workspace: str | None, tasks: list[dict[str, Any]],
              invocation_id: str, agent_kind: str | None) -> None:
        """关联当前统计身份；合批没有单个可归属整批的 Attempt。"""
        task_ids = [task.get("id") for task in tasks]
        identity = current_build_attempt_identity()
        if agent_kind is not None:
            identity = {
                "agentKind": agent_kind,
                "invocationId": invocation_id,
                "buildRunId": identity.get("buildRunId"),
                "attemptId": identity.get("attemptId") if len(task_ids) == 1 else None,
            }
        self._write("agent_start", workspace=workspace, taskIds=task_ids, **identity)

    @_trace_event
    def frontend_event(self, event: str, **fields: Any) -> None:
        """写入前端步骤与工具权限事件，沿用同一调用的事件锁与脱敏规则。"""
        self._write(event, **fields)

    @_trace_event
    def finish(self, start: float, error: BaseException | None = None) -> None:
        """记录成功或原业务异常、取消的终态与耗时。"""
        duration = round((time.monotonic() - start) * 1000)
        if error is None:
            self._write("agent_end", durationMs=duration)
        else:
            self._write("agent_error", errorType=type(error).__name__, error=str(error), durationMs=duration)


@contextmanager
def agent_execution_trace(
    *, workspace: str | None, tasks: list[dict[str, Any]], enabled_env: str,
    directory_name: str, agent_kind: str | None = None,
) -> Iterator[AgentExecutionTrace | None]:
    """只在临时开关启用时创建本次执行日志，退出时记录结果或取消原因。"""
    if os.getenv(enabled_env, "false").strip().lower() not in {"true", "1", "yes", "on"}:
        yield None
        return
    try:
        invocation_id = uuid4().hex
        filename = f"{datetime.now().astimezone():%Y%m%d_%H%M%S}_{invocation_id}.log"
        directory = _trace_directory(workspace, directory_name, allow_invalid_workspace=agent_kind is not None)
        trace = AgentExecutionTrace(directory / filename)
    except Exception as exc:
        _warn("无法创建 %s 追踪：%s", directory_name, type(exc).__name__)
        yield None
        return

    start = time.monotonic()
    try:
        trace.start(workspace, tasks, invocation_id, agent_kind)
        yield None if trace._failed else trace
    except BaseException as exc:
        trace.finish(start, exc)
        raise
    else:
        trace.finish(start)
    finally:
        trace.close()
