"""从异常和实际命令输出提取有界、脱敏、可定位的启动诊断。"""

import json
import re
from pathlib import Path
from typing import Any

from app.domain.preview_diagnostics import DiagnosticCommand, DiagnosticLocation, PreviewDiagnostic
from app.services.backend_startup_diagnostics import read_log_tail
from app.workspace.workspace import SENSITIVE_FILE_NAMES


# 匹配编译器常用的 path:line:column、path(line,column)、path:[line,column]，不按文件名分流。
_LOCATION = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\s\"'<>|]+\.[A-Za-z0-9_-]+)"
    r"(?::\[?(?P<line>\d+)(?:[:,](?P<column>\d+))?\]?|\((?P<paren_line>\d+),(?P<paren_column>\d+)\))"
)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def safe_diagnostic_path(workspace: str | Path, value: str, cwd: str = "") -> str | None:
    """只保留工作区内非敏感文件身份，拒绝越界、依赖目录和平台内部状态。"""
    root = Path(workspace).resolve()
    raw = value.replace("\\", "/")
    if ".." in Path(raw).parts:
        return None
    candidates = [Path(raw)] if Path(raw).is_absolute() else [Path(cwd) / raw, root / raw]
    # Agent 使用虚拟根路径；宿主机绝对路径仅在真实位于工作区时接纳。
    if raw.startswith(("/frontend/", "/Frontend/", "/backend/", "/Backend/")):
        candidates.append(root / raw.lstrip("/"))
    valid: list[str] = []
    for candidate in candidates:
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(root):
            continue
        relative = resolved.relative_to(root)
        parts = relative.parts
        if not parts or any(
            part.casefold() in {name.casefold() for name in SENSITIVE_FILE_NAMES}
            or part.casefold().startswith(".env")
            or part in {"node_modules", ".git", ".devagentstudio"}
            for part in parts
        ):
            continue
        if resolved.is_file():
            return relative.as_posix()
        valid.append(relative.as_posix())
    return valid[0] if valid else None


def _safe_text(value: str) -> str:
    """复用预览日志脱敏规则，移除终端控制码。"""
    from app.services.preview_runtime_state import redact

    return redact(_ANSI.sub("", value))


def _safe_argv(argv: list[str]) -> list[str]:
    """同时隐藏内联凭据和命令行中独立出现的凭据参数值。"""
    result: list[str] = []
    sensitive_value = False
    for arg in argv:
        result.append("[REDACTED]" if sensitive_value else _safe_text(arg))
        sensitive_value = bool(re.fullmatch(r"--?(?:password|passwd|secret|token|api[_-]?key|authorization)", arg, re.I))
    return result


def make_diagnostic(
    *, stage: str, category: str, message: str, code: str | None = None,
    location: DiagnosticLocation | None = None, command: DiagnosticCommand | None = None,
    log_paths: list[str] | None = None, excerpt: str = "",
) -> dict[str, Any]:
    """通过严格合同生成可持久化且可直接进入 AG-UI 的安全证据。"""
    return PreviewDiagnostic.model_validate({
        "stage": stage, "category": category, "message": _safe_text(message)[:4000],
        "code": code, "location": location,
        "command": command.model_copy(update={"argv": _safe_argv(command.argv)}) if command else None,
        "logPaths": log_paths or [], "excerpt": _safe_text(excerpt)[-8000:],
    }).model_dump(mode="json")


def diagnostic_from_exception(
    workspace: str | Path, stage: str, error: Exception, path: Path | None = None,
) -> dict[str, Any]:
    """按异常类型提取真实位置和源码窗口，不根据文件名猜测修复。"""
    line = getattr(error, "lineno", None)
    column = getattr(error, "colno", None) or getattr(error, "offset", None)
    position = getattr(error, "position", None)
    if isinstance(position, tuple) and len(position) == 2:
        line, column = position[0], position[1] + 1
    line = line if isinstance(line, int) and line > 0 else None
    column = column if isinstance(column, int) and column > 0 else None
    target = path or (Path(error.filename) if getattr(error, "filename", None) else None)
    relative = safe_diagnostic_path(workspace, str(target)) if target else None
    location = DiagnosticLocation(path=relative, line=line, column=column) if relative else None
    excerpt = ""
    if target and relative and line:
        try:
            # 窗口仅来自已验证路径，避免读到越界路径或符号链接目标。
            with (Path(workspace) / relative).open(encoding="utf-8", errors="replace") as source:
                for number, text in enumerate(source, 1):
                    if number > line + 2:
                        break
                    if number >= max(1, line - 2):
                        excerpt += f"{number}: {text[:1000]}"
        except OSError:
            pass
    parsing = isinstance(error, (ValueError, SyntaxError)) or isinstance(position, tuple)
    return make_diagnostic(
        stage=stage, category="parse" if parsing else "process",
        message=str(error), code=type(error).__name__, location=location, excerpt=excerpt,
    )


def read_json_object(workspace: Path, path: Path, stage: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """读取 JSON 对象并保留格式、编码、文件访问和根结构的原始错误。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON document root must be an object")
        return value, []
    except (OSError, ValueError) as error:
        return None, [diagnostic_from_exception(workspace, stage, error, path)]


def diagnostics_from_command(
    workspace: str | Path, stage: str, category: str, message: str,
    execution: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """保留命令退出、超时、日志及编译位置，未知格式仍保留原始输出供 Agent 判断。"""
    execution = execution or {}
    root = Path(workspace).resolve()
    cwd = str(execution.get("cwd") or root)
    text_parts = [str(execution.get(key) or "") for key in ("error", "stdout_tail", "stderr_tail")]
    log_paths: list[str] = []
    for key in ("stdout_log", "stderr_log"):
        path = Path(str(execution.get(key) or ""))
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path.is_symlink() or not path.is_file():
            continue
        if resolved.relative_to(root).parts[:2] != (".devagentstudio", "runtime") or resolved.suffix != ".log":
            continue
        log_paths.append("/" + resolved.relative_to(root).as_posix())
        try:
            text_parts.append(read_log_tail(path, limit=16000))
        except OSError:
            continue
    text = _safe_text("\n".join(text_parts))[-24000:]
    command = DiagnosticCommand(
        argv=[str(arg) for arg in execution.get("argv", [])], cwd=cwd,
        exitCode=execution.get("returncode"), timedOut=bool(execution.get("timed_out")),
    )
    locations: list[DiagnosticLocation | None] = []
    for match in _LOCATION.finditer(text):
        relative = safe_diagnostic_path(root, match["path"], cwd)
        if relative:
            locations.append(DiagnosticLocation(
                path=relative, line=int(match["line"] or match["paren_line"]),
                column=int(match["column"] or match["paren_column"]) if match["column"] or match["paren_column"] else None,
            ))
    return [make_diagnostic(
        stage=stage, category=category, message=message, command=command,
        location=location, log_paths=log_paths, excerpt=text,
    ) for location in (locations[:10] or [None])]


def launch_diagnostics(workspace: str | Path, layer: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """在失败生产边界统一适配安装、构建、进程和就绪证据。"""
    if result.get("status") != "failed":
        return []
    if result.get("diagnostics"):
        return [PreviewDiagnostic.model_validate(item).model_dump(mode="json") for item in result["diagnostics"]]
    for key, category in (("install", "dependency"), ("repackage", "compile"), ("build", "compile"), ("server", "process")):
        execution = result.get(key)
        if isinstance(execution, dict) and (key == "server" or execution.get("returncode") != 0):
            if key == "server" and execution.get("returncode") is None and not execution.get("error"):
                category = "readiness"
            return diagnostics_from_command(workspace, f"{layer}_{key}", category, str(result.get("message") or "启动失败"), execution)
    return [make_diagnostic(stage=str(result.get("failed_stage") or f"{layer}_validation"), category="process", message=str(result.get("message") or "启动失败"))]
