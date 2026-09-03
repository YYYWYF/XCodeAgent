"""按应用工作区登记并终止后端启动的短期命令进程。"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class WorkspaceProcessRegistry:
    """为同步 subprocess 命令提供工作区删除栅栏和进程组终止能力。"""

    def __init__(self) -> None:
        """初始化线程安全的工作区进程集合。"""

        self._lock = threading.Lock()
        self._processes: dict[str, set[subprocess.Popen[Any]]] = {}
        self._deleting_workspaces: set[str] = set()

    def run(
        self,
        *popenargs: Any,
        workspace: str | Path,
        input: Any = None,
        capture_output: bool = False,
        timeout: float | None = None,
        check: bool = False,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        """按 subprocess.run 语义执行命令，并在整个等待期间登记其进程组。"""

        key = _workspace_key(workspace)
        with self._lock:
            if key in self._deleting_workspaces:
                raise RuntimeError("应用正在删除，已拒绝启动新的工作区命令。")
        if input is not None:
            if kwargs.get("stdin") is not None:
                raise ValueError("stdin and input arguments may not both be used.")
            kwargs["stdin"] = subprocess.PIPE
        if capture_output:
            if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
                raise ValueError(
                    "stdout and stderr arguments may not be used with capture_output."
                )
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        _configure_process_group(kwargs)
        process = subprocess.Popen(*popenargs, **kwargs)
        self._register(key, process)
        try:
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _terminate_process_group(process, force=True)
                stdout, stderr = process.communicate()
                exc.stdout = stdout
                exc.stderr = stderr
                raise
            completed = subprocess.CompletedProcess(
                process.args,
                process.returncode,
                stdout,
                stderr,
            )
            if check:
                completed.check_returncode()
            return completed
        finally:
            self._unregister(key, process)

    def begin_workspace_deletion(self, workspace: str | Path) -> None:
        """封锁目标工作区后续短期命令启动。"""

        with self._lock:
            self._deleting_workspaces.add(_workspace_key(workspace))

    def end_workspace_deletion(self, workspace: str | Path) -> None:
        """仅解除目标工作区的删除栅栏，允许停机失败后继续启动命令。"""

        with self._lock:
            self._deleting_workspaces.discard(_workspace_key(workspace))

    def cancel_workspace(
        self,
        workspace: str | Path,
        *,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """终止目标工作区全部已登记进程组，并报告无法退出的进程。"""

        key = _workspace_key(workspace)
        self.begin_workspace_deletion(workspace)
        with self._lock:
            processes = [
                process
                for process in self._processes.get(key, set())
                if process.poll() is None
            ]
        for process in processes:
            _terminate_process_group(process, force=False)
        deadline = time.monotonic() + max(timeout_seconds, 0.1)
        while time.monotonic() < deadline and any(
            process.poll() is None for process in processes
        ):
            time.sleep(0.05)
        for process in processes:
            if process.poll() is None:
                _terminate_process_group(process, force=True)
        force_deadline = time.monotonic() + 1.0
        while time.monotonic() < force_deadline and any(
            process.poll() is None for process in processes
        ):
            time.sleep(0.02)
        remaining = [process.pid for process in processes if process.poll() is None]
        return {
            "requestedProcessIds": [process.pid for process in processes],
            "terminatedCount": len(processes) - len(remaining),
            "remainingProcessIds": remaining,
        }

    def active_process_ids(self, workspace: str | Path) -> list[int]:
        """返回目标工作区仍存活的已登记子进程，供状态核验和测试使用。"""

        key = _workspace_key(workspace)
        with self._lock:
            return sorted(
                process.pid
                for process in self._processes.get(key, set())
                if process.poll() is None
            )

    def _register(self, key: str, process: subprocess.Popen[Any]) -> None:
        """把新进程加入工作区集合，并关闭登记与删除之间的竞态窗口。"""

        with self._lock:
            if key in self._deleting_workspaces:
                _terminate_process_group(process, force=True)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                raise RuntimeError("应用正在删除，工作区命令已被中断。")
            self._processes.setdefault(key, set()).add(process)

    def _unregister(self, key: str, process: subprocess.Popen[Any]) -> None:
        """移除已经完成或被删除事务终止的进程句柄。"""

        with self._lock:
            processes = self._processes.get(key)
            if processes is None:
                return
            processes.discard(process)
            if not processes:
                self._processes.pop(key, None)


def _workspace_key(workspace: str | Path) -> str:
    """生成不依赖路径大小写表现的规范工作区键。"""

    return os.path.normcase(str(Path(workspace).expanduser().resolve(strict=False)))


def _configure_process_group(kwargs: dict[str, Any]) -> None:
    """让每条受控命令拥有可整体终止的独立进程组。"""

    if os.name == "nt":
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags") or 0)
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs.setdefault("start_new_session", True)


def _terminate_process_group(process: subprocess.Popen[Any], *, force: bool) -> None:
    """跨平台终止命令及其派生子进程，进程已退出时视为成功。"""

    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    *(["/F"] if force else []),
                ],
                capture_output=True,
                timeout=2,
                check=False,
            )
        else:
            os.killpg(
                os.getpgid(process.pid),
                signal.SIGKILL if force else signal.SIGTERM,
            )
    except (OSError, ProcessLookupError):
        return


workspace_process_registry = WorkspaceProcessRegistry()
