"""小任务 Agent 的提示组装、调用和不可信输出归一化。"""

from __future__ import annotations

import json
from typing import Any

from app.agents.small_task.scope import small_task_path_scope
from app.agents.tool_activity_stream import ToolActivityCallback, invoke_agent_with_tool_activity
from app.agents.messages import NO_AGENT_TEXT


SMALL_TASK_MODE_MARKER = "<xcodeagent-small-task-mode>"
_MAX_PACKET_CHARS = 28_000
_VALID_STATUSES = {
    "completed",
    "already_satisfied",
    "requires_user_confirmation",
    "requires_workflow",
    "failed",
}


def build_small_task_prompt(packet: dict[str, Any]) -> str:
    """把受限任务包编码为小任务 Agent 的唯一执行输入。"""

    packet_text = json.dumps(packet, ensure_ascii=False, indent=2)[:_MAX_PACKET_CHARS]
    return (
        f"{SMALL_TASK_MODE_MARKER}\n"
        "Execute exactly one bounded coding task from the packet below.\n"
        "Before writing, compare the requested change with the packet's confirmedContext, "
        "allowedPaths, engineeringAcceptanceChecks, and businessAcceptanceChecks. Read in this order: "
        "packet.candidateFiles first, then "
        "the narrowest relevant source directory under allowedPaths, then package or build metadata "
        "only when verification requires it. Never inspect node_modules, dist, build, target, cache, "
        "or generated dependency contents; use package manifests and lockfiles when dependency names "
        "or versions are needed. Use virtual absolute paths when calling workspace tools, but report "
        "changedFiles as workspace-relative paths. Run only focused existing "
        "checks; do not hide command exit codes and do not create temporary verification scripts. "
        "A successful result requires either an actual authorized code diff or proof that the "
        "requested behavior was already satisfied. A failed intermediate read or search is only a "
        "warning when the requested change is written and the final acceptance evidence passes; "
        "do not turn the whole task into failed solely because one tool call failed. "
        "Invoke tools through real tool calls, never as Action/Action Input text. "
        "Always finish with one complete JSON result containing status and a nonempty summary; "
        "tool arguments or an empty final message are not execution results.\n\n"
        "Required JSON contract:\n"
        '{"status":"completed|already_satisfied|requires_user_confirmation|requires_workflow|failed",'
        '"summary":"...","changedFiles":[],"verification":[],'
        '"failureReason":null,"escalation":{}}\n\n'
        f"TaskPacket:\n{packet_text}"
    )


def invoke_small_task_agent(
    *,
    packet: dict[str, Any],
    workspace: str | None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """使用本次工作流技能快照调用共享小任务 Agent。"""

    from app.agents import create_agent_bundle

    with small_task_path_scope(_string_list(packet.get("allowedPaths"), limit=100)):
        return invoke_agent_with_tool_activity(
            create_agent_bundle(workspace, selected_skill_names).small_task,
            {"messages": [{"role": "user", "content": build_small_task_prompt(packet)}]},
            workspace=workspace,
            on_tool_activity=on_tool_activity,
        )


def normalize_small_task_result(agent_note: str) -> dict[str, Any]:
    """校验模型返回的执行结果，并裁剪升级信息避免污染工作流上下文。"""

    payload, output_error = _read_small_task_output(agent_note)
    status = str(payload.get("status") or "failed").strip()
    if status not in _VALID_STATUSES:
        status = "failed"
    changed_files = _string_list(payload.get("changedFiles"), limit=200)
    verification = _string_list(payload.get("verification"), limit=100)
    escalation = payload.get("escalation")
    escalation = escalation if isinstance(escalation, dict) else {}
    normalized_escalation = {
        "reasonCode": str(
            escalation.get("reasonCode")
            or escalation.get("reason_code")
            or ""
        )[:120],
        "reason": str(escalation.get("reason") or "")[:2_000],
        "requestedPaths": _string_list(
            escalation.get("requestedPaths") or escalation.get("requested_paths"),
            limit=100,
        ),
        "requestedResources": _bounded_items(
            escalation.get("requestedResources")
            or escalation.get("requested_resources"),
            limit=50,
        ),
        "workflowIntent": str(
            escalation.get("workflowIntent")
            or escalation.get("workflow_intent")
            or ""
        )[:120],
    }
    failure_reason = str(payload.get("failureReason") or "").strip()[:2_000]
    if output_error:
        status = "failed"
        failure_reason = output_error
    return {
        "status": status,
        "summary": (output_error or str(payload.get("summary") or failure_reason).strip())[:4_000],
        "changedFiles": changed_files,
        "verification": verification,
        "alreadySatisfied": bool(
            payload.get("alreadySatisfied")
            or payload.get("already_satisfied")
            or status == "already_satisfied"
        ),
        "failureReason": failure_reason or None,
        "failureCode": "invalid_agent_output" if output_error else None,
        "escalation": normalized_escalation,
        "agentNote": str(agent_note or "")[-8_000:],
    }


def _read_small_task_output(agent_note: str) -> tuple[dict[str, Any], str | None]:
    """只接收完整结果对象，禁止把工具参数或损坏 JSON 的嵌套对象当成执行结果。"""

    text = agent_note.strip()
    if not text or text == NO_AGENT_TEXT:
        return {}, "SmallTask Agent 未返回最终执行结果（空响应）。"
    if text.startswith("Action:") or "Action Input:" in text.split("{", 1)[0]:
        return {}, "SmallTask Agent 仅返回工具调用文本，而非有效的最终执行结果。"
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return {}, "SmallTask Agent 没有返回完整有效的 JSON 执行结果。"
    if not isinstance(payload, dict):
        return {}, "SmallTask Agent 执行结果必须是 JSON 对象。"
    if not isinstance(payload.get("status"), str) or payload["status"] not in _VALID_STATUSES:
        return {}, "SmallTask Agent 执行结果缺少有效 status，不能将工具参数当作结果。"
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        return {}, "SmallTask Agent 执行结果缺少非空 summary。"
    return payload, None


def _string_list(value: Any, *, limit: int) -> list[str]:
    """把不可信列表收敛为去空的有界字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip()[:1_000] for item in value if str(item).strip()][:limit]


def _bounded_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    """裁剪升级资源对象，避免模型输出携带无限嵌套上下文。"""

    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        result.append({str(key)[:100]: str(value)[:500] for key, value in list(item.items())[:20]})
    return result
