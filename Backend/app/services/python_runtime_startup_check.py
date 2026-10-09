"""测试阶段对生成的 Python Agent Runtime 执行临时健康启动检查。"""

from __future__ import annotations

import secrets
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.agent_runtime_launch_support import (
    agent_runtime_environment,
    allocate_loopback_port,
    wait_for_agent_runtime_ready,
)
from app.services.agent_runtime_project_launcher import _direct_runtime_auth_enabled
from app.services.backend_startup_diagnostics import (
    OUTPUT_LIMIT,
    read_log_tail,
    redact_startup_text,
    sanitize_startup_log,
    startup_root_cause,
    startup_source_fingerprint,
)
from app.services.workspace_process_registry import workspace_process_registry


def run_python_runtime_startup_check(
    *,
    root: Path,
    runtime_root: Path,
    log_root: Path,
    run_id: str,
    uv_command: str | None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """使用已同步的依赖临时启动 Runtime，验证 /health 后回收进程。"""

    result: dict[str, Any] = {
        "id": "backend_startup", "name": "后端启动检查", "layer": "backend",
        "language": "python", "passed": False, "skipped": False,
        "required": True, "blocking": True,
        "evidence": "正在启动临时 Agent Runtime 并验证 /health。",
    }
    _report(on_progress, result, "running")
    attempt_root = log_root / "backend_startup"
    attempt_root.mkdir(parents=True, exist_ok=True)
    stdout_path = attempt_root / "stdout.log"
    stderr_path = attempt_root / "stderr.log"
    environment: dict[str, str] = {}
    argv = [uv_command or "uv", "run", "--no-sync", "agent-runtime"]
    execution: dict[str, Any] = {
        "tool": "subprocess", "argv": argv, "cwd": "agent-runtime",
        "returncode": None, "timed_out": False, "error": None,
        "started_at": datetime.now(UTC).isoformat(),
    }
    process: subprocess.Popen[Any] | None = None
    try:
        if not uv_command:
            raise RuntimeError("未找到 uv 命令，无法启动 Agent Runtime。")
        port = allocate_loopback_port()
        environment = agent_runtime_environment(
            Settings.from_env(), port=port,
            gateway_token=secrets.token_urlsafe(32),
            direct_auth_enabled=_direct_runtime_auth_enabled(root),
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            with workspace_process_registry.managed_process(
                argv, workspace=root, run_id=run_id, cwd=runtime_root,
                env=environment, stdout=stdout, stderr=stderr,
                stdin=subprocess.DEVNULL,
            ) as process:
                ready, health = wait_for_agent_runtime_ready(
                    f"http://127.0.0.1:{port}", process, timeout_seconds=60,
                )
                result["passed"] = bool(ready and process.poll() is None)
                result["evidence"] = (
                    "Agent Runtime /health 就绪，临时进程已回收。"
                    if result["passed"]
                    else f"Agent Runtime 未就绪；最后一次健康探测：{health}。"
                )
                execution["returncode"] = process.poll()
                execution["timed_out"] = not ready and process.poll() is None
    except (OSError, RuntimeError, ValueError) as exc:
        result["evidence"] = f"Agent Runtime 启动检查失败：{exc}"
        execution["error"] = str(exc)
    finally:
        for path in (stdout_path, stderr_path):
            if path.is_file():
                sanitize_startup_log(path, environment)
    stdout_text = read_log_tail(stdout_path) if stdout_path.is_file() else ""
    stderr_text = read_log_tail(stderr_path) if stderr_path.is_file() else ""
    root_cause = startup_root_cause(stdout_text, stderr_text)
    if not result["passed"] and root_cause:
        result["evidence"] += "\n启动异常：" + root_cause
    if not result["passed"] and stderr_text:
        result["evidence"] += "\n" + stderr_text[-2_000:]
    result["evidence"] = redact_startup_text(str(result["evidence"]), environment)[:OUTPUT_LIMIT]
    result["failure_category"] = None if result["passed"] else "backend_startup"
    result["command"] = " ".join(argv)
    execution.update({
        "finished_at": datetime.now(UTC).isoformat(),
        "cleanup_succeeded": process is None or process.poll() is not None,
        "stdout_tail": stdout_text[-OUTPUT_LIMIT:],
        "stderr_tail": stderr_text[-OUTPUT_LIMIT:],
        "root_cause": root_cause or None,
        "stdout_log": "/" + stdout_path.relative_to(root).as_posix(),
        "stderr_log": "/" + stderr_path.relative_to(root).as_posix(),
        "stdout_log_virtual": "/" + stdout_path.relative_to(root).as_posix(),
        "stderr_log_virtual": "/" + stderr_path.relative_to(root).as_posix(),
    })
    if execution["error"]:
        execution["error"] = redact_startup_text(str(execution["error"]), environment)
    result["execution"] = execution
    result["source_fingerprint"] = startup_source_fingerprint(root)
    result["repair_hints"] = [
        "agent-runtime/pyproject.toml", "agent-runtime/uv.lock",
        "agent-runtime/src/app/main.py", "agent-runtime/src/app/server/app.py",
    ]
    _report(on_progress, result, "passed" if result["passed"] else "failed")
    return result


def _report(
    reporter: Callable[[dict[str, Any]], None] | None,
    check: dict[str, Any],
    status: str,
) -> None:
    """向既有质量矩阵发送增量，展示失败不影响检测结论。"""

    if reporter:
        try:
            reporter({"check": dict(check), "status": status})
        except Exception:
            pass
