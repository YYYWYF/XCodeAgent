
from app.branding import WORKSPACE_ARTIFACT_DIR
"""统一预览启动事实、分端日志及当前进程健康投影。"""

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app.services.preview_runtime_guard import maintenance_owner


_lock = RLock()
LOG_LIMIT = 32_768
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LOG_FILES = {
    "frontend": ("install.stdout.log", "install.stderr.log", "frontend.stdout.log", "frontend.stderr.log"),
    "backend": ("backend-build.stdout.log", "backend-build.stderr.log", "backend-repackage.stdout.log", "backend-repackage.stderr.log", "backend.stdout.log", "backend.stderr.log"),
}


def _coerce_port(value: Any) -> int | None:
    """把启动器或旧快照中的端口值规范为合法整数。"""

    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port <= 65535 else None


def _port_from_url(value: Any) -> int | None:
    """从前端预览地址提取端口，兼容已有快照没有独立 port 字段的情况。"""

    if not value:
        return None
    try:
        return _coerce_port(urlparse(str(value)).port)
    except ValueError:
        return None


def _service_port(root: Path, layer: str, part: dict[str, Any]) -> int | None:
    """优先使用启动器端口，旧快照则从地址或受控启动日志补齐。"""

    port = _coerce_port(part.get("port"))
    if port is not None:
        return port
    port = _port_from_url(part.get("url"))
    if port is not None or layer != "backend":
        return port
    try:
        from app.services.backend_project_launcher import _detect_backend_port

        return _detect_backend_port(
            stdout_log=root / "backend.stdout.log",
            stdout_offset=0,
            stderr_log=root / "backend.stderr.log",
            stderr_offset=0,
        )
    except (ImportError, OSError, ValueError):
        return None


def runtime_root(workspace: str | Path) -> Path:
    """返回平台管理的标准预览目录。"""
    return Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "runtime" / "launch"


def redact(text: str) -> str:
    """在日志离开服务端前隐藏凭据和连接字符串中的密码。"""
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)", "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)[\"']?\s*[=:]\s*[\"']?)[^\s,;\"']+", r"\1[REDACTED]", text)
    text = re.sub(r"(?is)(<(?:password|secret|token)>).*?(</(?:password|secret|token)>)", r"\1[REDACTED]\2", text)
    return re.sub(r"(://[^\s/:]+:)[^\s@]+@", r"\1[REDACTED]@", text)


def _plain_log_text(text: str) -> str:
    """去掉终端颜色控制码后再脱敏，避免抽屉显示乱码或颜色码绕过脱敏。"""

    return redact(ANSI_ESCAPE_PATTERN.sub("", text))


def read_record(workspace: str | Path) -> dict[str, Any]:
    """读取当前启动事实；没有启动记录时返回未启动状态。"""
    path = runtime_root(workspace) / "preview-runtime.json"
    with _lock:
        if not path.exists():
            return {"attemptId": "", "status": "stopped", "frontend": {"status": "stopped"}, "backend": {"status": "stopped"}}
        return json.loads(path.read_text(encoding="utf-8"))


def write_record(workspace: str | Path, value: dict[str, Any]) -> None:
    """原子写入当前启动事实，避免日志订阅读到半个 JSON。"""
    with _lock:
        root = runtime_root(workspace)
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f".preview-{uuid4().hex}.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(root / "preview-runtime.json")


def begin_attempt(workspace: str | Path) -> str:
    """清空本轮标准日志并创建新的启动身份，避免混入上一轮错误。"""
    with _lock:
        root = runtime_root(workspace)
        root.mkdir(parents=True, exist_ok=True)
        for names in LOG_FILES.values():
            for name in names:
                path = root / name
                if path.exists() and not path.is_symlink():
                    path.write_text("", encoding="utf-8")
        attempt = uuid4().hex
        write_record(workspace, {"attemptId": attempt, "startedAt": datetime.now(UTC).isoformat(), "status": "starting", "frontend": {"status": "stopped"}, "backend": {"status": "starting"}})
        return attempt


def record_progress(workspace: str | Path, stage: str, status: str, message: str) -> None:
    """记录启动阶段，供独立抽屉实时读取。"""
    with _lock:
        value = read_record(workspace)
        if stage in {"frontend", "backend"}:
            current = value.get(stage) if isinstance(value.get(stage), dict) else {}
            # 进度事件没有 PID 和就绪证据，完整启动结果落盘前必须保持 starting，
            # 否则 watch 会把缺少 PID 的临时 running 误判为服务异常退出。
            projected_status = "starting" if status in {"running", "completed"} else status
            value[stage] = {
                **current,
                "status": projected_status,
                "message": redact(message),
            }
        write_record(workspace, value)


def mark_interrupted_attempt(workspace: str | Path, *, layer: str) -> None:
    """把平台进程重启时遗留的启动中状态收口为可重新启动的失败状态。"""

    with _lock:
        value = read_record(workspace)
        message = "预览启动任务意外中断，已清理残留进程，请重新启动服务。"
        value.update(
            status="failed",
            message=message,
            previewUrl=None,
            failedStage="launch_interrupted",
        )
        if layer in {"frontend", "backend"}:
            part = value.get(layer) if isinstance(value.get(layer), dict) else {}
            value[layer] = {**part, "status": "failed", "message": message}
        write_record(workspace, value)


def finish_attempt(workspace: str | Path, result: dict[str, Any]) -> None:
    """保存启动结果与受管理 PID，隐藏内部进程对象。"""
    with _lock:
        value = read_record(workspace)
        value.update({"status": result.get("status", "failed"), "message": redact(str(result.get("message") or "")), "previewUrl": result.get("preview_url") if result.get("status") == "running" else None, "failedStage": result.get("failed_stage")})
        for layer in ("backend", "frontend"):
            part = result.get(layer) or {}
            server = part.get("server") or {}
            value[layer] = {"status": part.get("status", "stopped"), "message": redact(str(part.get("message") or ("未启动：后端启动失败" if layer == "frontend" and result.get("status") == "failed" else ""))), "pid": server.get("pid"), "port": _coerce_port(server.get("port") or part.get("port")) or _port_from_url(part.get("preview_url")), "url": part.get("preview_url"), "command": str(server.get("command") or ""), "ready": server.get("ready", False)}
        write_record(workspace, value)


def record_frontend_runtime(workspace: str | Path, result: dict[str, Any]) -> None:
    """同步性能检查复用的标准前端服务，避免公共抽屉保留旧 PID。"""
    with _lock:
        value = read_record(workspace)
        server = result.get("server") or {}
        value["frontend"] = {
            "status": result.get("status", "stopped"),
            "message": redact(str(result.get("message") or "")),
            "pid": server.get("pid"),
            "port": _coerce_port(server.get("port") or result.get("port")) or _port_from_url(result.get("preview_url")),
            "url": result.get("preview_url"),
            "ready": server.get("ready", False),
        }
        states = {value["frontend"]["status"], value["backend"]["status"]}
        value["status"] = "failed" if "failed" in states else "running" if states <= {"running", "skipped"} else "stopped"
        value["previewUrl"] = result.get("preview_url") if result.get("status") == "running" else None
        if result.get("status") == "failed":
            value["failedStage"] = "frontend_start"
        write_record(workspace, value)


def read_logs(workspace: str | Path) -> dict[str, Any]:
    """只读取白名单日志的有界尾部，不接受客户端路径。"""
    root = runtime_root(workspace)
    logs: dict[str, Any] = {}
    for layer, names in LOG_FILES.items():
        entries: list[dict[str, Any]] = []
        for name in names:
            path = root / name
            if not path.is_file() or path.is_symlink():
                continue
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(max(0, size - LOG_LIMIT))
                content = _plain_log_text(stream.read(LOG_LIMIT).decode("utf-8", errors="replace"))
            if content:
                entries.append({"name": name, "stream": "stderr" if ".stderr." in name else "stdout", "stage": "install" if name.startswith("install") else "build" if "build" in name or "repackage" in name else "start", "content": content, "truncated": size > LOG_LIMIT})
        logs[layer] = entries
    return logs


def _frontend_runtime_is_ready(root: Path, part: dict[str, Any]) -> bool:
    """使用实时 HTTP 探测，并以本轮受管进程的启动日志作为受限降级证据。"""
    from app.services.frontend_project_launcher import (
        _dev_server_log_is_ready,
        _dev_server_stderr_is_fatal,
        _preview_is_ready,
    )

    url = str(part.get("url") or "").strip()
    if not url:
        return False
    if _preview_is_ready(url):
        return True
    # 桌面后端可能无法主动访问 Renderer 绑定的 loopback 端口。此时只有
    # 当前 PID 仍存活、Launcher 已完成 Ready 检查、本轮日志包含就绪标志且
    # stderr 没有致命错误时，才允许保留 running，避免只信历史成功记录。
    return bool(
        part.get("ready")
        and _dev_server_log_is_ready(root / "frontend.stdout.log", 0)
        and not _dev_server_stderr_is_fatal(root / "frontend.stderr.log")
    )


def runtime_snapshot(workspace: str | Path, *, logs: bool = True) -> dict[str, Any]:
    """校验启动事实对应的进程仍存在，并投影最新状态与占用。"""
    with _lock:
        value = deepcopy(read_record(workspace))
        if logs:
            value["logs"] = read_logs(workspace)
    root = runtime_root(workspace)
    for layer in ("frontend", "backend"):
        part = value[layer]
        if _coerce_port(part.get("port")) is None:
            port = _service_port(root, layer, part)
            if port is not None:
                part["port"] = port
        if part.get("status") == "running":
            try:
                pid = int(part.get("pid") or 0)
                if pid <= 0:
                    raise ProcessLookupError()
                os.kill(pid, 0)
                pid_path = root / f"{layer}.pid"
                if not pid_path.is_file() or int(pid_path.read_text().strip()) != pid:
                    raise ProcessLookupError()
                if layer == "frontend":
                    if not _frontend_runtime_is_ready(root, part):
                        raise ProcessLookupError()
                else:
                    from app.services.backend_project_launcher import _backend_logs_are_ready
                    if not _backend_logs_are_ready(stdout_log=root / "backend.stdout.log", stdout_offset=0, stderr_log=root / "backend.stderr.log", stderr_offset=0):
                        raise ProcessLookupError()
            except (OSError, ValueError):
                message = "服务进程已退出或就绪检测失败，请重启服务。"
                part.update(status="failed", message=message)
                value.update(status="failed", message=message, failedStage=f"{layer}_process", previewUrl=None)
    # 必须在实时健康校准之后计算；否则历史 running 在本轮变成 failed 时，
    # UI 会显示失败却仍把诊断按钮禁用。
    value["repairAvailable"] = bool(
        value.get("attemptId")
        and value.get("status") == "failed"
        and value.get("failedStage") not in {"stop", "launch_interrupted"}
    )
    value["maintenance"] = maintenance_owner(workspace)
    return value
