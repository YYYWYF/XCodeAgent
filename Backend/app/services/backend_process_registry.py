"""管理工作区 Java 预览进程、运行副本及安全终止。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.backend_runtime_jar import BACKEND_PROCESS_FILENAME, read_backend_process_record, remove_backend_jar
from app.utils.process_liveness import pid_is_running
from app.utils.subprocess_output import subprocess_output_text
from app.utils.subprocess_platform import WINDOWS_CREATE_NO_WINDOW


BACKEND_STOP_TIMEOUT_SECONDS = 5
BACKEND_STOP_POLL_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True)
class _RegisteredBackend:
    """内存中的进程及副本，记录写入失败时仍能回收。"""

    process: subprocess.Popen[bytes]
    jar: Path | None


_BACKEND_PROCESSES: dict[str, _RegisteredBackend] = {}
_BACKEND_LAUNCH_LOCKS: dict[str, threading.RLock] = {}
_BACKEND_REGISTRY_GUARD = threading.Lock()


def stop_owned_backend_previews() -> None:
    """后台退场只停止本次持有的 Java 预览句柄，不扫描或终止其它实例进程。"""
    with _BACKEND_REGISTRY_GUARD:
        entries = list(_BACKEND_PROCESSES.items())
    for workspace, process in entries:
        result = terminate_backend_process(workspace=Path(workspace), process=process, pid_file=None)
        if not result.get("success"):
            raise RuntimeError("后台退出时后端预览服务未能完成清理。")


def backend_launch_lock(workspace: Path) -> threading.RLock:
    """返回指定工作区的串行启动锁，避免停止、构建和启动相互交叉。"""

    workspace_key = _workspace_key(workspace)
    with _BACKEND_REGISTRY_GUARD:
        return _BACKEND_LAUNCH_LOCKS.setdefault(workspace_key, threading.RLock())


def register_backend_process(
    workspace: Path,
    process: subprocess.Popen[bytes],
    jar: Path | None = None,
) -> None:
    """登记指定工作区最新启动的 Java 进程。"""

    with _BACKEND_REGISTRY_GUARD:
        _BACKEND_PROCESSES[_workspace_key(workspace)] = _RegisteredBackend(process, jar)


def clear_backend_process_registry_workspace(workspace: str | Path) -> bool:
    """预览进程停止后移除目标工作区遗留的进程和启动锁缓存。"""

    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    workspace_key = _workspace_key(workspace_path)
    with _BACKEND_REGISTRY_GUARD:
        registered = _BACKEND_PROCESSES.get(workspace_key)
        if registered is not None and registered.process.poll() is None:
            return False
        removed_process = _BACKEND_PROCESSES.pop(workspace_key, None) is not None
        removed_lock = _BACKEND_LAUNCH_LOCKS.pop(workspace_key, None) is not None
        return removed_process or removed_lock


def stop_previous_backend_process(
    *,
    workspace: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    """优先停止内存登记进程，并在服务重启后通过进程记录校验身份。"""

    pid_file = runtime_root / BACKEND_PROCESS_FILENAME
    with _BACKEND_REGISTRY_GUARD:
        registered = _BACKEND_PROCESSES.get(_workspace_key(workspace))
    if registered is not None:
        return terminate_backend_process(
            workspace=workspace,
            process=registered.process,
            pid_file=pid_file,
            source="memory",
        )
    return _stop_backend_process_from_pid_file(
        workspace=workspace,
        pid_file=pid_file,
    )


def terminate_backend_process(
    *,
    workspace: Path | None,
    process: subprocess.Popen[bytes] | None,
    pid_file: Path | None,
    source: str = "memory",
) -> dict[str, Any]:
    """终止持有的 Java 进程，确认退出后清理进程记录与运行副本。"""

    pid = getattr(process, "pid", None)
    cleanup = _cleanup_result(
        attempted=process is not None,
        source=source,
        pid=pid if isinstance(pid, int) else None,
    )
    if process is None:
        cleanup["success"] = True
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=BACKEND_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                cleanup["forced"] = True
                process.kill()
                process.wait(timeout=BACKEND_STOP_TIMEOUT_SECONDS)
        cleanup["terminated"] = process.poll() is not None
        cleanup["success"] = cleanup["terminated"]
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup["error"] = str(exc)

    if cleanup["success"]:
        with _BACKEND_REGISTRY_GUARD:
            registered = _BACKEND_PROCESSES.get(_workspace_key(workspace)) if workspace is not None else None
        jar = registered.jar if registered is not None and registered.process is process else None
        _finish_process_cleanup(workspace, process, pid_file, cleanup, jar=jar)
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _stop_backend_process_from_pid_file(
    *,
    workspace: Path,
    pid_file: Path,
) -> dict[str, Any]:
    """从磁盘恢复进程，校验命令使用的运行副本后再终止。"""

    try:
        record = read_backend_process_record(pid_file)
    except (OSError, ValueError) as exc:
        cleanup = _cleanup_result(attempted=False, source="pid_file", pid=None)
        cleanup["error"] = f"无法读取有效的后端 PID 和运行副本记录：{exc}"
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup
    if record is None:
        cleanup = _cleanup_result(attempted=False, source="none", pid=None)
        cleanup["success"] = True
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    pid = record.pid
    cleanup = _cleanup_result(attempted=True, source="pid_file", pid=pid)
    if not _pid_is_running(pid):
        cleanup["attempted"] = False
        cleanup["stale"] = True
        cleanup["success"] = True
        _finish_process_cleanup(workspace, None, pid_file, cleanup)
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    command, command_error = _query_process_command(pid)
    if command_error is not None:
        cleanup["error"] = command_error
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup
    if not _command_matches_backend_jar(command, record.jar):
        cleanup["identity_matched"] = False
        cleanup["error"] = "PID 指向的进程未使用当前工作区登记的 JAR 副本，已拒绝终止。"
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    cleanup["identity_matched"] = True
    _terminate_recovered_pid(pid, cleanup)
    if cleanup["success"]:
        _finish_process_cleanup(workspace, None, pid_file, cleanup)
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _terminate_recovered_pid(pid: int, cleanup: dict[str, Any]) -> None:
    """先温和终止恢复出的 PID，超时后使用平台能力强制结束。"""

    try:
        os.kill(pid, signal.SIGTERM)
        if _wait_for_pid_exit(pid, BACKEND_STOP_TIMEOUT_SECONDS):
            cleanup["terminated"] = True
            cleanup["success"] = True
            return
        cleanup["forced"] = True
        _force_kill_pid(pid)
        cleanup["terminated"] = _wait_for_pid_exit(pid, BACKEND_STOP_TIMEOUT_SECONDS)
        cleanup["success"] = cleanup["terminated"]
        if not cleanup["success"]:
            cleanup["error"] = "强制结束后端进程后仍无法确认进程退出。"
    except OSError as exc:
        cleanup["error"] = str(exc)


def _force_kill_pid(pid: int) -> None:
    """使用当前操作系统的强制终止能力结束指定 PID。"""

    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=BACKEND_STOP_TIMEOUT_SECONDS,
            check=False,
            creationflags=WINDOWS_CREATE_NO_WINDOW,
        )
        if completed.returncode != 0 and _pid_is_running(pid):
            message = (
                subprocess_output_text(completed.stderr)
                or subprocess_output_text(completed.stdout)
                or "taskkill 执行失败"
            ).strip()
            raise OSError(message)
        return
    os.kill(pid, signal.SIGKILL)


def _wait_for_pid_exit(pid: int, timeout_seconds: float) -> bool:
    """在有限时间内轮询指定 PID 是否已经退出。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(BACKEND_STOP_POLL_INTERVAL_SECONDS)
    return not _pid_is_running(pid)


def _pid_is_running(pid: int) -> bool:
    """只读检查 PID，避免 Windows 零信号调用意外终止进程。"""

    return pid_is_running(pid)


def _query_process_command(pid: int) -> tuple[str, str | None]:
    """读取指定 PID 的完整命令行，用于避免 PID 复用导致误杀。"""

    try:
        if os.name == "nt":
            powershell = (
                shutil.which("pwsh")
                or shutil.which("pwsh.exe")
                or shutil.which("powershell")
                or shutil.which("powershell.exe")
            )
            if not powershell:
                return "", "无法找到 PowerShell，不能安全校验后端进程身份。"
            argv = [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                    "$process = Get-CimInstance Win32_Process -Filter "
                    f"\"ProcessId = {pid}\"; $process.CommandLine"
                ),
            ]
        else:
            argv = ["ps", "-ww", "-p", str(pid), "-o", "command="]
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=BACKEND_STOP_TIMEOUT_SECONDS,
            check=False,
            # 恢复预览时先核对旧进程身份，Windows 的 PowerShell 探测应静默执行。
            creationflags=WINDOWS_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"无法读取后端进程命令行：{exc}"
    command = subprocess_output_text(completed.stdout).strip()
    if completed.returncode != 0 or not command:
        detail = subprocess_output_text(completed.stderr).strip()
        return "", detail or "无法确认后端进程命令行。"
    return command, None


def _command_matches_backend_jar(command: str, jar: Path) -> bool:
    """核对 Java 可执行文件及最后一个参数，避免路径子串或 JVM 属性误匹配。"""

    normalized_command = _normalize_process_identity(command)
    launcher, separator, argument = normalized_command.rpartition(" -jar ")
    executable = launcher.partition(" -D" if os.name != "nt" else " -d")[0].strip('"')
    expected = _normalize_process_identity(str(jar))
    return (
        bool(separator)
        and executable.rsplit("/", 1)[-1] in {"java", "java.exe"}
        and argument in {expected, f'"{expected}"'}
    )


def _normalize_process_identity(value: str) -> str:
    """仅在 Windows 折叠进程路径大小写，POSIX 保留大小写敏感语义。"""

    normalized = value.replace("\\", "/")
    return normalized.lower() if os.name == "nt" else normalized


def _unregister_backend_process(
    workspace: Path | None,
    process: subprocess.Popen[bytes] | None,
) -> None:
    """仅移除目标工作区仍指向指定进程的内存登记。"""

    if workspace is None:
        return
    workspace_key = _workspace_key(workspace)
    with _BACKEND_REGISTRY_GUARD:
        registered = _BACKEND_PROCESSES.get(workspace_key)
        if process is None or (registered is not None and registered.process is process):
            _BACKEND_PROCESSES.pop(workspace_key, None)


def _finish_process_cleanup(
    workspace: Path | None,
    process: subprocess.Popen[bytes] | None,
    pid_file: Path | None,
    cleanup: dict[str, Any],
    *,
    jar: Path | None = None,
) -> None:
    """清理已退出的副本及匹配记录，失败时保留身份供下一次重试。"""

    try:
        record = read_backend_process_record(pid_file) if pid_file is not None else None
        matches = record is not None and record.pid == cleanup["pid"] and jar in {None, record.jar}
        if record is not None and not matches:
            cleanup["pid_file_preserved"] = True
        if matches and record is not None:
            jar = record.jar
        if jar is not None and (matches or record is None or jar != record.jar):
            remove_backend_jar(jar)
        if matches and pid_file is not None:
            pid_file.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        cleanup.update(success=False, error=f"无法清理后端进程记录或 JAR 副本：{exc}")
        return
    _unregister_backend_process(workspace, process)


def _cleanup_result(
    *,
    attempted: bool,
    source: str,
    pid: int | None,
) -> dict[str, Any]:
    """构造稳定的后端进程清理结果。"""

    return {
        "attempted": attempted,
        "source": source,
        "pid": pid,
        "identity_matched": None,
        "terminated": False,
        "forced": False,
        "stale": False,
        "success": False,
        "error": None,
    }


def _workspace_key(workspace: Path) -> str:
    """生成大小写规范化的绝对工作区注册键。"""

    return os.path.normcase(str(workspace.expanduser().resolve()))
