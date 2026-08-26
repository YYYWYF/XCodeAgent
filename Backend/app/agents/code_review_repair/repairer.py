"""代码审查修复 Agent 的提示、调用和结果归一化。"""

from __future__ import annotations

import json
from typing import Any

from app.agents.tool_activity_stream import ToolActivityCallback, invoke_agent_with_tool_activity
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
        "allowed_roots": ["frontend/src", "backend/src/main/java"],
    }
    return (
        "Execute the bounded code-review repair packet below. Read the supplied issue files and "
        "the required Skill/rules reference first. Attempt every issue id in the packet. Build "
        "failures are additional evidence from the previous deterministic build and must be fixed "
        "only when the source-root boundary allows it. Do not change files outside the two source "
        "roots. Return only JSON with this exact shape:\n"
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
) -> str:
    """调用当前工作区的 CodeReviewRepairAgent。"""

    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
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
        on_tool_activity=on_tool_activity,
    )


def normalize_code_review_repair_result(value: str | dict[str, Any]) -> dict[str, Any]:
    """严格归一化修复 Agent 的结构化结果。"""

    payload = value if isinstance(value, dict) else extract_json_object(value)
    if not isinstance(payload, dict):
        raise ValueError("CodeReviewRepairAgent 未返回合法 JSON。")
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise ValueError("CodeReviewRepairAgent status 无效。")
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
    }


def _string_list(value: Any, *, limit: int) -> list[str]:
    """裁剪不可信 Agent 列表并保持顺序去重。"""

    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip()[:1_000] for item in value if str(item).strip()))[:limit]
