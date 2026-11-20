"""为可定位的代码类单测失败提供仅文件工具的修复输入。"""

import re
from pathlib import Path
from typing import Any

from app.agents.small_task.scope import is_unit_test_repair_code_path
from app.services.integration_repair_evidence import bounded_diagnostic_text
from app.services.preview_diagnostics import diagnostic_locations, sanitize_diagnostic_text
from app.services.small_task_context import repair_strategy
from app.services.small_task_scope import _path_matches_task, _task_paths


_CODE_FAILURE = re.compile(
    r"\b(?:TS\d{4}|compilation (?:error|failure)|syntaxerror|"
    r"Assertion(?:Failed)?Error|expected:|expect\()", re.IGNORECASE,
)
_DEPENDENCY_OR_TOOL_FAILURE = re.compile(
    r"\b(?:TS2307|TS2688|TS7016|ERR_PNPM_\w+|cannot (?:find|resolve) module|"
    r"(?:could not|failed to) (?:resolve|download)|(?:command|mvn|jest): not found)\b",
    re.IGNORECASE,
)
_STACK_LOCATION = re.compile(r"\(([^()\r\n]+\.(?:java|[jt]sx?)):(\d+(?::\d+)?)\)")


def _location_input(diagnostic: str, task: dict[str, Any]) -> str:
    """还原 Jest 堆栈位置，并仅用唯一的任务文件匹配 JUnit 简写位置。"""
    locations = [diagnostic]
    targets = task.get("target_files") or []
    for reported, line in _STACK_LOCATION.findall(diagnostic):
        if "/" not in reported and "\\" not in reported and reported.endswith(".java"):
            matching = [path for path in targets if Path(path).name == reported]
            if len(matching) != 1:
                continue
            reported = matching[0]
        locations.append(f"{reported}:{line}")
    # 括号只是堆栈的位置界定符，不能成为诊断文件路径的一部分。
    return "\n".join([*locations[1:], locations[0]])


def unit_test_code_packet(
    task: dict[str, Any], state: dict[str, Any], source: str,
) -> dict[str, Any] | None:
    """只在真实编译或断言诊断命中相应工程的授权源码时，交由平台复测。"""
    owner = task.get("owner")
    check_id = (task.get("source_ref") or {}).get("failed_check_id")
    workspace = state.get("workspace") or state.get("workspace_path")
    if (source != "unit_test.small_task" or owner not in {"frontend", "backend"}
            or check_id != f"{owner}_unit_tests" or not workspace):
        return None
    failure = task.get("failure_evidence") or {}
    attempt = failure.get("failed_attempt") or {}
    execution = attempt.get("execution") or {}
    if (
        attempt.get("failure_category") != "test_failure"
        or execution.get("timed_out") or execution.get("error")
        or execution.get("returncode") in (0, None)
    ):
        return None
    tails = dict.fromkeys(execution[key] for key in ("stderr_tail", "stdout_tail") if execution.get(key))
    diagnostic = sanitize_diagnostic_text("\n".join(tails) or str(failure.get("evidence") or ""))
    if not _CODE_FAILURE.search(diagnostic) or _DEPENDENCY_OR_TOOL_FAILURE.search(diagnostic):
        return None
    root = Path(workspace).resolve()
    locations = {}
    for location in diagnostic_locations(root, _location_input(diagnostic, task), str(execution.get("cwd") or "")):
        if is_unit_test_repair_code_path(location.path, owner) and _path_matches_task(location.path, task):
            locations.setdefault((location.path, location.line), location.model_dump())
        if len(locations) == 3:
            break
    if not locations:
        return None

    return {
        "schemaVersion": "small-task-packet.v1",
        "source": source,
        "kind": "repair",
        "owner": owner,
        "taskId": str(task.get("id") or ""),
        "executionRunId": str(state.get("active_run_id") or ""),
        "verificationMode": "platform_unit_test",
        "allowedPaths": _task_paths(task)[:100],
        "candidateFiles": list(dict.fromkeys(path for path, _ in locations)),
        "repairStrategy": bounded_diagnostic_text(repair_strategy(state), 800),
        "failureEvidence": {
            "checkId": check_id,
            "error": bounded_diagnostic_text(diagnostic, 2_400),
            "locations": list(locations.values()),
        },
    }
