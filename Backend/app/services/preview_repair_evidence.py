"""组装当前启动尝试和最新验证失败的定向诊断上下文。"""

from pathlib import Path
from typing import Any

from app.services.preview_diagnostics import diagnostics_from_command, safe_diagnostic_path
from app.services.preview_runtime_state import read_logs, redact


def repair_evidence(workspace: str, record: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """优先使用本次修复后失败的验证结果，避免重诊断仍围绕旧启动错误。"""
    validation = previous.get("validation") or {}
    checks = [item for item in validation.get("test_results", []) if item.get("passed") is False]
    use_validation = bool(checks and previous.get("attemptId") == record.get("attemptId"))
    diagnostics = list(record.get("diagnostics") or [])
    if use_validation:
        diagnostics = [diagnostic for check in checks for diagnostic in diagnostics_from_command(
            workspace, str(check.get("id") or "validation"),
            "dependency" if str(check.get("id") or "").endswith("install") else "compile",
            str(check.get("evidence") or check.get("name") or "验证失败"), check.get("execution"),
        )]
    logs = read_logs(workspace)
    if use_validation:
        # 日志来自最新检查的受控执行结果，不把旧启动日志混成当前失败证据。
        logs = {"frontend": [], "backend": []}
    candidates: list[str] = []
    contexts: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        location = diagnostic.get("location") or {}
        path = safe_diagnostic_path(workspace, str(location.get("path") or ""))
        if not path or not (Path(workspace) / path).is_file() or path in candidates:
            continue
        candidates.append(path)
        line = location.get("line") or 1
        content: list[str] = []
        try:
            with (Path(workspace) / path).open(encoding="utf-8", errors="replace") as source:
                for number, text in enumerate(source, 1):
                    if number > line + 20:
                        break
                    if number >= max(1, line - 5):
                        content.append(f"{number}: {text[:1000]}")
        except OSError:
            continue
        contexts.append({"path": path, "line": location.get("line"), "column": location.get("column"), "content": redact("".join(content))[:8000]})
    log_paths = list(dict.fromkeys([
        *(path for diagnostic in diagnostics for path in diagnostic.get("logPaths", [])),
        *(f"/.devagentstudio/runtime/launch/{entry['name']}" for entries in logs.values() for entry in entries),
    ]))
    return {
        "launch": record, "diagnostics": diagnostics, "candidateFiles": candidates[:20],
        "sourceContext": contexts[:10], "logPaths": log_paths,
        "logs": logs, "validation": validation if use_validation else None,
        "evidenceSource": "validation" if use_validation else "launch",
    }


def evidence_markdown(evidence: dict[str, Any]) -> str:
    """在计划中展示确定性故障证据，避免用户只能看到泛化修复描述。"""
    lines: list[str] = []
    for diagnostic in evidence.get("diagnostics", [])[:10]:
        location = diagnostic.get("location") or {}
        path = str(location.get("path") or "")
        if location.get("line"):
            path += f":{location['line']}"
        if location.get("column"):
            path += f":{location['column']}"
        lines.append(f"- {diagnostic['stage']} / {diagnostic['category']}：{diagnostic['message']}" + (f"（{path}）" if path else ""))
    return "\n".join(lines)
