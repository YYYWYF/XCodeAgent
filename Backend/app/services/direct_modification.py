from __future__ import annotations

from pathlib import Path
from typing import Any

from app.utils.model_output import extract_json_object


MAX_DIRECT_MODIFICATION_SUMMARY_CHARS = 4_000

_DYNAMIC_PATH_ACTION_MARKERS = (
    "改",
    "修改",
    "更改",
    "更新",
    "调整",
    "配置",
    "修复",
    "重构",
    "替换",
    "写入",
    "创建",
    "添加",
    "新增",
    "删除",
    "移除",
    "安装",
    "升级",
    "降级",
    "change",
    "configure",
    "edit",
    "fix",
    "modify",
    "refactor",
    "replace",
    "update",
    "write",
    "create",
    "add",
    "remove",
    "install",
    "upgrade",
    "downgrade",
)
_DYNAMIC_PATH_DENIED_PARTS = {
    ".git",
    ".xcodeagent",
    ".next",
    ".nuxt",
    ".pnpm",
    ".turbo",
    ".venv",
    "build",
    "coverage",
    "dist",
    "migration",
    "migrations",
    "node_modules",
    "schema",
    "target",
    "vendor",
}
_DYNAMIC_PATH_DENIED_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "bun.lock",
    "bun.lockb",
    "cargo.lock",
    "composer.lock",
    "gradle.lockfile",
    "id_ed25519",
    "id_rsa",
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "yarn.lock",
}


def validated_dynamic_workspace_paths(
    *,
    workspace: str | None,
    request: str,
    owner: str,
    target_paths: list[str] | tuple[str, ...],
) -> list[str]:
    """把当前修改需求所需的现有安全文件精确加入本轮追加授权。"""

    if owner not in {"frontend", "backend", "fullstack"}:
        return []
    normalized_request = request.casefold()
    if not any(marker in normalized_request for marker in _DYNAMIC_PATH_ACTION_MARKERS):
        return []
    root = Path(workspace or "").expanduser()
    if not workspace or not root.is_dir():
        return []
    root = root.resolve()
    approved: list[str] = []
    for raw_path in target_paths:
        raw_text = str(raw_path).strip()
        if raw_text.startswith(("/", "\\")) or (
            len(raw_text) >= 3
            and raw_text[1] == ":"
            and raw_text[2] in {"/", "\\"}
        ):
            continue
        normalized = raw_text.replace("\\", "/").lstrip("/")
        if not _is_safe_dynamic_path_candidate(normalized, owner=owner):
            continue
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and normalized not in approved:
            approved.append(normalized)
    return approved[:100]


def _is_safe_dynamic_path_candidate(path: str, *, owner: str) -> bool:
    """校验动态路径必须精确、归属正确且不触及敏感或生成目录。"""

    if not path or any(character in path for character in "*?[]{}"):
        return False
    parts = [part for part in path.split("/") if part]
    lowered_parts = [part.casefold() for part in parts]
    if any(part in {".", ".."} for part in parts) or not lowered_parts:
        return False
    if any(part == ".env" or part.startswith(".env.") for part in lowered_parts):
        return False
    if lowered_parts[-1] in _DYNAMIC_PATH_DENIED_NAMES:
        return False
    if any(part in _DYNAMIC_PATH_DENIED_PARTS for part in lowered_parts):
        return False
    if owner == "frontend" and "frontend" not in lowered_parts:
        return False
    if owner == "backend" and "backend" not in lowered_parts:
        return False
    if owner == "fullstack" and not ({"frontend", "backend"} & set(lowered_parts)):
        return False
    return True


def parse_direct_modification_agent_result(agent_note: str) -> dict[str, Any]:
    """把快速 Agent 的最终 JSON 归一化为稳定阶段结果。"""

    payload = extract_json_object(agent_note) or {}
    status = str(payload.get("status") or "failed")
    already_satisfied = bool(
        payload.get("alreadySatisfied") or status == "already_satisfied"
    )
    if status not in {
        "completed",
        "failed",
        "already_satisfied",
        "requires_user_confirmation",
        "requires_workflow",
    }:
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
        "escalation": _normalize_escalation(payload.get("escalation")),
        "agentNote": agent_note[-8_000:],
    }


def _normalize_escalation(value: Any) -> dict[str, Any]:
    """裁剪共享小任务 Agent 的升级信息，供自由对话继续路由。"""

    payload = value if isinstance(value, dict) else {}
    raw_analysis = payload.get("changeImpactAnalysis") or payload.get("change_impact_analysis")
    return {
        "reasonCode": str(
            payload.get("reasonCode") or payload.get("reason_code") or ""
        )[:120],
        "reason": str(payload.get("reason") or "")[:2_000],
        "requestedPaths": _string_list(
            payload.get("requestedPaths") or payload.get("requested_paths"),
            limit=100,
        ),
        "requestedResources": _bounded_json_value(
            payload.get("requestedResources") or payload.get("requested_resources")
        ),
        "workflowIntent": str(
            payload.get("workflowIntent") or payload.get("workflow_intent") or ""
        )[:120],
        # 只保留有界的原始分析；进入 Router 前仍会按当前 JSON 重新复核，
        # 不能因为 SmallTask 在升级包里携带了分析就直接取得写权限。
        "changeImpactAnalysis": (
            _bounded_json_value(raw_analysis) if isinstance(raw_analysis, dict) else {}
        ),
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
            "partialChanges": False,
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
            "partialChanges": False,
            "failureReason": "Agent 报告完成，但工作区没有实际代码差异。",
        }
    # 工具调用或模型自报失败不能覆盖已经落盘的授权范围内差异；后续节点仍需独立验收。
    if normalized.get("status") == "failed" and changed_paths:
        return {**normalized, "partialChanges": True}
    return normalized


def direct_path_matches_owner(path: str, owner: str) -> bool:
    """按虚拟工程目录判断一次代码变更是否属于当前 Agent。"""

    parts = [part.casefold() for part in path.replace("\\", "/").split("/") if part]
    if owner == "workspace":
        lowered = "/".join(parts)
        return bool(parts) and not (
            any(part == ".env" or part.startswith(".env.") for part in parts)
            or ".xcodeagent/" in lowered
            or "frontend" in parts
            or "backend" in parts
        )
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
