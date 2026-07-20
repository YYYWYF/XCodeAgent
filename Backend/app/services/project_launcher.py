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
from urllib.error import URLError
from urllib.request import urlopen

from app.utils.subprocess_output import subprocess_output_text
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


INSTALL_TIMEOUT_SECONDS = 120
SERVER_READY_TIMEOUT_SECONDS = 20
SERVER_READY_INTERVAL_SECONDS = 1


def launch_frontend_project(state: dict[str, Any]) -> dict[str, Any]:
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
    if not shutil.which(package_manager):
        return _failed_launch(
            f"未找到包管理器命令：{package_manager}",
            root=root,
            package_json_path=package_path,
        )

    runtime_root = workflow_artifact_root(state) / "runtime" / "launch"
    runtime_root.mkdir(parents=True, exist_ok=True)
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

    preview_url = _preview_url(scripts.get(script_name, ""))
    launch_result = _start_dev_server(
        package_manager=package_manager,
        script_name=script_name,
        cwd=package_path.parent,
        runtime_root=runtime_root,
        preview_url=preview_url,
    )
    ready = _wait_until_ready(preview_url)
    return {
        **_base_launch_payload(
            root=root,
            package_json_path=package_path,
            package_manager=package_manager,
            script_name=script_name,
            runtime_root=runtime_root,
        ),
        "status": "running" if launch_result.get("pid") else "failed",
        "message": (
            "前端项目已启动并可访问。"
            if ready
            else "前端启动命令已执行，预览服务可能仍在编译中。"
        ),
        "preview_url": preview_url,
        "install": install_result,
        "server": {
            **launch_result,
            "ready": ready,
            "ready_checked_at": datetime.now(UTC).isoformat(),
        },
    }


def _find_frontend_package_json(root: Path) -> Path | None:
    candidates = [
        root / "Frontend" / "package.json",
        root / "frontend" / "package.json",
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
    cwd: Path,
    runtime_root: Path,
    preview_url: str,
) -> dict[str, Any]:
    argv = [package_manager, "run", script_name]
    stdout_path = runtime_root / "frontend.stdout.log"
    stderr_path = runtime_root / "frontend.stderr.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    env = {
        **os.environ,
        "BROWSER": "none",
        "HOST": "127.0.0.1",
    }
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
        return {
            "argv": argv,
            "cwd": str(cwd),
            "pid": None,
            "error": str(exc),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "preview_url": preview_url,
        }
    stdout.close()
    stderr.close()
    pid_path = runtime_root / "frontend.pid"
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return {
        "argv": argv,
        "cwd": str(cwd),
        "pid": process.pid,
        "pid_file": str(pid_path),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "preview_url": preview_url,
        "started_at": datetime.now(UTC).isoformat(),
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


def _wait_until_ready(preview_url: str) -> bool:
    deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urlopen(preview_url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True
        except (OSError, URLError):
            time.sleep(SERVER_READY_INTERVAL_SECONDS)
    return False


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
