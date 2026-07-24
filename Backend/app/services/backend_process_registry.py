"""管理工作区 Java 预览进程的内存登记、PID 恢复与安全终止。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BACKEND_STOP_TIMEOUT_SECONDS = 5
BACKEND_STOP_POLL_INTERVAL_SECONDS = 0.05
_BACKEND_PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
_BACKEND_LAUNCH_LOCKS: dict[str, threading.RLock] = {}
_BACKEND_REGISTRY_GUARD = threading.Lock()


def backend_launch_lock(workspace: Path) -> threading.RLock:
    """返回指定工作区的串行启动锁，避免停止、构建和启动相互交叉。"""

    workspace_key = _workspace_key(workspace)
    with _BACKEND_REGISTRY_GUARD:
        return _BACKEND_LAUNCH_LOCKS.setdefault(workspace_key, threading.RLock())


def register_backend_process(
    workspace: Path,
    process: subprocess.Popen[bytes],
) -> None:
    """登记指定工作区最新启动的 Java 进程。"""

    with _BACKEND_REGISTRY_GUARD:
        _BACKEND_PROCESSES[_workspace_key(workspace)] = process


def stop_previous_backend_process(
    *,
    workspace: Path,
    backend_root: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    """优先停止内存登记进程，并在服务重启后安全回退到 PID 文件。"""

    pid_file = runtime_root / "backend.pid"
    with _BACKEND_REGISTRY_GUARD:
        process = _BACKEND_PROCESSES.get(_workspace_key(workspace))
    if process is not None:
        return terminate_backend_process(
            workspace=workspace,
            process=process,
            pid_file=pid_file,
            source="memory",
        )
    return _stop_backend_process_from_pid_file(
        workspace=workspace,
        backend_root=backend_root,
        pid_file=pid_file,
    )


def terminate_backend_process(
    *,
    workspace: Path | None,
    process: subprocess.Popen[bytes] | None,
    pid_file: Path | None,
    source: str = "memory",
) -> dict[str, Any]:
    """终止已持有 Popen 对象的 Java 进程，并同步清理登记与 PID 文件。"""

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
        _unregister_backend_process(workspace, process)
        _remove_pid_file(pid_file, cleanup, expected_pid=cleanup["pid"])
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _stop_backend_process_from_pid_file(
    *,
    workspace: Path,
    backend_root: Path,
    pid_file: Path,
) -> dict[str, Any]:
    """从 PID 文件恢复进程，校验命令身份后再执行跨平台终止。"""

    if not pid_file.is_file():
        cleanup = _cleanup_result(attempted=False, source="none", pid=None)
        cleanup["success"] = True
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        if pid <= 0:
            raise ValueError("PID 必须为正整数")
    except (OSError, ValueError) as exc:
        cleanup = _cleanup_result(attempted=False, source="pid_file", pid=None)
        cleanup["error"] = f"无法读取有效的后端 PID 文件：{exc}"
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    cleanup = _cleanup_result(attempted=True, source="pid_file", pid=pid)
    if not _pid_is_running(pid):
        cleanup["attempted"] = False
        cleanup["stale"] = True
        cleanup["success"] = True
        _remove_pid_file(pid_file, cleanup, expected_pid=pid)
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    command, command_error = _query_process_command(pid)
    if command_error is not None:
        cleanup["error"] = command_error
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup
    if not _command_matches_workspace_backend(command, backend_root):
        cleanup["identity_matched"] = False
        cleanup["error"] = "PID 指向的进程不是当前工作区的 java -jar 后端，已拒绝终止。"
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    cleanup["identity_matched"] = True
    _terminate_recovered_pid(pid, cleanup)
    if cleanup["success"]:
        _unregister_backend_process(workspace, None)
        _remove_pid_file(pid_file, cleanup, expected_pid=pid)
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
            text=True,
            capture_output=True,
            timeout=BACKEND_STOP_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0 and _pid_is_running(pid):
            message = (completed.stderr or completed.stdout or "taskkill 执行失败").strip()
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
    """使用零信号检查 PID 是否仍存在且可被当前用户观察。"""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _query_process_command(pid: int) -> tuple[str, str | None]:
    """读取指定 PID 的完整命令行，用于避免 PID 复用导致误杀。"""

    try:
        if os.name == "nt":
            powershell = shutil.which("powershell") or shutil.which("powershell.exe")
            if not powershell:
                return "", "无法找到 PowerShell，不能安全校验后端进程身份。"
            argv = [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "$process = Get-CimInstance Win32_Process -Filter "
                    f"\"ProcessId = {pid}\"; $process.CommandLine"
                ),
            ]
        else:
            argv = ["ps", "-ww", "-p", str(pid), "-o", "command="]
        completed = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=BACKEND_STOP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"无法读取后端进程命令行：{exc}"
    command = (completed.stdout or "").strip()
    if completed.returncode != 0 or not command:
        detail = (completed.stderr or "").strip()
        return "", detail or "无法确认后端进程命令行。"
    return command, None


def _command_matches_workspace_backend(command: str, backend_root: Path) -> bool:
    """确认命令属于当前工作区 target 下的 Java JAR，而不是其他 Java 进程。"""

    normalized_command = command.replace("\\", "/").lower()
    if "java" not in normalized_command or "-jar" not in normalized_command:
        return False
    target_root = backend_root / "target"
    jar_paths = {
        str(path.resolve()).replace("\\", "/").lower()
        for path in target_root.glob("*.jar")
        if path.is_file()
    }
    return any(jar_path in normalized_command for jar_path in jar_paths)


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
        if process is None or registered is process:
            _BACKEND_PROCESSES.pop(workspace_key, None)


def _remove_pid_file(
    pid_file: Path | None,
    cleanup: dict[str, Any],
    *,
    expected_pid: int | None,
) -> None:
    """仅删除仍指向目标进程的 PID 文件，避免并发启动误删新记录。"""

    if pid_file is None:
        return
    try:
        if expected_pid is not None and pid_file.is_file():
            current_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if current_pid != expected_pid:
                cleanup["pid_file_preserved"] = True
                return
        pid_file.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        cleanup["pid_file_error"] = str(exc)


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
