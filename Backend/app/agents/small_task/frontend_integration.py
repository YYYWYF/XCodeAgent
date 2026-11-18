"""前端集成修复的任务包和仅提交结果的收尾。"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agents.integration_repair_result import invoke_repair_result
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.integration_repair_evidence import DIAGNOSTIC_CHARS, bounded_diagnostic_text, compact_json
from app.services.preview_diagnostics import diagnostic_locations, sanitize_diagnostic_text
from app.services.small_task_context import repair_candidate_files, repair_strategy
from app.services.small_task_scope import _path_matches_task, _task_paths


def is_frontend_integration(source: str | None, owner: Any) -> bool:
    """按任务的真实来源挑选前端集成专用策略。"""
    return source == "integration_test.small_task" and owner == "frontend"


def frontend_integration_packet(task: dict[str, Any], state: dict[str, Any], source: str) -> dict[str, Any]:
    """只为本任务投影诊断、授权路径和验收条件，按完整字段应用长度预算。"""
    workspace = str(state.get("workspace") or state.get("workspace_path") or "")
    failure = task.get("failure_evidence") or task.get("failureEvidence") or {}
    attempt = failure.get("failed_attempt") or {}
    execution = attempt.get("execution") or {}
    tails = list(dict.fromkeys(
        str(execution[key])[-2_000:] for key in ("error", "stderr_tail", "stdout_tail") if execution.get(key)
    ))
    diagnostic = "\n".join(tails) or str(failure.get("evidence") or "")[:8_000]
    packet: dict[str, Any] = {
        "schemaVersion": "small-task-packet.v1",
        "source": source, "owner": "frontend", "kind": "repair",
        "taskId": str(task.get("id") or "")[:240],
        "executionRunId": str(state.get("active_run_id") or ""),
        "description": bounded_diagnostic_text(str(task.get("description") or "局部代码修复"), 400),
        "failureEvidence": {
            "checkId": str(attempt.get("check_id") or "")[:80],
            "category": str(attempt.get("failure_category") or "")[:80],
            "exitCode": execution.get("returncode"),
            "error": bounded_diagnostic_text(diagnostic, 3_200),
        },
        "repairStrategy": bounded_diagnostic_text(repair_strategy(state), 800),
        "allowedPaths": [], "candidateFiles": [],
        "engineeringAcceptanceChecks": [], "businessAcceptanceChecks": [],
    }
    candidates = []
    if workspace:
        root = Path(workspace).resolve()
        candidates = [
            location.path for location in diagnostic_locations(root, sanitize_diagnostic_text(diagnostic), str(execution.get("cwd") or ""))
            if _path_matches_task(location.path, task) and (root / location.path).is_file()
        ]
    candidates = list(dict.fromkeys([*candidates, *repair_candidate_files(task, workspace or None)]))
    categories = (
        ("allowedPaths", _task_paths(task)[:100], 640),
        ("candidateFiles", [path for path in candidates if workspace and (Path(workspace) / path).is_file()][:8], 640),
    )
    for field, values, max_length in categories:
        for value in values:
            if len(value) > max_length:
                continue
            packet[field].append(value)
            if len(compact_json(packet)) > DIAGNOSTIC_CHARS - 2_200:
                packet[field].pop()
    for field, checks in (
        ("engineeringAcceptanceChecks", task.get("acceptance_checks") or task.get("engineering_acceptance_checks")
         or task.get("acceptance_criteria")),
        ("businessAcceptanceChecks", task.get("business_acceptance_checks") or task.get("businessAcceptanceChecks")),
    ):
        if not isinstance(checks, list):
            continue
        for check in checks[:10]:
            value = (check[:600] if isinstance(check, str) else {
                key: val[:240] if isinstance(val, str) else val
                for key, val in check.items()
                if isinstance(key, str) and len(key) <= 80 and type(val) in {str, bool, int}
            } if isinstance(check, dict) else None)
            if value:
                packet[field].append(value)
                if len(compact_json(packet)) > DIAGNOSTIC_CHARS:
                    packet[field].pop()
    return packet


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]


class RepairEscalation(BaseModel):
    """只允许收尾提交现有 SmallTask 确认所需的信息。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    reasonCode: ShortText
    reason: ShortText
    requestedPaths: list[ShortText] = Field(max_length=12)
    workflowIntent: ShortText


class FrontendRepairResult(BaseModel):
    """结构化收尾无工作区工具，真实位置来自平台差异。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["completed", "already_satisfied", "requires_user_confirmation", "requires_workflow", "failed"]
    summary: Annotated[ShortText, StringConstraints(min_length=1)]
    failureReason: ShortText | None
    escalation: RepairEscalation | None


def finalize_frontend_repair(
    *, note: str, receipts: list[dict[str, str]], authorized_diff: list[dict[str, str]],
) -> str | None:
    """尝试一次结果收尾，发布的文件与验收事实仍由平台提供。"""
    result = invoke_repair_result(
        create_chat_model(Settings.from_env(), max_tokens_override=2_048, max_retries_override=0),
        FrontendRepairResult,
        instructions=(
            "Recover the final SmallTask result from the supplied evidence. Do not perform repairs. "
            "An authorized diff establishes edits only; platform retest decides whether they work. "
            "Use completed only when an authorized diff exists. Preserve any evidenced scope or formal "
            "revision request. Do not invent authorization, changes, or verification. Write a Chinese summary."
        ),
        evidence=compact_json({
            "finalReply": sanitize_diagnostic_text(note)[-2_000:],
            "toolReceipts": receipts[-12:],
            "authorizedDiff": authorized_diff[:12],
        }),
    )
    if result is None or (result.status == "completed" and not authorized_diff):
        return None
    if result.status in {"requires_user_confirmation", "requires_workflow"} and (
        not result.escalation or not result.escalation.reason
        or (result.status == "requires_user_confirmation" and not result.escalation.requestedPaths)
        or (result.status == "requires_workflow" and not result.escalation.reasonCode)
    ):
        return None
    return result.model_dump_json()
