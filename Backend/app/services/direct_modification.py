from __future__ import annotations

from typing import Any

from app.utils.model_output import extract_json_object


MAX_DIRECT_MODIFICATION_SUMMARY_CHARS = 4_000


def parse_direct_modification_agent_result(agent_note: str) -> dict[str, Any]:
    """把快速 Agent 的最终 JSON 归一化为稳定阶段结果。"""

    payload = extract_json_object(agent_note) or {}
    status = str(payload.get("status") or "failed")
    already_satisfied = bool(
        payload.get("alreadySatisfied") or status == "already_satisfied"
    )
    if status not in {"completed", "failed", "already_satisfied"}:
        status = "failed"
    summary = str(payload.get("summary") or "").strip()
    failure_reason = str(payload.get("failureReason") or "").strip()
    if not payload:
        status = "failed"
        failure_reason = "Agent 没有返回有效的 JSON 结果。"
    return {
        "status": "completed" if status == "already_satisfied" else status,
        "summary": summary or failure_reason or "Agent 未提供执行摘要。",
        "changedFiles": _string_list(payload.get("changedFiles"), limit=200),
        "verification": _string_list(payload.get("verification"), limit=100),
        "alreadySatisfied": already_satisfied,
        "failureReason": failure_reason or None,
        "backendHandoff": _normalize_backend_handoff(payload.get("backendHandoff")),
    }


def _normalize_backend_handoff(value: Any) -> dict[str, Any]:
    """限制后端交接信息的大小和公开字段，避免污染后续前端上下文。"""

    payload = value if isinstance(value, dict) else {}
    raw_endpoints = payload.get("endpoints")
    endpoints: list[dict[str, Any]] = []
    for item in raw_endpoints if isinstance(raw_endpoints, list) else []:
        if not isinstance(item, dict):
            continue
        endpoints.append(
            {
                "method": str(item.get("method") or "")[:16],
                "path": str(item.get("path") or "")[:512],
                "request": _bounded_json_value(item.get("request")),
                "response": _bounded_json_value(item.get("response")),
            }
        )
    return {
        "summary": str(payload.get("summary") or "")[:1_000],
        "endpoints": endpoints[:50],
        "changedFiles": _string_list(payload.get("changedFiles"), limit=200),
        "notes": _string_list(payload.get("notes"), limit=100),
    }


def _string_list(value: Any, *, limit: int) -> list[str]:
    """把模型返回值裁剪为去空的短字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip()[:1_000] for item in value if str(item).strip()][
        :limit
    ]


def _bounded_json_value(value: Any, *, depth: int = 0) -> Any:
    """递归裁剪后端交接中的 JSON 值，防止嵌套契约占满上下文。"""

    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, dict):
        return {
            str(key)[:200]: _bounded_json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:1_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1_000]


def validated_direct_stage_result(
    result: dict[str, Any],
    *,
    code_change_set: dict[str, Any] | None,
    owner: str,
) -> dict[str, Any]:
    """以工作区快照覆盖模型文件清单，并拒绝无差异或跨目录写入。"""

    files = (
        [item for item in code_change_set.get("files", []) if isinstance(item, dict)]
        if isinstance(code_change_set, dict)
        else []
    )
    changed_paths = [str(item.get("path") or "") for item in files if item.get("path")]
    invalid_paths = [
        path for path in changed_paths if not direct_path_matches_owner(path, owner)
    ]
    normalized = {**result, "changedFiles": changed_paths}
    if invalid_paths:
        return {
            **normalized,
            "status": "failed",
            "failureReason": f"Agent 修改了职责目录之外的文件：{', '.join(invalid_paths)}",
        }
    if (
        normalized.get("status") == "completed"
        and not changed_paths
        and normalized.get("alreadySatisfied") is not True
    ):
        return {
            **normalized,
            "status": "failed",
            "failureReason": "Agent 报告完成，但工作区没有实际代码差异。",
        }
    return normalized


def direct_path_matches_owner(path: str, owner: str) -> bool:
    """按虚拟工程目录判断一次代码变更是否属于当前 Agent。"""

    parts = [part.casefold() for part in path.replace("\\", "/").split("/") if part]
    expected = "frontend" if owner == "frontend" else "backend"
    return expected in parts


def append_direct_conversation_summary(
    previous: str,
    *,
    request: str,
    outcome: str,
) -> str:
    """追加本轮需求和结果，并把 quick-chat 摘要限制在4000字符。"""

    entry = f"用户：{request.strip()}\n结果：{outcome.strip()}".strip()
    return "\n\n".join(part for part in (previous.strip(), entry) if part)[
        -MAX_DIRECT_MODIFICATION_SUMMARY_CHARS:
    ]


def direct_state_message(state: dict[str, Any]) -> str:
    """读取快速修改状态消息，并在 LangGraph 过滤 message 时回退到澄清问题。"""

    message = str(state.get("message") or "").strip()
    if message:
        return message
    clarification = state.get("clarification")
    if not isinstance(clarification, dict):
        return ""
    clarification_message = str(clarification.get("message") or "").strip()
    if clarification_message:
        return clarification_message
    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return ""
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if question:
            return question
    return ""


def direct_final_message(
    *,
    status: str,
    current_message: str,
    stage_summaries: list[str],
) -> str:
    """根据业务终态生成面向用户的快速修改结果消息。"""

    if status == "requires_user_input":
        return current_message or "请补充修改信息。"
    if status == "requires_planning":
        return current_message or "该需求需要正式设计工作流。"
    if status == "failed":
        return current_message or "快速修改执行失败，请查看阶段结果和日志。"
    return "；".join(stage_summaries) or "快速修改已完成并启动预览。"


def direct_test_log_paths(test_results: Any) -> list[str]:
    """从测试执行证据中提取可供界面展示的虚拟日志路径。"""

    paths: list[str] = []
    for check in test_results if isinstance(test_results, list) else []:
        execution = check.get("execution") if isinstance(check, dict) else None
        if not isinstance(execution, dict):
            continue
        for key in ("stdout_log", "stderr_log"):
            value = str(execution.get(key) or "").strip()
            if value and value not in paths:
                paths.append(value)
    return paths
