"""把二次修改的预览故障转换为本轮可定位的修复证据。"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from app.services.backend_project_launcher import find_backend_project_root
from app.services.backend_startup_diagnostics import database_resource_failure, read_log_tail
from app.services.direct_modification import direct_path_matches_owner
from app.services.integration_repair_evidence import repair_log_path
from app.services.preview_diagnostics import diagnostic_locations, safe_diagnostic_path, sanitize_diagnostic_text
from app.services.preview_runtime_state import runtime_root
from app.services.small_task_context import repair_candidate_files
from app.services.test_error_logs import record_test_error_log


_LOG_LIMIT = 32_000
_GENERATED_DIRS = {".git", ".devagentstudio", ".next", "build", "dist", "node_modules", "target"}
_ENVIRONMENT_ERROR = re.compile(
    r"EADDRINUSE|Address already in use|Port\s+\d+\s+(?:was|is)\s+(?:already\s+)?in use|"
    r"ECONNREFUSED|ENETUNREACH|ENOTFOUND|ECONNRESET|UnknownHostException|"
    r"Connection refused|Network is unreachable|Permission denied|No space left on device|"
    r"(?:Could not transfer artifact|Non-resolvable parent POM).*?(?:timed out|PKIX path building failed)",
    re.IGNORECASE,
)
_JAVA_CLASS = r"[\w$]+(?:\.[\w$]+)*"
_FAILED_TEST_CLASS = re.compile(
    rf"Tests run:\s*\d+,\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),[^\n]*?"
    rf"(?:--\s*in|\s+in)\s+({_JAVA_CLASS})"
)
_JAVA_FRAME = re.compile(rf"\bat\s+({_JAVA_CLASS})\.[\w$<>]+\(([\w$]+\.java):\d+\)")
_VITE_IMPORTER = re.compile(
    r'''(?:Could not resolve|Failed to resolve import)[^\n]+?\bfrom\s+["']([^"'\n]+)["']|'''
    r"^\s*file:\s*(.+?)(?::\d+|\s*$)", re.MULTILINE | re.IGNORECASE,
)
_FRONTEND_CONFIG = re.compile(r"(?:package\.json|tsconfig(?:\.[\w-]+)?\.json|(?:vite|rsbuild|webpack)\.config\.[cm]?[jt]s)")


def capture_direct_restart_failure(root: Path, checks: list[dict[str, Any]]) -> None:
    """在启动锁内保存不可变日志，并把同一次可修复故障仅交给一个实际责任方。"""

    failed = [item for item in checks if item.get("passed") is False and item.get("startup")]
    if not failed:
        return
    launch = failed[0]["startup"].get("launch") or {}
    owner, part, raw_execution = _failed_operation(launch)
    logs_dir = runtime_root(root)
    output = _captured_output(root, logs_dir, raw_execution)
    diagnostics = part.get("diagnostics") or launch.get("diagnostics") or []
    message = sanitize_diagnostic_text(str(part.get("message") or failed[0]["evidence"]))
    runtime_message = "\n".join(
        sanitize_diagnostic_text(str(item.get("message") or "")) for item in diagnostics
    )[:4_000]
    text = "\n".join((message, runtime_message, *output.values()))
    cwd = str(raw_execution.get("cwd") or root)
    paths = (
        _repair_paths(root, owner, cwd, text, diagnostics)
        if owner and not raw_execution.get("timed_out") and not raw_execution.get("error")
        and not _ENVIRONMENT_ERROR.search(text) and not database_resource_failure(text)
        else []
    )
    # 公开的检查标签按变更范围显示，实际责任方只用于本次修复请求。
    primary = next((item for item in failed if item.get("layer") == owner), failed[0])
    command = shlex.join([sanitize_diagnostic_text(str(arg)) for arg in raw_execution.get("argv", [])])
    summary = {key: launch[key] for key in ("status", "failed_stage", "attempt_id") if key in launch}
    primary.update(evidence=message, command=command, execution={
        "cwd": cwd,
        "returncode": raw_execution.get("returncode"),
        "timed_out": bool(raw_execution.get("timed_out")),
        "stdout_tail": output.get("stdout", "")[-2_000:],
        "stderr_tail": output.get("stderr", "")[-2_000:],
    })
    record_test_error_log(root, logs_dir, primary, captured_output=output)
    for item in failed:
        item.update(
            evidence=message,
            repairable=False,
            startup={**item["startup"], "launch": summary},
            errorLogReferences=primary["errorLogReferences"],
        )
    if paths:
        primary.update(repairable=True, repair_owner=owner, restart_repair_paths=paths)
        primary["evidence"] = f"{message}\n故障文件：{', '.join(paths[:5])}"


def _failed_operation(launch: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """选择实际失败的命令，避免把成功的后端构建当作启动故障证据。"""

    stage = launch.get("failed_stage")
    if stage == "stop":
        return "", {}, {}
    backend = launch.get("backend") or {}
    if backend.get("status") == "failed":
        stage = backend.get("failed_stage") or stage
        operation = {
            "backend_build": "build", "backend_repackage": "repackage", "backend_start": "server",
            "backend_database_resource": "server",
        }.get(stage)
        owner = "backend" if operation and stage != "backend_database_resource" else ""
        return owner, backend, backend.get(operation) or {}
    frontend = launch.get("frontend") or {}
    if frontend.get("status") == "failed":
        return "frontend", frontend, frontend.get("server") or frontend.get("install") or {}
    return "", {}, {}


def _captured_output(root: Path, logs_dir: Path, execution: dict[str, Any]) -> dict[str, str]:
    """只快照标准预览的受控日志，裁剪并脱敏后才进入报告和模型。"""

    output: dict[str, str] = {}
    for stream in ("stdout", "stderr"):
        path = repair_log_path(root, logs_dir, str(execution.get(f"{stream}_log") or ""))
        if path:
            try:
                output[stream] = sanitize_diagnostic_text(read_log_tail(path, limit=_LOG_LIMIT))[-_LOG_LIMIT:]
            except OSError:
                # 保留已捕获的另一条流及启动器自己的诊断。
                continue
    return output


def _repair_paths(
    root: Path, owner: str, cwd: str, text: str, diagnostics: list[dict[str, Any]],
) -> list[str]:
    """复用编译位置，并补齐 Java 堆栈和 Vite 无行号 importer 的文件身份。"""

    reported = [
        str((item.get("location") or {}).get("path") or "") for item in diagnostics
    ]
    reported.extend(item.path for item in diagnostic_locations(root, text, cwd))
    # Maven 的绝对源码路径可能含空格，复用单测修复的编译器位置解析。
    reported.extend(repair_candidate_files({
        "allowed_paths": [owner],
        "failure_evidence": {"failed_attempt": {"execution": {"cwd": cwd, "stderr_tail": text[-24_000:]}}},
    }, str(root)))
    if owner == "backend":
        project = find_backend_project_root(root)
        if project:
            reported.extend(_java_source_paths(project, text))
    else:
        reported.extend(match[1] or match[2] for match in _VITE_IMPORTER.finditer(text))

    candidates: list[str] = []
    for raw in dict.fromkeys(reported):
        if path := verified_restart_repair_path(root, owner, raw, cwd):
            candidates.append(path)
    if owner == "backend":
        for test in list(dict.fromkeys(candidates))[:20]:
            if source := _referenced_java_source(root, test):
                candidates.append(source)
    return list(dict.fromkeys(candidates))[:20]


def _java_source_paths(project: Path, text: str) -> list[str]:
    """按 Surefire 失败测试类和 JVM 栈帧解析精确的包路径。"""

    paths: list[str] = []
    for failure in _FAILED_TEST_CLASS.finditer(text):
        if int(failure[1]) or int(failure[2]):
            relative = failure[3].split("$", 1)[0].replace(".", "/")
            paths.append(str(project / "src/test/java" / f"{relative}.java"))
    for frame in _JAVA_FRAME.finditer(text):
        package, _, class_name = frame[1].rpartition(".")
        if class_name.split("$", 1)[0] + ".java" != frame[2]:
            continue
        relative = Path(package.replace(".", "/")) / frame[2]
        paths.extend(str(project / directory / relative) for directory in ("src/main/java", "src/test/java"))
    return paths


def _referenced_java_source(root: Path, test: str) -> str | None:
    """普通断言失败无业务栈帧时，补入失败测试明确引用的同包被测类。"""

    project, separator, package_path = test.partition("/src/test/java/")
    if not separator or not package_path.endswith("Test.java"):
        return None
    source = verified_restart_repair_path(
        root, "backend", f"{project}/src/main/java/{package_path.removesuffix('Test.java')}.java",
    )
    if source:
        try:
            with (root / test).open(encoding="utf-8", errors="replace") as stream:
                content = stream.read(_LOG_LIMIT)
        except OSError:
            return None
        if re.search(rf"\b{re.escape(Path(source).stem)}\b", content):
            return source
    return None


def verified_restart_repair_path(root: Path, owner: str, raw: str, cwd: str = "") -> str | None:
    """仅授权存在的同侧源码或工程配置，并在派发前再次复用这条边界。"""

    path = safe_diagnostic_path(root, raw, cwd) if raw else None
    if not path or not direct_path_matches_owner(path, owner) or not (root / path).is_file():
        return None
    parts = Path(path).parts
    if any(part.casefold() in _GENERATED_DIRS for part in parts) or any(char in path for char in "*?[]"):
        return None
    relative = "/".join(parts[1:])
    if owner == "frontend":
        return path if relative.startswith("src/") or _FRONTEND_CONFIG.fullmatch(relative) else None
    if relative.startswith(("src/main/java/", "src/test/java/")) and relative.endswith(".java"):
        return path
    if relative == "pom.xml" or (
        relative.startswith("src/main/resources/") and Path(relative).suffix in {".yml", ".yaml", ".properties", ".xml", ".json"}
    ):
        return path
    return None
