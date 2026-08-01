from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.utils.subprocess_output import subprocess_output_text
INSTALL_TIMEOUT_SECONDS = 120
SERVER_READY_TIMEOUT_SECONDS = 20
SERVER_READY_INTERVAL_SECONDS = 1
FRONTEND_STOP_TIMEOUT_SECONDS = 5
FRONTEND_STOP_POLL_INTERVAL_SECONDS = 0.05
_FRONTEND_PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
_FRONTEND_PROCESS_GUARD = threading.Lock()


def launch_frontend_project(workspace_path: str | Path) -> dict[str, Any]:
    """按普通工作目录安装并启动前端，不依赖 LangGraph 状态。"""

    root = Path(workspace_path).expanduser().resolve()
    package_path = _find_frontend_package_json(root)
    if package_path is None:
        return _failed_launch("未找到前端 package.json。", root=root)

    package_json = _read_package_json(package_path)
    if package_json is None:
        return _failed_launch(
            f"无法读取或解析 package.json：{_relative(package_path, root)}",
            root=root,
        )

    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    script_name = _select_launch_script(scripts)
    if not script_name:
        return _failed_launch(
            "package.json 中未找到可启动脚本（优先 dev，其次 start）。",
            root=root,
            package_json_path=package_path,
        )

    package_manager = _select_package_manager(package_path.parent)
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    runtime_root.mkdir(parents=True, exist_ok=True)
    preview_url = _preview_url(scripts.get(script_name, ""))
    existing_server = _reuse_ready_server(runtime_root, preview_url)
    if existing_server is not None:
        return {
            **_base_launch_payload(
                root=root,
                package_json_path=package_path,
                package_manager=package_manager,
                script_name=script_name,
                runtime_root=runtime_root,
            ),
            "status": "running",
            "message": "前端项目已在运行并可访问。",
            "preview_url": preview_url,
            "install": {
                "skipped": True,
                "reason": "预览服务已就绪，无需重复安装依赖。",
            },
            "server": existing_server,
        }

    # 复用失败但可能存在僵尸进程占用端口（如上次启动的 Vite 仍在运行），
    # 这里主动清理，避免新启动必然端口冲突导致 Vite 自动递增端口。
    _stop_frontend_process_from_pid_file(runtime_root / "frontend.pid")

    package_manager_command = shutil.which(package_manager)
    if not package_manager_command:
        return _failed_launch(
            f"未找到包管理器命令：{package_manager}",
            root=root,
            package_json_path=package_path,
        )

    install_result = _run_install(
        package_manager_command=package_manager_command,
        cwd=package_path.parent,
        runtime_root=runtime_root,
    )
    if install_result["returncode"] != 0:
        return {
            **_base_launch_payload(
                root=root,
                package_json_path=package_path,
                package_manager=package_manager,
                script_name=script_name,
                runtime_root=runtime_root,
            ),
            "status": "failed",
            "message": (
                "前端依赖安装命令执行失败。"
                if install_result.get("error")
                else "前端依赖安装失败。"
            ),
            "install": install_result,
        }

    launch_result, process = _start_dev_server(
        package_manager_command=package_manager_command,
        script_name=script_name,
        script_command=str(scripts.get(script_name, "")),
        cwd=package_path.parent,
        runtime_root=runtime_root,
        preview_url=preview_url,
    )
    if process is not None:
        _register_frontend_process(root, process)
    _stdout_log = Path(str(launch_result.get("stdout_log") or ""))
    _stdout_offset = int(launch_result.get("stdout_offset") or 0)
    _stderr_log = Path(str(launch_result.get("stderr_log") or ""))
    ready = process is not None and _wait_until_ready(
        preview_url,
        process,
        stdout_log=_stdout_log,
        stdout_offset=_stdout_offset,
        stderr_log=_stderr_log,
    )
    # Vite / webpack dev server 在端口被占用时会自动递增端口，
    # 这里从日志中提取实际监听地址并覆写 preview_url。
    resolved_url = _resolve_actual_preview_url(
        stdout_log=_stdout_log,
        stdout_offset=_stdout_offset,
        fallback_url=preview_url,
    )
    if resolved_url != preview_url and _preview_is_ready(resolved_url):
        preview_url = resolved_url
    returncode = process.poll() if process is not None else None
    process_running = process is not None and returncode is None
    launch_status = "running" if ready and process_running else "failed"
    if launch_status == "running":
        message = "前端项目已启动并可访问。"
    elif returncode is not None:
        message = f"前端启动进程已退出（退出码：{returncode}）。"
    elif process is None:
        message = "前端启动命令执行失败。"
    else:
        message = "前端服务健康检查超时。"

    return {
        **_base_launch_payload(
            root=root,
            package_json_path=package_path,
            package_manager=package_manager,
            script_name=script_name,
            runtime_root=runtime_root,
        ),
        "status": launch_status,
        "message": message,
        "preview_url": preview_url,
        "install": install_result,
        "server": {
            **launch_result,
            "ready": ready,
            "returncode": returncode,
            "ready_checked_at": datetime.now(UTC).isoformat(),
        },
    }


def stop_frontend_project(workspace_path: str | Path) -> dict[str, Any]:
    """停止指定工作区由前端预览启动器记录的开发服务器进程。"""

    root = Path(workspace_path).expanduser().resolve()
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    pid_file = runtime_root / "frontend.pid"
    cleanup = _stop_frontend_process(root, pid_file)
    return {
        "status": "stopped" if cleanup.get("success") else "failed",
        "message": (
            "前端预览进程已停止。"
            if cleanup.get("success") and cleanup.get("attempted")
            else "未发现正在运行的前端预览进程。"
            if cleanup.get("success")
            else "前端预览进程停止失败。"
        ),
        "workspace": str(root),
        "runtime_root": str(runtime_root),
        "cleanup": cleanup,
    }


def _workspace_key(workspace: Path) -> str:
    """生成跨平台稳定的工作区进程登记键。"""

    resolved = str(workspace.expanduser().resolve())
    return resolved.lower() if os.name == "nt" else resolved


def _register_frontend_process(workspace: Path, process: subprocess.Popen[bytes]) -> None:
    """登记指定工作区最近一次启动的前端预览进程。"""

    with _FRONTEND_PROCESS_GUARD:
        _FRONTEND_PROCESSES[_workspace_key(workspace)] = process


def _unregister_frontend_process(workspace: Path, process: subprocess.Popen[bytes] | None) -> None:
    """清理指定工作区的前端预览进程登记。"""

    workspace_key = _workspace_key(workspace)
    with _FRONTEND_PROCESS_GUARD:
        current_process = _FRONTEND_PROCESSES.get(workspace_key)
        if process is None or current_process is process:
            _FRONTEND_PROCESSES.pop(workspace_key, None)


def _stop_frontend_process(workspace: Path, pid_file: Path) -> dict[str, Any]:
    """优先停止内存登记的前端进程，缺失时回退到 PID 文件。"""

    with _FRONTEND_PROCESS_GUARD:
        process = _FRONTEND_PROCESSES.get(_workspace_key(workspace))
    if process is None:
        return _stop_frontend_process_from_pid_file(pid_file)
    cleanup = _terminate_frontend_process(
        workspace=workspace,
        process=process,
        pid_file=pid_file,
    )
    if cleanup.get("success"):
        return cleanup
    return _stop_frontend_process_from_pid_file(pid_file)


def _find_frontend_package_json(root: Path) -> Path | None:
    # 直接平铺到根目录：frontend/package.json
    candidates = [
        root / "frontend" / "package.json",
        root / "Frontend" / "package.json",
        root / "app" / "frontend" / "package.json",
        root / "package.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    for path in root.glob("*/package.json"):
        if path.parent.name in {"node_modules", ".xcodeagent"}:
            continue
        package = _read_package_json(path)
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        if isinstance(scripts, dict) and ("dev" in scripts or "start" in scripts):
            return path
    return None


def _read_package_json(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _select_launch_script(scripts: dict[str, Any]) -> str | None:
    for name in ("dev", "start"):
        if name in scripts:
            return name
    return None


def _select_package_manager(cwd: Path) -> str:
    if (cwd / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (cwd / "yarn.lock").is_file():
        return "yarn"
    return "pnpm"


def _run_install(
    *,
    package_manager_command: str,
    cwd: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    argv = [package_manager_command, "install"]
    started_at = datetime.now(UTC).isoformat()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = subprocess_output_text(completed.stdout)
        stderr = subprocess_output_text(completed.stderr)
        returncode = completed.returncode
        timed_out = False
        error = None
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired 的输出可能是 bytes，统一解码后再写入运行日志。
        stdout = subprocess_output_text(exc.stdout)
        stderr = subprocess_output_text(exc.stderr)
        returncode = None
        timed_out = True
        error = None
    except OSError as exc:
        # Windows 上 npm/pnpm/yarn 通常是 .cmd，启动异常需落盘并返回给 Workflow。
        stdout = ""
        stderr = str(exc)
        returncode = None
        timed_out = False
        error = str(exc)

    stdout_path = runtime_root / "install.stdout.log"
    stderr_path = runtime_root / "install.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "argv": argv,
        "cwd": str(cwd),
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }


def _start_dev_server(
    *,
    package_manager_command: str,
    script_name: str,
    script_command: str,
    cwd: Path,
    runtime_root: Path,
    preview_url: str,
) -> tuple[dict[str, Any], subprocess.Popen[bytes] | None]:
    """以后台进程启动开发服务器，并保留进程对象供健康检查监督。"""
    argv = [package_manager_command, "run", script_name]
    stdout_path = runtime_root / "frontend.stdout.log"
    stderr_path = runtime_root / "frontend.stderr.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    stdout_offset = stdout.tell()
    env = _launch_environment(script_command)
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            # macOS 预览进程继承 Electron 后端进程组，应用退出时可统一回收。
            start_new_session=os.name == "nt",
            env=env,
        )
    except OSError as exc:
        stdout.close()
        stderr.close()
        return (
            {
                "argv": argv,
                "cwd": str(cwd),
                "pid": None,
                "error": str(exc),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
                "preview_url": preview_url,
            },
            None,
        )
    stdout.close()
    stderr.close()
    pid_path = runtime_root / "frontend.pid"
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return (
        {
            "argv": argv,
            "cwd": str(cwd),
            "pid": process.pid,
            "pid_file": str(pid_path),
            "stdout_log": str(stdout_path),
            "stdout_offset": stdout_offset,
            "stderr_log": str(stderr_path),
            "preview_url": preview_url,
            "started_at": datetime.now(UTC).isoformat(),
        },
        process,
    )


def _launch_environment(script_command: str) -> dict[str, str]:
    """构造受控启动环境，避免 CRA 在代理配置下收到非法 loopback HOST。"""

    env = {**os.environ, "BROWSER": "none"}
    if "react-scripts" in script_command:
        env.pop("HOST", None)
    else:
        env["HOST"] = "localhost"
    return env


def _reuse_ready_server(runtime_root: Path, preview_url: str) -> dict[str, Any] | None:
    """复用 launcher 先前启动且仍可访问的服务，避免调试续跑重复占用端口。"""

    pid_path = runtime_root / "frontend.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or not _preview_is_ready(preview_url):
        return None
    return {
        "pid": pid,
        "pid_file": str(pid_path),
        "preview_url": preview_url,
        "ready": True,
        "reused": True,
        "returncode": None,
        "ready_checked_at": datetime.now(UTC).isoformat(),
    }


def _preview_url(script: str) -> str:
    port = _script_port(script)
    if port is None:
        port = 80
    return f"http://localhost:{port}"


def _script_port(script: str) -> int | None:
    match = re.search(r"(?:--port\s+|--port=|PORT=)(\d{2,5})", script)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


_DEV_SERVER_URL_PATTERN = re.compile(
    r"(?:Local|localhost|127\.0\.0\.1)\s*:\s*(https?://[^\s\n]+)",
    re.IGNORECASE,
)


def _resolve_actual_preview_url(
    stdout_log: Path,
    stdout_offset: int,
    fallback_url: str,
) -> str:
    """从本次启动的 stdout 日志中提取 dev server 实际监听地址。

    Vite 在端口被占用时会自动递增端口（如 3000→3001），
    ``_preview_url`` 根据启动命令推算的端口可能与实际不符。
    这里优先从日志中的 ``Local:`` 行解析真实地址。
    """
    try:
        with stdout_log.open("rb") as stream:
            stream.seek(max(0, stdout_offset))
            content = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return fallback_url

    match = _DEV_SERVER_URL_PATTERN.search(content)
    if match:
        return match.group(1).rstrip("/")
    return fallback_url


def _wait_until_ready(
    preview_url: str,
    process: subprocess.Popen[bytes],
    *,
    stdout_log: Path | None = None,
    stdout_offset: int = 0,
    stderr_log: Path | None = None,
) -> bool:
    """结合 HTTP 与本次启动日志轮询就绪状态，并监督启动进程存活。

    Vite 在缺失依赖时仍能启动 HTTP 服务并在 stdout 打印 ``Local:``，
    但 stderr 会输出 ``Cannot find module`` 编译错误。这里检查 stderr
    致命错误，避免将半死的 dev server 误判为就绪。
    """

    deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _dev_server_stderr_is_fatal(stderr_log):
            return False
        if _preview_is_ready(preview_url):
            return True
        if stdout_log is not None and _dev_server_log_is_ready(stdout_log, stdout_offset):
            return True
        time.sleep(SERVER_READY_INTERVAL_SECONDS)
    return False


def _preview_is_ready(preview_url: str) -> bool:
    """执行一次本地预览 HTTP 探测，并兼容 urllib 对 4xx 抛出 HTTPError。"""

    try:
        with urlopen(preview_url, timeout=2) as response:
            return 200 <= response.status < 500
    except HTTPError as exc:
        return 200 <= exc.code < 500
    except (OSError, URLError):
        return False


def _dev_server_log_is_ready(stdout_log: Path, stdout_offset: int) -> bool:
    """仅检查本次启动追加的日志，识别常见前端开发服务器就绪标志。"""

    try:
        with stdout_log.open("rb") as stream:
            stream.seek(max(0, stdout_offset))
            content = stream.read().decode("utf-8", errors="replace").lower()
    except OSError:
        return False
    ready_markers = (
        "compiled successfully",
        "compiled with warnings",
        "webpack compiled successfully",
        "you can now view",
        "ready in",
        "local:",
    )
    return any(marker in content for marker in ready_markers)


_FATAL_STDERR_PATTERNS = (
    "cannot find module",
    "module not found",
    "failed to resolve",
    "could not resolve",
    "error:  ts",
    "syntaxerror:",
    "unexpected token",
)


def _dev_server_stderr_is_fatal(stderr_log: Path | None) -> bool:
    """检查 dev server stderr 是否包含致命编译错误。"""

    if stderr_log is None:
        return False
    try:
        content = stderr_log.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(pattern in content for pattern in _FATAL_STDERR_PATTERNS)


def _stop_frontend_process_from_pid_file(pid_file: Path) -> dict[str, Any]:
    """根据前端 PID 文件停止启动器创建的那个开发服务器进程。"""

    cleanup = {
        "attempted": False,
        "success": False,
        "source": "pid_file",
        "pid": None,
        "terminated": False,
        "forced": False,
    }
    if not pid_file.is_file():
        cleanup["success"] = True
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        if pid <= 0:
            raise ValueError("PID 必须为正整数")
    except (OSError, ValueError) as exc:
        cleanup["error"] = f"无法读取有效的前端 PID 文件：{exc}"
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    cleanup["pid"] = pid
    if not _pid_is_running(pid):
        cleanup["stale"] = True
        cleanup["success"] = True
        _remove_pid_file(pid_file, cleanup, expected_pid=pid)
        cleanup["finished_at"] = datetime.now(UTC).isoformat()
        return cleanup

    cleanup["attempted"] = True
    _terminate_frontend_pid(pid, cleanup)
    if cleanup["success"]:
        _remove_pid_file(pid_file, cleanup, expected_pid=pid)
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _terminate_frontend_process(
    *,
    workspace: Path,
    process: subprocess.Popen[bytes],
    pid_file: Path,
) -> dict[str, Any]:
    """终止仍由当前后端持有的前端预览 Popen 对象。"""

    pid = process.pid if isinstance(process.pid, int) else None
    cleanup = {
        "attempted": process.poll() is None,
        "success": False,
        "source": "memory",
        "pid": pid,
        "terminated": False,
        "forced": False,
    }
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=FRONTEND_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                cleanup["forced"] = True
                process.kill()
                process.wait(timeout=FRONTEND_STOP_TIMEOUT_SECONDS)
        cleanup["terminated"] = process.poll() is not None
        cleanup["success"] = cleanup["terminated"]
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup["error"] = str(exc)
    if cleanup["success"]:
        _unregister_frontend_process(workspace, process)
        _remove_pid_file(pid_file, cleanup, expected_pid=pid)
    cleanup["finished_at"] = datetime.now(UTC).isoformat()
    return cleanup


def _terminate_frontend_pid(pid: int, cleanup: dict[str, Any]) -> None:
    """先温和停止前端启动进程，超时后再强制结束。"""

    try:
        os.kill(pid, signal.SIGTERM)
        if _wait_for_pid_exit(pid, FRONTEND_STOP_TIMEOUT_SECONDS):
            cleanup["terminated"] = True
            cleanup["success"] = True
            return
        cleanup["forced"] = True
        _force_kill_pid(pid)
        cleanup["terminated"] = _wait_for_pid_exit(pid, FRONTEND_STOP_TIMEOUT_SECONDS)
        cleanup["success"] = cleanup["terminated"]
        if not cleanup["success"]:
            cleanup["error"] = "强制结束前端预览进程后仍无法确认进程退出。"
    except OSError as exc:
        cleanup["error"] = str(exc)


def _force_kill_pid(pid: int) -> None:
    """使用当前操作系统的强制终止能力结束指定 PID。"""

    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            text=True,
            capture_output=True,
            timeout=FRONTEND_STOP_TIMEOUT_SECONDS,
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
        time.sleep(FRONTEND_STOP_POLL_INTERVAL_SECONDS)
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


def _remove_pid_file(
    pid_file: Path,
    cleanup: dict[str, Any],
    *,
    expected_pid: int | None,
) -> None:
    """仅在 PID 文件仍指向预期进程时删除它，避免清掉新启动记录。"""

    try:
        if not pid_file.is_file():
            return
        current_pid = int(pid_file.read_text(encoding="utf-8").strip())
        if expected_pid is not None and current_pid != expected_pid:
            cleanup["pid_file_preserved"] = True
            return
        pid_file.unlink()
        cleanup["pid_file_removed"] = True
    except (OSError, ValueError) as exc:
        cleanup["pid_file_error"] = str(exc)


def _base_launch_payload(
    *,
    root: Path,
    package_json_path: Path,
    package_manager: str,
    script_name: str,
    runtime_root: Path,
) -> dict[str, Any]:
    return {
        "workspace": str(root),
        "package_json_path": str(package_json_path),
        "package_json_relative_path": _relative(package_json_path, root),
        "package_manager": package_manager,
        "script": script_name,
        "runtime_root": str(runtime_root),
    }


def _failed_launch(
    message: str,
    *,
    root: Path,
    package_json_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "message": message,
        "workspace": str(root),
        "package_json_path": str(package_json_path) if package_json_path else None,
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
