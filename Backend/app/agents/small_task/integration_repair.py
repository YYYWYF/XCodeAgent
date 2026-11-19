"""前后端集成修复的有界任务包和仅提交结果的收尾。"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agents.integration_repair_result import invoke_repair_result
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.backend_startup_diagnostics import read_log_tail, redact_startup_text, startup_root_cause
from app.services.integration_repair_evidence import (
    DIAGNOSTIC_CHARS, bounded_diagnostic_text, compact_json, repair_log_path,
)
from app.services.preview_diagnostics import diagnostic_locations, safe_diagnostic_path, sanitize_diagnostic_text
from app.services.small_task_context import repair_candidate_files, repair_strategy
from app.services.small_task_scope import _path_matches_task, _task_paths
from app.workspace.spec_documents import WORKFLOW_ARTIFACT_DIR


def is_restricted_integration_task(source: str | None, owner: Any) -> bool:
    """仅对集成测试派发的前后端任务启用受限策略。"""
    return source == "integration_test.small_task" and owner in ("frontend", "backend")


def _backend_log_windows(root: Path, check_id: str, execution: dict[str, Any]) -> dict[str, str]:
    """只补读本轮报告引用的对应检查日志，每个输出流最多读取 4,000 字节。"""
    if check_id not in {"backend_build", "backend_startup"}:
        return {}
    directory = root / WORKFLOW_ARTIFACT_DIR / "runtime" / "tests" / check_id
    windows: dict[str, str] = {}
    for stream in ("stdout", "stderr"):
        reference = execution.get(f"{stream}_log_virtual") or execution.get(f"{stream}_log")
        if not isinstance(reference, str):
            continue
        path = repair_log_path(root, directory, reference)
        if not path or path.name != f"{stream}.log":
            continue
        if path.parent != directory and not (
            check_id == "backend_startup" and path.parent.parent == directory
        ):
            continue
        try:
            excerpt = sanitize_diagnostic_text(redact_startup_text(read_log_tail(path, limit=4_000), {}))
        except OSError:
            continue
        inline = sanitize_diagnostic_text(redact_startup_text(str(execution.get(f"{stream}_tail") or ""), {}))
        if excerpt.strip() and excerpt.strip() not in inline:
            windows[stream] = excerpt
    return windows


def _backend_diagnostics(
    root: Path | None, task: dict[str, Any], check_id: str, execution: dict[str, Any], diagnostic: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """优先保存实际 Java 诊断位置或启动异常链，再按需补充日志窗口。"""
    cwd = str(execution.get("cwd") or "")
    is_startup = check_id == "backend_startup"
    root_cause = str(execution.get("root_cause") or "")[:3_600] if is_startup else ""
    if is_startup and not root_cause:
        root_cause = startup_root_cause(diagnostic, "")
    locations = diagnostic_locations(root, diagnostic, cwd) if root is not None else []
    windows: dict[str, str] = {}
    if root is not None and (
        (is_startup and not root_cause) or (not is_startup and not locations and not execution.get("error"))
    ):
        windows = _backend_log_windows(root, check_id, execution)
        locations.extend(diagnostic_locations(root, "\n".join(windows.values()), cwd))
        if is_startup and windows:
            root_cause = startup_root_cause(windows.get("stdout", ""), windows.get("stderr", ""))
    evidence: dict[str, Any] = {"timedOut": bool(execution.get("timed_out"))}
    if root_cause:
        evidence["rootCause"] = bounded_diagnostic_text(redact_startup_text(root_cause, {}), 3_600)
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for location in locations:
        if (root is not None and len(compact_json(location.path)) <= 320
                and _path_matches_task(location.path, task) and (root / location.path).is_file()):
            unique[(location.path, location.line)] = location.model_dump(exclude_none=True)
    if unique:
        evidence["locations"] = list(unique.values())[:3]
    return evidence, windows


def _backend_error_summary(diagnostic: str, check_id: str) -> str:
    """让 Maven 的关键错误行优先进入正文，避免被 INFO 和构建尾注挤掉。"""
    if check_id == "backend_build":
        errors = list(dict.fromkeys(line.strip() for line in diagnostic.splitlines() if line.startswith("[ERROR] ")))
        if errors:
            return "\n".join(errors)
    return diagnostic[-2_000:]


def integration_repair_packet(task: dict[str, Any], state: dict[str, Any], source: str) -> dict[str, Any]:
    """只为本任务投影诊断、授权路径和验收条件，按完整字段应用长度预算。"""
    workspace = str(state.get("workspace") or state.get("workspace_path") or "")
    failure = task.get("failure_evidence") or task.get("failureEvidence") or {}
    attempt = failure.get("failed_attempt") or {}
    execution = attempt.get("execution") or {}
    owner = task.get("owner")
    tail_length = 4_000 if owner == "backend" else 2_000
    tails = list(dict.fromkeys(
        str(execution[key])[-tail_length:] for key in ("error", "stderr_tail", "stdout_tail") if execution.get(key)
    ))
    diagnostic = "\n".join(tails) or str(failure.get("evidence") or "")[:8_000]
    check_id = str(attempt.get("check_id") or "")
    if owner == "backend":
        diagnostic = sanitize_diagnostic_text(redact_startup_text(diagnostic, {}))
    exit_code = execution.get("returncode")
    evidence: dict[str, Any] = {
        "checkId": bounded_diagnostic_text(check_id, 80),
        "category": bounded_diagnostic_text(str(attempt.get("failure_category") or ""), 80),
        "exitCode": exit_code if type(exit_code) is int and -(2**31) <= exit_code < 2**32 else None,
        "error": (
            bounded_diagnostic_text(_backend_error_summary(diagnostic, check_id), 2_000)
            if owner == "backend" else bounded_diagnostic_text(diagnostic, 3_200)
        ),
    }
    candidates: list[str] = []
    windows: dict[str, str] = {}
    root = Path(workspace).resolve() if workspace else None
    if owner == "backend":
        backend_evidence, windows = _backend_diagnostics(
            root, task, check_id, execution, diagnostic,
        )
        evidence.update(backend_evidence)
        candidates = [location["path"] for location in backend_evidence.get("locations", [])]
    elif root is not None:
        candidates = [
            location.path for location in diagnostic_locations(
                root, sanitize_diagnostic_text(diagnostic), str(execution.get("cwd") or ""),
            )
            if _path_matches_task(location.path, task) and (root / location.path).is_file()
        ]
    run_id = str(state.get("active_run_id") or "")
    packet: dict[str, Any] = {
        "schemaVersion": "small-task-packet.v1",
        "source": source, "owner": owner, "kind": "repair",
        "taskId": str(task.get("id") or "")[:240],
        # 超长运行标识仍由 RunnableConfig 绑定取消，不裁成另一个标识。
        "executionRunId": run_id if len(compact_json(run_id)) <= 500 else "",
        "description": bounded_diagnostic_text(str(task.get("description") or "局部代码修复"), 400),
        "failureEvidence": evidence,
        "repairStrategy": bounded_diagnostic_text(repair_strategy(state), 800),
        "allowedPaths": [], "candidateFiles": [],
        "engineeringAcceptanceChecks": [], "businessAcceptanceChecks": [],
    }
    if root is not None:
        candidates.extend(
            relative for path in repair_candidate_files(task, workspace)
            if (relative := safe_diagnostic_path(root, path)) and _path_matches_task(relative, task)
            and (root / relative).is_file()
        )
    categories = (
        ("allowedPaths", _task_paths(task)[:100], 640),
        ("candidateFiles", list(dict.fromkeys(candidates))[:8], 640),
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
            value = (bounded_diagnostic_text(check, 600) if isinstance(check, str) else {
                key: bounded_diagnostic_text(val, 240) if isinstance(val, str) else val
                for key, val in check.items()
                if isinstance(key, str) and len(key) <= 80 and type(val) in {str, bool, int}
            } if isinstance(check, dict) else None)
            if value:
                packet[field].append(value)
                if len(compact_json(packet)) > DIAGNOSTIC_CHARS:
                    packet[field].pop()
    for stream, excerpt in windows.items():
        field = f"{stream}Excerpt"
        available = min(1_600, DIAGNOSTIC_CHARS - len(compact_json(packet)) - len(field) - 4)
        value = bounded_diagnostic_text(excerpt[-1_600:], available)
        if value:
            evidence[field] = value
    return packet


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]


class RepairEscalation(BaseModel):
    """只允许收尾提交现有 SmallTask 确认所需的信息。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    reasonCode: ShortText
    reason: ShortText
    requestedPaths: list[ShortText] = Field(max_length=12)
    workflowIntent: ShortText


class IntegrationRepairResult(BaseModel):
    """结构化收尾无工作区工具，真实位置来自平台差异。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["completed", "already_satisfied", "requires_user_confirmation", "requires_workflow", "failed"]
    summary: Annotated[ShortText, StringConstraints(min_length=1)]
    failureReason: ShortText | None
    escalation: RepairEscalation | None


def finalize_integration_repair(
    *, note: str, receipts: list[dict[str, str]], authorized_diff: list[dict[str, str]],
) -> str | None:
    """尝试一次结果收尾，发布的文件与验收事实仍由平台提供。"""
    reply = sanitize_diagnostic_text(note)
    if len(reply) > 2_000:
        reply = reply[:980] + "\n[...truncated...]\n" + reply[-1_000:]
    result = invoke_repair_result(
        create_chat_model(Settings.from_env(), max_tokens_override=2_048, max_retries_override=0),
        IntegrationRepairResult,
        instructions=(
            "Recover the final SmallTask result from the supplied evidence. Do not perform repairs. "
            "An authorized diff establishes edits only; platform retest decides whether they work. "
            "Use completed only when an authorized diff exists. Preserve any evidenced scope or formal "
            "revision request. Do not invent authorization, changes, or verification. Write a Chinese summary."
        ),
        evidence=compact_json({
            "finalReply": reply,
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
