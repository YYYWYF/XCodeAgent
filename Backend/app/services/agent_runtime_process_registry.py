"""管理工作区 Agent Runtime 预览进程的登记、恢复与安全终止。"""

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


AGENT_RUNTIME_STOP_TIMEOUT_SECONDS = 5
AGENT_RUNTIME_STOP_POLL_INTERVAL_SECONDS = 0.05
_AGENT_RUNTIME_PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
_AGENT_RUNTIME_LAUNCH_LOCKS: dict[str, threading.RLock] = {}
_AGENT_RUNTIME_REGISTRY_GUARD = threading.Lock()


def agent_runtime_launch_lock(workspace: Path) -> threading.RLock:
    """返回指定工作区的 Runtime 串行启动锁。"""

    workspace_key = _workspace_key(workspace)
    with _AGENT_RUNTIME_REGISTRY_GUARD:
        return _AGENT_RUNTIME_LAUNCH_LOCKS.setdefault(workspace_key, threading.RLock())


def register_agent_runtime_process(
    workspace: Path,
    process: subprocess.Popen[bytes],
) -> None:
    """登记指定工作区最新启动的 Agent Runtime 进程。"""

    with _AGENT_RUNTIME_REGISTRY_GUARD:
        _AGENT_RUNTIME_PROCESSES[_workspace_key(workspace)] = process


def clear_agent_runtime_process_registry_workspace(workspace: str | Path) -> bool:
    """应用删除完成后移除目标工作区遗留的 Runtime 进程和锁缓存。"""

    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    workspace_key = _workspace_key(workspace_path)
    with _AGENT_RUNTIME_REGISTRY_GUARD:
        process = _AGENT_RUNTIME_PROCESSES.get(workspace_key)
        if process is not None and process.poll() is None:
            return False
        removed_process = _AGENT_RUNTIME_PROCESSES.pop(workspace_key, None) is not None
        removed_lock = _AGENT_RUNTIME_LAUNCH_LOCKS.pop(workspace_key, None) is not None
        return removed_process or removed_lock


def stop_previous_agent_runtime_process(
    *,
    workspace: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    """优先停止内存登记进程，并安全回退到 PID 文件。"""

    pid_file = runtime_root / "agent-runtime.pid"
    with _AGENT_RUNTIME_REGISTRY_GUARD:
        process = _AGENT_RUNTIME_PROCESSES.get(_workspace_key(workspace))
    if process is not None:
        return terminate_agent_runtime_process(
            workspace=workspace,
            process=process,
            pid_file=pid_file,
            source="memory",
        )
    return _stop_agent_runtime_process_from_pid_file(
        workspace=workspace,
        agent_runtime_root=agent_runtime_root,
        pid_file=pid_file,
    )


def terminate_agent_runtime_process(
    *,
    workspace: Path | None,
    process: subprocess.Popen[bytes] | None,
    pid_file: Path | None,
    source: str = "memory",
) -> dict[str, Any]:
    """终止已持有的 Runtime 进程并同步清理登记和 PID 文件。"""

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
                process.wait(timeout=AGENT_RUNTIME_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                cleanup["forced"] = True
                process.kill()
                process.wait(timeout=AGENT_RUNTIME_STOP_TIMEOUT_SECONDS)
        cleanup["terminated"] = process.poll() is not None
        cleanup["success"] = cleanup["terminated"]
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup["error"] = str(exc)

    if cleanup["success"]:
        _unregister_agent_runtime_process(workspace, process)
        _remove_pid_file(pid_file, cleanup, expected_pid=cleanup["pid"])
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _stop_agent_runtime_process_from_pid_file(
    *,
    workspace: Path,
    agent_runtime_root: Path,
    pid_file: Path,
) -> dict[str, Any]:
    """校验 PID 对应命令属于当前 Runtime 后再终止恢复出的进程。"""

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
        cleanup["error"] = f"无法读取有效的 Agent Runtime PID 文件：{exc}"
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
    process_cwd: str | None = None
    if os.name != "nt":
        process_cwd, cwd_error = _query_process_working_directory(pid)
        if cwd_error is not None:
            cleanup["error"] = (
                "无法完成 Agent Runtime 进程身份二次确认，已拒绝终止："
                f"{cwd_error}"
            )
            cleanup["finished_at"] = datetime.now(UTC).isoformat()
            return cleanup
    if not _command_matches_agent_runtime(
        command,
        agent_runtime_root,
        process_cwd=process_cwd,
    ):
        cleanup["identity_matched"] = False
        cleanup["error"] = "PID 指向的进程不属于当前工作区 Agent Runtime，已拒绝终止。"
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    cleanup["identity_matched"] = True
    _terminate_recovered_pid(pid, cleanup)
    if cleanup["success"]:
        _unregister_agent_runtime_process(workspace, None)
        _remove_pid_file(pid_file, cleanup, expected_pid=pid)
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _terminate_recovered_pid(pid: int, cleanup: dict[str, Any]) -> None:
    """先温和终止恢复出的 PID，超时后再强制结束。"""

    try:
        os.kill(pid, signal.SIGTERM)
        if _wait_for_pid_exit(pid, AGENT_RUNTIME_STOP_TIMEOUT_SECONDS):
            cleanup["terminated"] = True
            cleanup["success"] = True
            return
        cleanup["forced"] = True
        _force_kill_pid(pid)
        cleanup["terminated"] = _wait_for_pid_exit(
            pid, AGENT_RUNTIME_STOP_TIMEOUT_SECONDS
        )
        cleanup["success"] = cleanup["terminated"]
        if not cleanup["success"]:
            cleanup["error"] = "强制结束 Agent Runtime 后仍无法确认进程退出。"
    except OSError as exc:
        cleanup["error"] = str(exc)


def _force_kill_pid(pid: int) -> None:
    """使用当前操作系统的强制终止能力结束指定 PID。"""

    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            text=True,
            capture_output=True,
            timeout=AGENT_RUNTIME_STOP_TIMEOUT_SECONDS,
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
        time.sleep(AGENT_RUNTIME_STOP_POLL_INTERVAL_SECONDS)
    return not _pid_is_running(pid)


def _pid_is_running(pid: int) -> bool:
    """使用零信号检查 PID 是否仍存在。"""

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
            powershell = (
                shutil.which("pwsh")
                or shutil.which("pwsh.exe")
                or shutil.which("powershell")
                or shutil.which("powershell.exe")
            )
            if not powershell:
                return "", "无法找到 PowerShell，不能安全校验 Runtime 进程身份。"
            argv = [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "$process = Get-CimInstance Win32_Process -Filter "
                    f'"ProcessId = {pid}"; $process.CommandLine'
                ),
            ]
        else:
            argv = ["ps", "-ww", "-p", str(pid), "-o", "command="]
        completed = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=AGENT_RUNTIME_STOP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"无法读取 Agent Runtime 进程命令行：{exc}"
    command = (completed.stdout or "").strip()
    if completed.returncode != 0 or not command:
        detail = (completed.stderr or "").strip()
        return "", detail or "无法确认 Agent Runtime 进程命令行。"
    return command, None


def _query_process_working_directory(pid: int) -> tuple[str, str | None]:
    """读取指定 PID 的工作目录，补足命令行未携带工作区路径的恢复场景。"""

    if os.name == "nt":
        return "", "Windows 无法可靠读取其他进程的工作目录。"

    proc_cwd = Path("/proc") / str(pid) / "cwd"
    if proc_cwd.is_symlink():
        try:
            return os.readlink(proc_cwd), None
        except OSError as exc:
            return "", str(exc)

    lsof_command = shutil.which("lsof")
    if not lsof_command and Path("/usr/sbin/lsof").is_file():
        lsof_command = "/usr/sbin/lsof"
    if not lsof_command:
        return "", "无法找到 lsof 命令。"
    try:
        completed = subprocess.run(
            [lsof_command, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            text=True,
            capture_output=True,
            timeout=AGENT_RUNTIME_STOP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    working_directory = next(
        (
            line[1:].strip()
            for line in completed.stdout.splitlines()
            if line.startswith("n") and line[1:].strip()
        ),
        "",
    )
    if completed.returncode != 0 or not working_directory:
        detail = (completed.stderr or "").strip()
        return "", detail or "lsof 未返回进程工作目录。"
    return working_directory, None


def _command_contains_runtime_root(command: str, agent_runtime_root: Path) -> bool:
    """判断进程命令行是否显式携带当前 Runtime 根目录。"""

    normalized_command = _normalize_process_identity(command)
    normalized_root = _normalize_process_identity(str(agent_runtime_root.resolve()))
    search_from = 0
    while True:
        root_index = normalized_command.find(normalized_root, search_from)
        if root_index < 0:
            return False
        root_end = root_index + len(normalized_root)
        if root_end == len(normalized_command):
            return True
        if normalized_command[root_end] in "/ \t\r\n\"'":
            return True
        search_from = root_index + 1


def _command_matches_agent_runtime(
    command: str,
    agent_runtime_root: Path,
    *,
    process_cwd: str | None = None,
) -> bool:
    """使用命令标识和独立工作区位置证据二次确认恢复进程身份。"""

    normalized_command = _normalize_process_identity(command)
    if "agent-runtime" not in normalized_command:
        return False
    if os.name == "nt":
        return _command_contains_runtime_root(command, agent_runtime_root)
    if not process_cwd:
        return False
    normalized_cwd = _normalize_process_identity(
        str(Path(process_cwd).expanduser().resolve(strict=False))
    )
    normalized_root = _normalize_process_identity(str(agent_runtime_root.resolve()))
    return normalized_cwd == normalized_root


def _normalize_process_identity(value: str) -> str:
    """仅在 Windows 折叠进程路径大小写。"""

    normalized = value.replace("\\", "/")
    return normalized.lower() if os.name == "nt" else normalized


def _unregister_agent_runtime_process(
    workspace: Path | None,
    process: subprocess.Popen[bytes] | None,
) -> None:
    """仅移除目标工作区仍指向指定进程的内存登记。"""

    if workspace is None:
        return
    workspace_key = _workspace_key(workspace)
    with _AGENT_RUNTIME_REGISTRY_GUARD:
        registered = _AGENT_RUNTIME_PROCESSES.get(workspace_key)
        if process is None or registered is process:
            _AGENT_RUNTIME_PROCESSES.pop(workspace_key, None)


def _remove_pid_file(
    pid_file: Path | None,
    cleanup: dict[str, Any],
    *,
    expected_pid: int | None,
) -> None:
    """仅删除仍指向目标进程的 PID 文件。"""

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
    """构造稳定的 Runtime 进程清理结果。"""

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
