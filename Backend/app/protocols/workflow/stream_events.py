"""将主工作流进度、模型活动和工具活动编码为 AG-UI 事件帧。"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ag_ui.core import (
    CustomEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi.encoders import jsonable_encoder

from app.protocols.workflow.definition import PROCESS_DETAIL_LIMIT, PROCESS_EVENT_NAME
from app.protocols.workflow.projection import (
    _workflow_node_label,
    _workflow_progress_summary,
    _workflow_visual_payload,
)


def _workflow_ag_ui_frames(
    encoder: EventEncoder,
    *,
    run_id: str,
    thread_id: str,
    events: list[dict[str, Any]],
    result: dict[str, Any],
    visual_payload: dict[str, Any] | None = None,
) -> Iterable[str]:
    """同时发送最新的完整自定义事件和可视化状态。

    CustomEvent 用于实时订阅，StateSnapshot 用于重连后的状态恢复。
    """

    payload = visual_payload or _workflow_visual_payload(
        run_id=run_id,
        thread_id=thread_id,
        summary=_workflow_progress_summary(result, events),
        events=events,
        result=result,
    )
    safe_payload = jsonable_encoder(payload)

    yield encoder.encode(CustomEvent(name="workflow-run", value=safe_payload))
    yield encoder.encode(StateSnapshotEvent(snapshot={"workflow": safe_payload}))


def _process_frame(
    encoder: EventEncoder,
    *,
    id: str,
    kind: str,
    status: str,
    title: str,
    sequence: int,
    detail: str = "",
    result: str = "",
    append_detail: bool = False,
    checks: list[dict[str, Any]] | None = None,
    node_name: str | None = None,
    attempt: int | None = None,
    iteration_kind: str | None = None,
    build_execution_slice: dict[str, Any] | None = None,
    dag_generation: dict[str, Any] | None = None,
    workspace_inspection: dict[str, Any] | None = None,
    workspace_inspection_progress: dict[str, Any] | None = None,
) -> str:
    value: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "status": status,
        "title": title,
        "detail": detail[-PROCESS_DETAIL_LIMIT:],
        "result": result[-PROCESS_DETAIL_LIMIT:],
        "appendDetail": append_detail,
        "sequence": sequence,
    }
    if checks is not None:
        value["checks"] = checks
    if node_name:
        value["nodeName"] = node_name
    if attempt is not None:
        value["attempt"] = attempt
    if iteration_kind:
        value["iterationKind"] = iteration_kind
    if build_execution_slice is not None:
        value["buildExecutionSlice"] = build_execution_slice
    if dag_generation is not None:
        value["dagGeneration"] = dag_generation
    if workspace_inspection is not None:
        value["workspaceInspection"] = workspace_inspection
    if workspace_inspection_progress is not None:
        value["workspaceInspectionProgress"] = workspace_inspection_progress
    return encoder.encode(
        CustomEvent(
            name=PROCESS_EVENT_NAME,
            value=value,
        )
    )


def integration_test_checks(value: Any) -> list[dict[str, Any]]:
    """将测试结果裁剪为前端进度卡可安全展示的小型检查清单。"""

    raw_checks = value.get("checks") if isinstance(value, dict) else value
    if not isinstance(raw_checks, list):
        return []
    checks: list[dict[str, Any]] = []
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict):
            continue
        check_id = str(raw_check.get("id") or "").strip()
        if not check_id:
            continue
        status = str(raw_check.get("status") or "").strip()
        if status not in {"running", "passed", "skipped", "failed"}:
            if raw_check.get("skipped") and raw_check.get("passed"):
                status = "skipped"
            elif raw_check.get("passed"):
                status = "passed"
            else:
                status = "failed"
        normalized_check = {
            "id": check_id,
            "name": str(raw_check.get("name") or check_id),
            "status": status,
            "required": bool(raw_check.get("required")),
            "evidence": str(raw_check.get("evidence") or "")[:1_000],
        }
        passed_tests = raw_check.get("passed_tests", raw_check.get("passedTests"))
        total_tests = raw_check.get("total_tests", raw_check.get("totalTests"))
        if (
            isinstance(passed_tests, int)
            and not isinstance(passed_tests, bool)
            and passed_tests >= 0
        ):
            normalized_check["passed_tests"] = passed_tests
        if (
            isinstance(total_tests, int)
            and not isinstance(total_tests, bool)
            and total_tests >= 0
        ):
            normalized_check["total_tests"] = total_tests
        checks.append(normalized_check)
    return checks


def integration_test_check_summary(checks: list[dict[str, Any]]) -> str:
    """生成兼容旧版前端的逐项检查详情，确保不会只展示数字汇总。"""

    if not checks:
        return "正在准备检查项。"
    labels = {
        "running": "检查中",
        "passed": "已通过",
        "skipped": "已跳过",
        "failed": "未通过",
    }
    lines = ["检查项"]
    for check in checks:
        status = str(check.get("status") or "failed")
        line = f"{check.get('name') or check.get('id')}：{labels.get(status, '未通过')}"
        evidence = str(check.get("evidence") or "").strip()
        if status == "failed" and evidence:
            line = f"{line} — {evidence[:1_000]}"
        lines.append(line)
    return "\n".join(lines)


def _message_process_frames(
    encoder: EventEncoder,
    *,
    message_chunk: Any,
    metadata: Any,
    reasoning_steps: dict[str, str],
    tool_steps: dict[str, dict[str, str]],
    tool_indexes: dict[int, str],
    sequence: int,
) -> tuple[list[str], int]:
    frames: list[str] = []
    metadata = metadata if isinstance(metadata, dict) else {}
    node_name = str(metadata.get("langgraph_node") or "model")
    message_id = str(getattr(message_chunk, "id", "") or node_name)
    reasoning = _reasoning_text(message_chunk)
    if reasoning:
        step_id = f"reasoning:{message_id}"
        reasoning_steps[step_id] = "streaming"
        sequence += 1
        frames.append(
            _process_frame(
                encoder,
                id=step_id,
                kind="reasoning",
                status="running",
                title=f"正在思考 · {_workflow_node_label(node_name)}",
                detail=reasoning,
                append_detail=True,
                sequence=sequence,
            )
        )

    tool_call_id = str(getattr(message_chunk, "tool_call_id", "") or "")
    if tool_call_id and tool_call_id in tool_steps:
        current = tool_steps[tool_call_id]
        if current.get("result_emitted") != "true":
            content = getattr(message_chunk, "content", "")
            result = content if isinstance(content, str) else str(content)
            current["result_emitted"] = "true"
            if current.get("ended") != "true":
                current["ended"] = "true"
                frames.append(encoder.encode(ToolCallEndEvent(toolCallId=tool_call_id)))
            frames.append(
                encoder.encode(
                    ToolCallResultEvent(
                        messageId=message_id,
                        toolCallId=tool_call_id,
                        content=result,
                        role="tool",
                    )
                )
            )
            sequence += 1
            frames.append(
                _process_frame(
                    encoder,
                    id=f"tool:{tool_call_id}",
                    kind="command" if _is_command_tool(current["name"]) else "tool",
                    status="completed",
                    title=_tool_title(current["name"], current["args"], running=False),
                    detail=current["args"],
                    result=result,
                    sequence=sequence,
                )
            )

    for tool_chunk in getattr(message_chunk, "tool_call_chunks", None) or []:
        if not isinstance(tool_chunk, dict):
            continue
        tool_index = tool_chunk.get("index")
        tool_id = str(tool_chunk.get("id") or "")
        if tool_id and isinstance(tool_index, int):
            tool_indexes[tool_index] = tool_id
        if not tool_id and isinstance(tool_index, int):
            tool_id = tool_indexes.get(tool_index, "")
        if not tool_id:
            continue
        current = tool_steps.setdefault(tool_id, {"name": "unknown", "args": ""})
        if current.get("started") != "true":
            current["started"] = "true"
            frames.append(
                encoder.encode(
                    ToolCallStartEvent(
                        toolCallId=tool_id,
                        toolCallName=str(tool_chunk.get("name") or current["name"]),
                        parentMessageId=message_id,
                    )
                )
            )
        if tool_chunk.get("name"):
            current["name"] = str(tool_chunk["name"])
        args_delta = str(tool_chunk.get("args") or "")
        current["args"] = (current["args"] + args_delta)[-PROCESS_DETAIL_LIMIT:]
        if args_delta:
            frames.append(
                encoder.encode(ToolCallArgsEvent(toolCallId=tool_id, delta=args_delta))
            )
        sequence += 1
        kind = "command" if _is_command_tool(current["name"]) else "tool"
        frames.append(
            _process_frame(
                encoder,
                id=f"tool:{tool_id}",
                kind=kind,
                status="running",
                title=_tool_title(current["name"], current["args"], running=True),
                detail=current["args"],
                sequence=sequence,
            )
        )
    return frames, sequence


def _reasoning_text(message_chunk: Any) -> str:
    additional = getattr(message_chunk, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = additional.get(key)
            if isinstance(value, str) and value:
                return value
    content = getattr(message_chunk, "content", None)
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or block.get("content") or "")
        for block in content
        if isinstance(block, dict)
        and str(block.get("type") or "").lower() in {"reasoning", "thinking"}
    )


def _tool_result_frames(
    encoder: EventEncoder,
    *,
    update: dict[str, Any],
    tool_steps: dict[str, dict[str, str]],
    sequence: int,
) -> Iterable[str]:
    messages = update.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        tool_call_id = str(getattr(message, "tool_call_id", "") or "")
        if not tool_call_id or tool_call_id not in tool_steps:
            continue
        current = tool_steps[tool_call_id]
        if current.get("result_emitted") == "true":
            continue
        content = getattr(message, "content", "")
        result = content if isinstance(content, str) else str(content)
        current["result_emitted"] = "true"
        if current.get("ended") != "true":
            current["ended"] = "true"
            yield encoder.encode(ToolCallEndEvent(toolCallId=tool_call_id))
        yield encoder.encode(
            ToolCallResultEvent(
                messageId=str(getattr(message, "id", "") or f"tool-result:{tool_call_id}"),
                toolCallId=tool_call_id,
                content=result,
                role="tool",
            )
        )
        yield _process_frame(
            encoder,
            id=f"tool:{tool_call_id}",
            kind="command" if _is_command_tool(current["name"]) else "tool",
            status="completed",
            title=_tool_title(current["name"], current["args"], running=False),
            detail=current["args"],
            result=result,
            sequence=sequence + 1,
        )


def _pending_tool_frames(
    encoder: EventEncoder,
    *,
    update: dict[str, Any],
    tool_steps: dict[str, dict[str, str]],
    sequence: int,
) -> Iterable[str]:
    clarification = update.get("clarification")
    for tool_call_id, current in tool_steps.items():
        if current.get("started") != "true" or current.get("ended") == "true":
            continue
        current["ended"] = "true"
        yield encoder.encode(ToolCallEndEvent(toolCallId=tool_call_id))

        if current.get("name") != "ask_user" or not isinstance(clarification, dict):
            continue
        result = json.dumps(clarification, ensure_ascii=False)
        current["result_emitted"] = "true"
        yield encoder.encode(
            ToolCallResultEvent(
                messageId=f"tool-result:{tool_call_id}",
                toolCallId=tool_call_id,
                content=result,
                role="tool",
            )
        )
        yield _process_frame(
            encoder,
            id=f"tool:{tool_call_id}",
            kind="tool",
            status="completed",
            title=_tool_title(current["name"], current["args"], running=False),
            detail=current["args"],
            result=result,
            sequence=sequence + 1,
        )


def _is_command_tool(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("execute", "exec", "shell", "terminal", "command"))


def _tool_title(name: str, args: str, *, running: bool) -> str:
    if _is_command_tool(name):
        command = _command_summary(args) or name
        return f"{'正在执行' if running else '已执行'} {command} 命令"
    return f"{'正在调用' if running else '已调用'} {name} 工具"


def _command_summary(args: str) -> str:
    try:
        payload = json.loads(args)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        for key in ("command", "cmd", "input"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().splitlines()[0][:80]
    return args.strip().splitlines()[0][:80] if args.strip() else ""


def _text_delta_frames(
    encoder: EventEncoder,
    message_id: str,
    text: str,
    *,
    size: int = 80,
) -> Iterable[str]:
    for chunk in _chunk_text(text, size=size):
        yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=chunk))


def _chunk_text(text: str, *, size: int = 80) -> Iterable[str]:
    if not text:
        yield ""
        return

    for index in range(0, len(text), size):
        yield text[index : index + size]
