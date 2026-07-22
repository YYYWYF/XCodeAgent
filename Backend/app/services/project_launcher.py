from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.utils.subprocess_output import subprocess_output_text
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


INSTALL_TIMEOUT_SECONDS = 120
SERVER_READY_TIMEOUT_SECONDS = 20
SERVER_READY_INTERVAL_SECONDS = 1


def launch_frontend_project(state: dict[str, Any]) -> dict[str, Any]:
    """安装并启动前端项目，仅在服务通过健康检查后返回可验收状态。"""

    root = workspace_root(state).resolve()
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
    runtime_root = workflow_artifact_root(state) / "runtime" / "launch"
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

    if not shutil.which(package_manager):
        return _failed_launch(
            f"未找到包管理器命令：{package_manager}",
            root=root,
            package_json_path=package_path,
        )

    install_result = _run_install(
        package_manager=package_manager,
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
            "message": "前端依赖安装失败。",
            "install": install_result,
        }

    launch_result, process = _start_dev_server(
        package_manager=package_manager,
        script_name=script_name,
        script_command=str(scripts.get(script_name, "")),
        cwd=package_path.parent,
        runtime_root=runtime_root,
        preview_url=preview_url,
    )
    ready = process is not None and _wait_until_ready(
        preview_url,
        process,
        stdout_log=Path(str(launch_result.get("stdout_log") or "")),
        stdout_offset=int(launch_result.get("stdout_offset") or 0),
    )
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
    return "npm"


def _run_install(
    *,
    package_manager: str,
    cwd: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    argv = [package_manager, "install"]
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
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired 的输出可能是 bytes，统一解码后再写入运行日志。
        stdout = subprocess_output_text(exc.stdout)
        stderr = subprocess_output_text(exc.stderr)
        returncode = None
        timed_out = True

    stdout_path = runtime_root / "install.stdout.log"
    stderr_path = runtime_root / "install.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "argv": argv,
        "cwd": str(cwd),
        "returncode": returncode,
        "timed_out": timed_out,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }


def _start_dev_server(
    *,
    package_manager: str,
    script_name: str,
    script_command: str,
    cwd: Path,
    runtime_root: Path,
    preview_url: str,
) -> tuple[dict[str, Any], subprocess.Popen[bytes] | None]:
    """以后台进程启动开发服务器，并保留进程对象供健康检查监督。"""

    argv = [package_manager, "run", script_name]
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
            start_new_session=True,
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
        env["HOST"] = "127.0.0.1"
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
        port = 5173 if "vite" in script else 3000
    return f"http://127.0.0.1:{port}"


def _script_port(script: str) -> int | None:
    match = re.search(r"(?:--port\s+|--port=|PORT=)(\d{2,5})", script)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _wait_until_ready(
    preview_url: str,
    process: subprocess.Popen[bytes],
    *,
    stdout_log: Path | None = None,
    stdout_offset: int = 0,
) -> bool:
    """结合 HTTP 与本次启动日志轮询就绪状态，并监督启动进程存活。"""

    deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
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
