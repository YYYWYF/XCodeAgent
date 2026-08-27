"""代码审查修复 Agent 的提示、调用和结果归一化。"""

from __future__ import annotations

import json
from typing import Any

from app.agents.tool_activity_stream import ToolActivityCallback, invoke_agent_with_tool_activity
from app.tools.code_review_pnpm import (
    PNPM_INSTALL_TOOL_NAME,
    read_pnpm_install_evidence,
)
from app.utils.model_output import extract_json_object


def build_code_review_repair_prompt(
    *,
    issues: list[dict[str, Any]],
    build_failures: list[dict[str, Any]],
    attempt: int,
    max_attempts: int,
) -> str:
    """构造一轮受限代码修复任务包。"""

    packet = {
        "attempt": attempt,
        "max_attempts": max_attempts,
        "issues": issues[:100],
        "build_failures": build_failures[:10],
        "allowed_roots": ["frontend", "backend/src/main/java"],
        "denied_paths": ["frontend/node_modules", "frontend/pnpm-lock.yaml:file_write"],
    }
    return (
        "Execute the bounded code-review repair packet below. Read the supplied issue files and "
        "the required Skill/rules reference first. Attempt every issue id in the packet. Build "
        "failures are additional evidence from the previous deterministic build and must be fixed "
        "only when the repair boundary allows it. Follow each issue's repair_actions exactly. "
        "For pnpm_install, edit package.json first and call pnpm_install_frontend exactly once; "
        "never edit pnpm-lock.yaml directly. Return only JSON with this exact shape:\n"
        '{"status":"completed|failed","summary":"...","attempted_issue_ids":[],'
        '"changed_files":[],"failure_reason":null}\n\n'
        f"RepairPacket:\n{json.dumps(packet, ensure_ascii=False, indent=2)}"
    )


def invoke_code_review_repair_agent(
    *,
    issues: list[dict[str, Any]],
    build_failures: list[dict[str, Any]],
    attempt: int,
    max_attempts: int,
    workspace: str | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> dict[str, Any]:
    """调用当前工作区的 CodeReviewRepairAgent。"""

    from app.agents import create_agent_bundle

    before_evidence = read_pnpm_install_evidence(workspace) if workspace else None
    before_execution_id = str((before_evidence or {}).get("execution_id") or "")
    observed_call_ids: set[str] = set()
    completed_call_ids: set[str] = set()
    failed_call_ids: set[str] = set()

    def observe(activity: dict[str, Any]) -> None:
        """记录专用 pnpm 工具活动并转发安全活动事件。"""

        if activity.get("tool") == PNPM_INSTALL_TOOL_NAME:
            call_id = str(activity.get("callId") or "").strip()
            if call_id:
                observed_call_ids.add(call_id)
                status = str(activity.get("status") or "")
                if status == "completed":
                    completed_call_ids.add(call_id)
                elif status == "failed":
                    failed_call_ids.add(call_id)
        if on_tool_activity is not None:
            on_tool_activity(activity)

    agent_output = invoke_agent_with_tool_activity(
        create_agent_bundle(workspace).code_review_repair,
        {
            "messages": [
                {
                    "role": "user",
                    "content": build_code_review_repair_prompt(
                        issues=issues,
                        build_failures=build_failures,
                        attempt=attempt,
                        max_attempts=max_attempts,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=observe,
    )
    after_evidence = read_pnpm_install_evidence(workspace) if workspace else None
    after_execution_id = str((after_evidence or {}).get("execution_id") or "")
    fresh_evidence = (
        after_evidence
        if after_execution_id and after_execution_id != before_execution_id
        else None
    )
    return {
        "agent_output": agent_output,
        "pnpm_install_call_count": len(observed_call_ids),
        "pnpm_install_called": bool(observed_call_ids),
        "pnpm_install_completed": bool(completed_call_ids),
        "pnpm_install_failed": bool(failed_call_ids),
        "pnpm_install": fresh_evidence,
    }


def normalize_code_review_repair_result(value: str | dict[str, Any]) -> dict[str, Any]:
    """严格归一化修复 Agent 的结构化结果。"""

    invocation = value if isinstance(value, dict) and "agent_output" in value else {}
    raw_value = invocation.get("agent_output") if invocation else value
    payload = raw_value if isinstance(raw_value, dict) else extract_json_object(raw_value)
    if not isinstance(payload, dict):
        raise ValueError("CodeReviewRepairAgent 未返回合法 JSON。")
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise ValueError("CodeReviewRepairAgent status 无效。")
    pnpm_install = _normalize_pnpm_install_evidence(invocation.get("pnpm_install"))
    return {
        "status": status,
        "summary": str(payload.get("summary") or "").strip()[:2_000],
        "attempted_issue_ids": _string_list(
            payload.get("attempted_issue_ids", payload.get("attemptedIssueIds")),
            limit=100,
        ),
        "changed_files": _string_list(
            payload.get("changed_files", payload.get("changedFiles")),
            limit=100,
        ),
        "failure_reason": str(
            payload.get("failure_reason", payload.get("failureReason")) or ""
        ).strip()[:2_000]
        or None,
        "pnpm_install_call_count": _safe_nonnegative_int(
            invocation.get("pnpm_install_call_count")
        ),
        "pnpm_install_called": bool(invocation.get("pnpm_install_called")),
        "pnpm_install_completed": bool(invocation.get("pnpm_install_completed")),
        "pnpm_install_failed": bool(invocation.get("pnpm_install_failed")),
        "pnpm_install": pnpm_install,
    }


def _normalize_pnpm_install_evidence(value: Any) -> dict[str, Any] | None:
    """裁剪专用 pnpm 工具证据并拒绝宿主绝对路径。"""

    if not isinstance(value, dict):
        return None
    command = value.get("command")
    if command != ["pnpm", "install"] or value.get("cwd") != "frontend":
        return None
    exit_code = value.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return None
    return {
        "execution_id": str(value.get("execution_id") or "")[:80],
        "status": "passed" if value.get("status") == "passed" and exit_code == 0 else "failed",
        "exit_code": exit_code,
        "timed_out": bool(value.get("timed_out")),
        "command": ["pnpm", "install"],
        "cwd": "frontend",
        "stdout_log": _safe_log_path(value.get("stdout_log")),
        "stderr_log": _safe_log_path(value.get("stderr_log")),
        "stdout_tail": str(value.get("stdout_tail") or "")[-4_000:],
        "stderr_tail": str(value.get("stderr_tail") or "")[-4_000:],
    }


def _safe_log_path(value: Any) -> str | None:
    """只保留代码审查运行目录下的相对日志引用。"""

    path = str(value or "").strip().replace("\\", "/").lstrip("/")
    prefix = ".xcodeagent/runtime/code-review/pnpm-install/"
    return path[:1_000] if path.startswith(prefix) and ".." not in path.split("/") else None


def _string_list(value: Any, *, limit: int) -> list[str]:
    """裁剪不可信 Agent 列表并保持顺序去重。"""

    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip()[:1_000] for item in value if str(item).strip()))[:limit]


def _safe_nonnegative_int(value: Any) -> int:
    """把不可信调用次数钳制为非负整数。"""

    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
