"""后端启动检查的有界日志、根因摘要与敏感信息清理。"""
from __future__ import annotations

import re
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

OUTPUT_LIMIT = 4_000
LOG_READ_LIMIT = 256_000


def startup_source_fingerprint(root: Path) -> str:
    """记录构建相关源码及应用配置，等待确认期间有修改时使启动缓存失效。"""
    files: set[Path] = set()
    for relative in ('backend', 'Backend', 'src', '.mvn', '.xcodeagent/datasource'):
        directory = root / relative
        if not directory.is_dir():
            continue
        for current, directories, names in os.walk(directory):
            directories[:] = [name for name in directories if name not in {'.git', 'target', 'node_modules'}]
            files.update(Path(current) / name for name in names)
    files.update(root / name for name in ('pom.xml', 'mvnw', 'mvnw.cmd', '.xcodeagent/application.json'))
    digest = sha256()
    for path in sorted(files):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b'\0')
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


def read_log_tail(path: Path, limit: int = LOG_READ_LIMIT) -> str:
    """只读取日志末尾，避免大体积启动日志拖慢检测和模型输入。"""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read().decode("utf-8", errors="replace")


def redact_startup_text(text: str, environment: dict[str, str]) -> str:
    """清理日志中的凭据值及常见连接串、配置项中的认证字段。"""
    secrets = {
        value for key, value in environment.items()
        if value and re.search(r"password|pwd|secret|token|api_?key", key, re.I)
    }
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*)([^\s,;&]+)",
        r"\1[REDACTED]", text,
    )
    return re.sub(r"(://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)


def sanitize_startup_log(path: Path, environment: dict[str, str]) -> None:
    """进程结束后逐行清理完整日志，保留可供修复 Agent 读取的安全证据。"""
    temporary = path.with_suffix(path.suffix + ".sanitized")
    try:
        with path.open(encoding="utf-8", errors="replace") as source, temporary.open(
            "w", encoding="utf-8"
        ) as target:
            for line in source:
                target.write(redact_startup_text(line, environment))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def startup_root_cause(stdout: str, stderr: str) -> str:
    """保留顶层异常和最后的原因链，让最底层类缺失等信息不被栈帧挤掉。"""
    lines = [line.strip() for line in (stdout + "\n" + stderr).splitlines()]
    causes = [line for line in lines if re.search(
        r"Caused by:|\b[\w.$]*(?:Exception|Error):|APPLICATION FAILED TO START|Application run failed",
        line,
    )]
    selected = list(dict.fromkeys(causes[:1] + causes[-6:]))
    return "\n".join(line[:600] for line in selected)[-3_600:]


def startup_failure_packet(failure: dict[str, Any]) -> dict[str, Any] | None:
    """抽取独立且紧凑的启动失败包，防止整个报告被裁剪时丢失修复所需证据。"""
    attempt = failure.get("failed_attempt", {})
    if not isinstance(attempt, dict) or attempt.get("check_id") != "backend_startup":
        return None
    execution = attempt.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    return {
        "checkId": "backend_startup",
        "rootCause": execution.get("root_cause"),
        "reason": str(attempt.get("agent_note") or "")[:OUTPUT_LIMIT],
        "command": attempt.get("command"),
        "cwd": execution.get("cwd"),
        "returncode": execution.get("returncode"),
        "timedOut": execution.get("timed_out"),
        "stdoutLog": execution.get("stdout_log_virtual"),
        "stderrLog": execution.get("stderr_log_virtual"),
        "stdoutTail": str(execution.get("stdout_tail") or "")[-OUTPUT_LIMIT:],
        "stderrTail": str(execution.get("stderr_tail") or "")[-OUTPUT_LIMIT:],
    }
