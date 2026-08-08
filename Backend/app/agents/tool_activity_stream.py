from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.messages import last_agent_text, optional_last_agent_text
from app.agents.tool_activity_projection import (
    completed_activity_message,
    failed_activity_message,
    normalized_tool_activity,
)


ToolActivity = dict[str, Any]
ToolActivityCallback = Callable[[ToolActivity], None]
TextActivityCallback = Callable[[str], None]
StreamNamespace = tuple[str, ...]


def invoke_agent_with_tool_activity(
    agent: Any,
    payload: dict[str, Any],
    *,
    workspace: str | None,
    on_tool_activity: ToolActivityCallback | None = None,
    on_text_delta: TextActivityCallback | None = None,
) -> str:
    """执行 Deep Agent，并在需要时把安全化的工作区工具活动实时回调给构建调度器。"""

    if on_tool_activity is None:
        result = agent.invoke(payload)
        return last_agent_text(result)

    streamed_states: dict[StreamNamespace, tuple[int, dict[str, Any]]] = {}
    text_chunks: dict[StreamNamespace, list[str]] = {}
    text_orders: dict[StreamNamespace, int] = {}
    calls: dict[str, ToolActivity] = {}
    chunk_args: dict[str, str] = {}
    chunk_ids: dict[tuple[StreamNamespace, int], str] = {}
    stream = agent.stream(
        payload,
        stream_mode=["messages", "values"],
        subgraphs=True,
    )
    for order, streamed in enumerate(stream):
        stream_part = _parse_stream_part(streamed)
        if stream_part is None:
            continue
        namespace, stream_mode, chunk = stream_part
        if stream_mode == "values" and isinstance(chunk, dict):
            streamed_states[namespace] = (order, chunk)
            continue
        if stream_mode != "messages":
            continue
        message = chunk[0] if isinstance(chunk, tuple) and chunk else chunk
        agent_text = _agent_text_chunk(message)
        if agent_text:
            text_chunks.setdefault(namespace, []).append(agent_text)
            text_orders[namespace] = order
            if on_text_delta is not None:
                on_text_delta(agent_text)
        _consume_tool_message(
            message,
            namespace=namespace,
            workspace=workspace,
            calls=calls,
            chunk_args=chunk_args,
            chunk_ids=chunk_ids,
            on_tool_activity=on_tool_activity,
        )
    return _streamed_agent_text(
        streamed_states=streamed_states,
        text_chunks=text_chunks,
        text_orders=text_orders,
    ) or last_agent_text({})


def _agent_text_chunk(message: Any) -> str:
    """提取 Agent 文本分片，并排除用户消息和工具结果。"""

    if getattr(message, "tool_call_id", None):
        return ""
    message_type = str(getattr(message, "type", "") or "").lower()
    class_name = type(message).__name__.lower()
    if message_type in {"human", "tool"} or class_name.startswith(("human", "tool")):
        return ""
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else ""


def _streamed_agent_text(
    *,
    streamed_states: dict[StreamNamespace, tuple[int, dict[str, Any]]],
    text_chunks: dict[StreamNamespace, list[str]],
    text_orders: dict[StreamNamespace, int],
) -> str | None:
    """按主图优先、浅层优先、最新优先选择流式 Agent 的最终文本。"""

    root_state = streamed_states.get(())
    if root_state and (text := optional_last_agent_text(root_state[1])):
        return text
    if root_chunks := text_chunks.get(()):
        if text := "".join(root_chunks).strip():
            return text

    ranked_states = sorted(
        (item for item in streamed_states.items() if item[0]),
        key=lambda item: (len(item[0]), -item[1][0]),
    )
    for _, (_, state) in ranked_states:
        if text := optional_last_agent_text(state):
            return text

    ranked_namespaces = sorted(
        (namespace for namespace in text_chunks if namespace),
        key=lambda namespace: (len(namespace), -text_orders.get(namespace, -1)),
    )
    for namespace in ranked_namespaces:
        if text := "".join(text_chunks[namespace]).strip():
            return text
    return None


def _parse_stream_part(streamed: Any) -> tuple[StreamNamespace, str, Any] | None:
    """兼容主图与子图流事件，统一解析命名空间、流模式和事件数据。"""

    if not isinstance(streamed, tuple):
        return None
    if len(streamed) == 2:
        stream_mode, chunk = streamed
        return (), str(stream_mode), chunk
    if len(streamed) != 3:
        return None

    raw_namespace, stream_mode, chunk = streamed
    if isinstance(raw_namespace, (tuple, list)):
        namespace = tuple(str(part) for part in raw_namespace)
    else:
        namespace = ()
    return namespace, str(stream_mode), chunk


def _consume_tool_message(
    message: Any,
    *,
    namespace: StreamNamespace,
    workspace: str | None,
    calls: dict[str, ToolActivity],
    chunk_args: dict[str, str],
    chunk_ids: dict[tuple[StreamNamespace, int], str],
    on_tool_activity: ToolActivityCallback,
) -> None:
    """消费一个 LangGraph 消息块，并让同一工具调用的完整参数覆盖早期分片。"""

    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        _publish_activity(
            call_id=_stream_call_id(namespace, str(tool_call.get("id") or "")),
            tool_name=str(tool_call.get("name") or ""),
            args=tool_call.get("args"),
            workspace=workspace,
            calls=calls,
            on_tool_activity=on_tool_activity,
        )

    for tool_chunk in getattr(message, "tool_call_chunks", None) or []:
        if not isinstance(tool_chunk, dict):
            continue
        index = tool_chunk.get("index")
        call_id = _stream_call_id(namespace, str(tool_chunk.get("id") or ""))
        if call_id and isinstance(index, int):
            chunk_ids[(namespace, index)] = call_id
        if not call_id and isinstance(index, int):
            call_id = chunk_ids.get((namespace, index), "")
        if not call_id:
            continue
        args_delta = tool_chunk.get("args")
        if isinstance(args_delta, str):
            chunk_args[call_id] = (chunk_args.get(call_id, "") + args_delta)[-12_000:]
        tool_name = str(tool_chunk.get("name") or calls.get(call_id, {}).get("tool") or "")
        _publish_activity(
            call_id=call_id,
            tool_name=tool_name,
            args=chunk_args.get(call_id, ""),
            workspace=workspace,
            calls=calls,
            on_tool_activity=on_tool_activity,
        )

    tool_call_id = _stream_call_id(
        namespace,
        str(getattr(message, "tool_call_id", "") or ""),
    )
    status = str(getattr(message, "status", "") or "")
    if tool_call_id and status == "error" and tool_call_id in calls:
        failed = {
            **calls[tool_call_id],
            "status": "failed",
            "message": failed_activity_message(
                str(calls[tool_call_id].get("message") or "")
            ),
        }
        calls[tool_call_id] = failed
        on_tool_activity(failed)
    elif tool_call_id and tool_call_id in calls:
        completed = {
            **calls[tool_call_id],
            "status": "completed",
            "message": completed_activity_message(
                str(calls[tool_call_id].get("message") or "")
            ),
        }
        calls[tool_call_id] = completed
        on_tool_activity(completed)


def _stream_call_id(namespace: StreamNamespace, call_id: str) -> str:
    """为子图调用补充命名空间，避免多个代理复用调用 ID 或分片索引时串位。"""

    normalized_call_id = call_id.strip()
    if not normalized_call_id or not namespace:
        return normalized_call_id
    namespace_prefix = "/".join(namespace)
    return f"{namespace_prefix}::{normalized_call_id}"[-512:]


def _publish_activity(
    *,
    call_id: str,
    tool_name: str,
    args: Any,
    workspace: str | None,
    calls: dict[str, ToolActivity],
    on_tool_activity: ToolActivityCallback,
) -> None:
    """发布可见工具活动；参数尚未完整时先展示不含路径的安全通用文案。"""

    if not call_id or not tool_name:
        return
    activity = normalized_tool_activity(
        call_id=call_id,
        tool_name=tool_name,
        args=args,
        workspace=workspace,
    )
    if activity is None or activity == calls.get(call_id):
        return
    calls[call_id] = activity
    on_tool_activity(activity)
