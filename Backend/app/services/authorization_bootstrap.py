"""执行生成应用中模板提供的权限数据库 Bootstrap。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any

from app.utils.subprocess_output import subprocess_output_text
from app.services.workspace_process_registry import workspace_process_registry


AUTHORIZATION_BOOTSTRAP_TIMEOUT_SECONDS = 600
_WORKSPACE_LOCKS: dict[str, Lock] = {}
_WORKSPACE_LOCKS_GUARD = Lock()


def clear_authorization_bootstrap_lock(workspace: str | Path) -> bool:
    """在应用停止后移除目标工作区的权限初始化互斥锁缓存。"""

    key = _authorization_workspace_key(workspace)
    with _WORKSPACE_LOCKS_GUARD:
        return _WORKSPACE_LOCKS.pop(key, None) is not None


def authorization_bootstrap_enabled(technical_plan: Any) -> bool:
    """判断已确认技术规划是否要求执行模板权限初始化。"""

    if not isinstance(technical_plan, dict):
        return False
    manifest = technical_plan.get("authorization_manifest")
    return (
        technical_plan.get("artifact_type") == "technical-plan"
        and technical_plan.get("confirmation_status") == "confirmed"
        and isinstance(manifest, dict)
        and manifest.get("enabled") is True
        and isinstance(manifest.get("fingerprint"), str)
        and bool(manifest["fingerprint"].strip())
    )


def run_authorization_bootstrap(
    workspace: str | Path,
    technical_plan: dict[str, Any],
) -> dict[str, Any]:
    """以固定模板脚本执行或复用权限 Bootstrap，并返回可安全投影的结果。"""

    root = Path(workspace).expanduser().resolve()
    manifest = (
        technical_plan.get("authorization_manifest")
        if isinstance(technical_plan, dict)
        else None
    )
    fingerprint = (
        str(manifest.get("fingerprint") or "").strip()
        if isinstance(manifest, dict)
        else ""
    )
    if not authorization_bootstrap_enabled(technical_plan):
        return {"status": "skipped", "reason": "authorization_disabled"}
    if not root.is_dir():
        return _failed_result(fingerprint, "workspace_invalid", "工作区不存在或不是目录。")

    lock = _workspace_lock(root)
    with lock:
        runtime_root = (
            root
            / ".xcodeagent"
            / "runtime"
            / "authorization-bootstrap"
            / _fingerprint_key(fingerprint)
        )
        marker_path = runtime_root / "result.json"
        if _successful_marker(marker_path, fingerprint):
            return {
                "status": "reused",
                "manifest_fingerprint": fingerprint,
                "message": "当前权限 manifest 已成功初始化，复用已有结果。",
                "log_directory": _relative_path(runtime_root, root),
            }

        command, error = _command_for_workspace(root)
        if error:
            return _failed_result(fingerprint, "template_contract_invalid", error)
        runtime_root.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            completed = workspace_process_registry.run(
                command,
                workspace=root,
                cwd=root,
                env=_bootstrap_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=AUTHORIZATION_BOOTSTRAP_TIMEOUT_SECONDS,
                check=False,
            )
            stdout = subprocess_output_text(completed.stdout)
            stderr = subprocess_output_text(completed.stderr)
            exit_code = completed.returncode
            failure_category = "script_failed" if exit_code else ""
        except subprocess.TimeoutExpired as exc:
            stdout = subprocess_output_text(exc.stdout)
            stderr = subprocess_output_text(exc.stderr)
            exit_code = None
            failure_category = "timeout"
        except OSError as exc:
            stdout = ""
            stderr = str(exc)
            exit_code = None
            failure_category = "process_start_failed"

        duration_ms = round((time.monotonic() - started) * 1000)
        stdout_path = runtime_root / "stdout.log"
        stderr_path = runtime_root / "stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        base = {
            "manifest_fingerprint": fingerprint,
            "duration_ms": duration_ms,
            "exit_code": exit_code,
            "log_directory": _relative_path(runtime_root, root),
            "stdout_log": _relative_path(stdout_path, root),
            "stderr_log": _relative_path(stderr_path, root),
        }
        if failure_category:
            message = (
                f"权限数据库初始化超时（超过 {AUTHORIZATION_BOOTSTRAP_TIMEOUT_SECONDS}s）。"
                if failure_category == "timeout"
                else "权限数据库初始化脚本执行失败，请检查本地 Bootstrap 日志。"
            )
            return {
                "status": "failed",
                "failure_category": failure_category,
                "message": message,
                **base,
            }
        result = {"status": "executed", "message": "权限数据库初始化完成。", **base}
        _write_json_atomically(marker_path, result)
        return result


def _command_for_workspace(root: Path) -> tuple[list[str], str | None]:
    """按宿主系统返回唯一允许的模板脚本调用命令。"""

    backend = root / "backend"
    if not backend.is_dir():
        return [], "生成项目缺少 backend 目录。"
    if os.name == "nt":
        script = backend / "scripts" / "bootstrap-authorization.ps1"
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    else:
        script = backend / "scripts" / "bootstrap-authorization.sh"
        command = ["/bin/bash", str(script)]
    try:
        resolved_script = script.resolve(strict=True)
        resolved_backend = backend.resolve(strict=True)
    except OSError:
        return [], f"权限 Bootstrap 模板脚本不存在：{script.relative_to(root)}。"
    if script.is_symlink() or not resolved_script.is_relative_to(resolved_backend):
        return [], "权限 Bootstrap 脚本必须是 backend 目录内的普通文件。"
    if not (backend / "docs" / "auth" / "sql" / "ddl.sql").is_file():
        return [], "权限 Bootstrap 缺少 backend/docs/auth/sql/ddl.sql。"
    return command, None


def _bootstrap_environment() -> dict[str, str]:
    """隔离宿主数据库变量，避免其他应用连接配置泄漏到当前项目。"""

    blocked_prefixes = ("DB_", "MYSQL_", "SPRING_DATASOURCE_")
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(blocked_prefixes)
    }


def _workspace_lock(root: Path) -> Lock:
    """获取同一工作区的进程内互斥锁，避免并发重复启动脚本。"""

    key = _authorization_workspace_key(root)
    with _WORKSPACE_LOCKS_GUARD:
        return _WORKSPACE_LOCKS.setdefault(key, Lock())


def _authorization_workspace_key(workspace: str | Path) -> str:
    """生成权限初始化锁使用的规范化绝对工作区键。"""

    return os.path.normcase(str(Path(workspace).expanduser().resolve(strict=False)))


def _successful_marker(path: Path, fingerprint: str) -> bool:
    """只把同一权限指纹的完整成功记录视为可复用结果。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("status") == "executed"
        and value.get("manifest_fingerprint") == fingerprint
    )


def _fingerprint_key(fingerprint: str) -> str:
    """将 manifest 指纹转换为跨平台安全的运行时目录名。"""

    return fingerprint.removeprefix("sha256:") or "unknown"


def _relative_path(path: Path, root: Path) -> str:
    """返回仅限当前工作区的相对日志引用。"""

    return path.resolve().relative_to(root).as_posix()


def _failed_result(fingerprint: str, category: str, message: str) -> dict[str, Any]:
    """构造不泄露环境或数据库细节的失败结果。"""

    return {
        "status": "failed",
        "manifest_fingerprint": fingerprint,
        "failure_category": category,
        "message": message,
    }


def _write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    """原子持久化成功标记，避免中断留下可误复用的半文件。"""

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        temporary_path = Path(stream.name)
    temporary_path.replace(path)
