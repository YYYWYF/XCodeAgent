"""Runtime 预览进程与 Java 分开登记，共用当前工作区互斥和进程回收基础设施。"""

import os
from pathlib import Path
import subprocess
from threading import RLock

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.backend_process_registry import backend_launch_lock, _query_process_command, _terminate_recovered_pid, _wait_for_pid_exit
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.process_liveness import pid_is_running

_processes: dict[str, subprocess.Popen] = {}
_guard = RLock()


def runtime_directory(root: Path, *, profile: str = "preview") -> Path:
    """返回当前标准预览目录，不创建历史状态或独立恢复协议。"""

    if profile not in {"preview", "debug"}:
        raise ValueError("Runtime Profile 无效。")
    return root / WORKSPACE_ARTIFACT_DIR / ("runtime/debug" if profile == "debug" else "runtime/launch")


def register_runtime(root: Path, process: subprocess.Popen, *, profile: str = "preview") -> None:
    """保存本次进程和 PID，后端重启后仍可安全识别并停止。"""

    with _guard:
        _processes[str(root) + ":" + profile] = process
    (runtime_directory(root, profile=profile) / "agent-runtime.pid").write_text(str(process.pid), encoding="utf-8")


def _matches_runtime(command: str, root: Path) -> bool:
    """要求命令同时包含当前工程绝对路径和固定控制台入口，防止复用 PID 误杀。"""

    normalized = command.replace("\\", "/")
    directory = str(root / "agent-runtime").replace("\\", "/")
    if os.name == "nt":
        normalized, directory = normalized.casefold(), directory.casefold()
    return ((f"{directory}/.venv/" in normalized and "agent-runtime" in normalized)
            or ((f"--directory {directory} " in normalized or f'--directory "{directory}" ' in normalized
                 or f"--directory '{directory}' " in normalized)
                and " run " in normalized and normalized.rstrip().endswith("agent-runtime")))


def terminate_runtime_process(root: Path, process: subprocess.Popen) -> None:
    """回收持有句柄的独立进程组；临时验收可复用而不触碰预览登记。"""

    if process.poll() is None:
        if os.name == "nt":
            completed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, timeout=5, check=False)
            if completed.returncode and process.poll() is None:
                raise RuntimeError("Runtime 进程树停止失败。")
        else:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    workspace_process_registry.release(root, process)


def stop_runtime(root: Path, *, profile: str = "preview") -> dict:
    """在已有工作区启动锁内终止 Runtime 进程树，退出失败时保留登记与 PID。"""

    with backend_launch_lock(root):
        # 标准停机同时撤销调试；单独停止调试不影响正在运行的预览。
        if profile == "preview":
            debug_result = stop_runtime(root, profile="debug")
            if debug_result.get("status") == "failed":
                return debug_result
        pid_path = runtime_directory(root, profile=profile) / "agent-runtime.pid"
        with _guard:
            process = _processes.get(str(root) + ":" + profile)
        try:
            if process is not None:
                terminate_runtime_process(root, process)
            elif pid_path.is_file():
                pid = int(pid_path.read_text(encoding="utf-8"))
                if pid <= 0:
                    raise ValueError("Runtime PID 无效。")
                if pid_is_running(pid):
                    command, error = _query_process_command(pid)
                    if error or not _matches_runtime(command, root):
                        raise RuntimeError("无法确认 Runtime 进程属于当前工作区，已拒绝停止。")
                    cleanup = {"success": False}
                    if os.name != "nt":
                        import signal
                        if os.getpgid(pid) != pid:
                            raise RuntimeError("Runtime 进程组身份不一致，已拒绝停止。")
                        os.killpg(pid, signal.SIGTERM)
                        if not _wait_for_pid_exit(pid, 5):
                            os.killpg(pid, signal.SIGKILL)
                        cleanup["success"] = _wait_for_pid_exit(pid, 5)
                    else:
                        # Windows 沿用平台 taskkill /T，确保子进程树同步退出。
                        _terminate_recovered_pid(pid, cleanup)
                    if not cleanup.get("success"):
                        raise RuntimeError(str(cleanup.get("error") or "Runtime 进程未退出。"))
            pid_path.unlink(missing_ok=True)
            with _guard:
                _processes.pop(str(root) + ":" + profile, None)
            return {"status": "stopped", "message": "Agent Runtime 已停止。"}
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "message": f"Agent Runtime 停止失败：{exc}"}


def runtime_pid_is_current(root: Path, pid: int) -> bool:
    """确认状态投影属于当前登记进程；重启后再核对受控命令身份。"""

    if pid <= 0 or not pid_is_running(pid):
        return False
    with _guard:
        process = _processes.get(str(root) + ":preview")
    if process is not None:
        return process.pid == pid and process.poll() is None
    command, error = _query_process_command(pid)
    return error is None and _matches_runtime(command, root)
