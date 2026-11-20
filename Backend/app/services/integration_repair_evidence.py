"""将本轮集成检查压缩为规划和执行共用的有界诊断。"""

import json
from itertools import islice
from pathlib import Path
import re
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR_NAME
from app.services.backend_startup_diagnostics import read_log_tail
from app.services.preview_diagnostics import diagnostic_locations, sanitize_diagnostic_text, safe_diagnostic_path

DIAGNOSTIC_CHARS = 12_000
_GENERATED_DIRS = {".git", ".devagentstudio", ".next", ".venv", "build", "dist", "node_modules", "target"}
_MISSING_NODE_MODULE = re.compile(
    r"""(?:cannot find module|can't resolve)\s+['"]((?:@[\w.-]+/)?[\w.-]+)(?:/[^'"]*)?['"]""",
    re.IGNORECASE,
)


def compact_json(value: Any) -> str:
    """紧凑序列化，使用实际传给模型的字符数校验输入预算。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bounded_diagnostic_text(value: str, limit: int) -> str:
    """裁剪脱敏文本，按 JSON 转义后的实际长度保留字段预算。"""
    if limit <= 2:
        return ""
    text = sanitize_diagnostic_text(value)[:limit]
    while (size := len(compact_json(text))) > limit:
        text = text[:max(0, len(text) * (limit - 2) // size)]
    return text


def repair_log_path(root: Path, runtime: Path, value: str) -> Path | None:
    """只接纳本轮检查引用中位于当前运行目录的真实日志。"""
    if not value or ".." in Path(value).parts:
        return None
    path = Path(value)
    if not path.is_absolute() or value.startswith(f"/{WORKSPACE_ARTIFACT_DIR_NAME}/"):
        path = root / value.lstrip("/")
    try:
        resolved = path.resolve()
        if (resolved.is_relative_to(root) and resolved.is_relative_to(runtime)
                and resolved.suffix == ".log" and not path.is_symlink() and resolved.is_file()):
            return resolved
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _source_location(root: Path, value: dict[str, Any], cwd: str) -> dict[str, Any] | None:
    """验证位置来自实际工程文件，并拒绝构建输出和敏感路径。"""
    raw = value.get("path")
    line = value.get("line")
    if not isinstance(raw, str) or type(line) is not int or not 1 <= line <= 100_000:
        return None
    try:
        relative = safe_diagnostic_path(root, raw, cwd)
        if (relative and len(relative) <= 320 and (root / relative).is_file()
                and not any(part.casefold() in _GENERATED_DIRS for part in Path(relative).parts)):
            return {"path": relative, "line": line}
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _diagnosis(root: Path, runtime: Path, request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """保留一份去重后的失败摘要，初始解析只消费报告已有的输出。"""
    check = request.get("failed_check") or {}
    execution = check.get("execution") or (request.get("failed_attempt") or {}).get("execution") or {}
    check_id = str(check.get("id") or "")[:80]
    category = str(check.get("failure_category") or "test_failure")[:80]
    tails = list(dict.fromkeys(
        str(execution[key])[-4_000:] for key in ("error", "stderr_tail", "stdout_tail") if execution.get(key)
    ))
    # runner 已把 stdout/stderr 拼到 evidence，存在尾部字段时避免复制整份 evidence。
    reason = str(execution.get("root_cause") or check.get("root_cause") or "")[:500]
    text = sanitize_diagnostic_text(
        "\n".join(dict.fromkeys(filter(None, [reason, *tails]))) or str(request.get("evidence") or "")[:8_000]
    )
    locations = diagnostic_locations(root, text, str(execution.get("cwd") or ""))
    log_refs = [
        execution.get(f"{stream}_log_virtual") or execution.get(f"{stream}_log")
        for stream in ("stderr", "stdout")
    ]
    logs = list(dict.fromkeys(
        path.relative_to(root).as_posix() for value in log_refs if isinstance(value, str)
        if (path := repair_log_path(root, runtime, value)) and len(path.relative_to(root).as_posix()) <= 320
    ))
    owners = request.get("owners") or [request.get("owner")]
    return {
        "checkId": check_id, "owners": [owner for owner in owners if owner in {"frontend", "backend", "database"}],
        "category": category, "exitCode": execution.get("returncode"),
        "timedOut": bool(execution.get("timed_out")), "error": bounded_diagnostic_text(text, 1_200), "logs": logs,
    }, [
        *(item.get("location") or {} for item in (check.get("diagnostics") or []) if isinstance(item, dict)),
        *(item.model_dump() for item in locations),
    ]


def integration_repair_evidence(
    root: Path, runtime: Path, requests: list[dict[str, Any]], scoped_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """按检查和真实位置去重，并在添加可选项时保留完整 JSON 和总量边界。"""
    evidence: dict[str, Any] = {"checks": [], "locations": [], "authorizedPaths": {}}
    locations: dict[tuple[str, int], dict[str, Any]] = {}
    checks: set[str] = set()
    for request in requests:
        check, reported_locations = _diagnosis(root, runtime, request)
        check_id = check["checkId"]
        if check_id in checks or len(checks) == 6:
            continue
        evidence["checks"].append(check)
        if len(compact_json(evidence)) > DIAGNOSTIC_CHARS:
            evidence["checks"].pop()
            continue
        checks.add(check_id)
        for reported in reported_locations:
            location = _source_location(root, reported, str(
                ((request.get("failed_check") or {}).get("execution") or {}).get("cwd") or ""
            ))
            if not location:
                continue
            key = (location["path"], location["line"])
            if key in locations or len(locations) == 3:
                continue
            locations[key] = {"checkId": check_id, **location}
    for location in locations.values():
        evidence["locations"].append(location)
        if len(compact_json(evidence)) > DIAGNOSTIC_CHARS:
            evidence["locations"].pop()
    for task in scoped_tasks:
        owner = task.get("owner")
        if owner not in {"frontend", "backend", "database"}:
            continue
        paths = evidence["authorizedPaths"].setdefault(owner, [])
        for path in task.get("allowed_paths") or []:
            if not isinstance(path, str) or path in paths or len(paths) == 8 or len(path) > 240:
                continue
            paths.append(path)
            if len(compact_json(evidence)) > DIAGNOSTIC_CHARS:
                paths.pop()
        if not paths:
            evidence["authorizedPaths"].pop(owner)
    return evidence


def supplement_repair_evidence(root: Path, runtime: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    """只读取初始诊断引用的有限窗口，不接受模型提供的文件路径。"""
    result = {**evidence}
    windows: list[dict[str, str]] = []
    result["windows"] = windows
    references = [(item["path"], item["line"]) for item in evidence["locations"]]
    log_names = dict.fromkeys(log for check in evidence["checks"] for log in check["logs"])
    references.extend((log, None) for log in islice(log_names, 2))
    # 为补充证据留空间，仍保留检查、位置、授权和日志引用。
    result["checks"] = [{**check, "error": check["error"][:360]} for check in evidence["checks"]]
    for name, line in references:
        try:
            if line is None:
                log = repair_log_path(root, runtime, name)
                snippet = read_log_tail(log, limit=1_800) if log else ""
            elif _source_location(root, {"path": name, "line": line}, "") and (root / name).stat().st_size <= 512_000:
                with (root / name).open(encoding="utf-8", errors="replace") as source:
                    start = max(line - 6, 1)
                    snippet = "".join(
                        f"{number}: {text.rstrip()[:160]}\n"
                        for number, text in enumerate(islice(source, start - 1, line + 6), start)
                    )
            else:
                continue
        except (OSError, ValueError):
            continue
        if snippet:
            windows.append({"reference": name, "excerpt": sanitize_diagnostic_text(snippet)[:1_800]})
            if len(compact_json(result)) > DIAGNOSTIC_CHARS:
                windows.pop()
    return result


def unit_test_dependency_evidence(root: Path, evidence: dict[str, Any]) -> dict[str, str] | None:
    """校验缺失的前端包已由工程声明，供规划器选择可执行的安装修复。"""
    package_names = {
        match.group(1)
        for check in evidence["checks"] if check["checkId"] == "frontend_unit_tests"
        for match in _MISSING_NODE_MODULE.finditer(check["error"])
    }
    if not package_names:
        return None
    for manifest in ("frontend/package.json", "Frontend/package.json", "package.json"):
        if not (relative := safe_diagnostic_path(root, manifest)):
            continue
        path = root / relative
        try:
            if path.stat().st_size > 128_000:
                continue
            project = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            declared = project.get(field) if isinstance(project, dict) else None
            if isinstance(declared, dict) and (matches := sorted(package_names.intersection(declared))):
                return {"manifest": relative, "missingDeclaredPackage": matches[0]}
        return None
    return None
