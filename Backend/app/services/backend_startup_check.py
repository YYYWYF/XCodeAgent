"""测试阶段独立 Java 启动检查；不接管已有验收或预览进程。"""
from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.backend_launch_support import (
    _backend_runtime_environment, _find_backend_snapshot_jar,
    _jar_has_main_class, _run_backend_repackage,
)
from app.services.backend_startup_diagnostics import (
    OUTPUT_LIMIT, read_log_tail, redact_startup_text,
    sanitize_startup_log, startup_root_cause, startup_source_fingerprint,
)
from app.services.workspace_process_registry import workspace_process_registry

BACKEND_STARTUP_TIMEOUT_SECONDS = 60
BACKEND_STARTUP_STABILITY_SECONDS = 3
CHECK_ID = "backend_startup"
CHECK_NAME = "后端启动检查"
_STARTED = re.compile(r"\bStarted\s+\S+.*\bin\s+\d[\d.]*\s+seconds")
_PORT = re.compile(
    r"(?:Tomcat|Jetty|Netty|Undertow).*?started.*?port(?:\(s\)|s)?\s*:?\s*(\d+)", re.I,
)
_FATAL = re.compile(r"APPLICATION FAILED TO START|Application run failed|cancelling refresh attempt")


def _port_ready(port: int) -> bool:
    """探测本次进程日志声明的本地端口，不发送业务请求或依赖额外健康端点。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def wait_for_startup(
    process: subprocess.Popen[Any], stdout: Path, stderr: Path, run_id: str,
) -> tuple[bool, bool, str]:
    """等待完整初始化、端口监听和稳定存活，横幅或旧预览端口均不能充当成功证据。"""
    deadline = time.monotonic() + BACKEND_STARTUP_TIMEOUT_SECONDS
    stable_since: float | None = None
    while time.monotonic() < deadline:
        if workspace_process_registry.is_run_cancelled(run_id):
            return False, False, "当前运行已停止，启动检测已取消。"
        output = read_log_tail(stdout) + "\n" + read_log_tail(stderr)
        if process.poll() is not None:
            return False, False, f"Java 后端进程提前退出（退出码：{process.returncode}）。"
        if _FATAL.search(output):
            return False, False, "Java 后端应用初始化失败。"
        port_match = _PORT.search(output)
        ready = bool(
            _STARTED.search(output) and port_match
            and 0 < int(port_match.group(1)) <= 65535
            and _port_ready(int(port_match.group(1)))
        )
        now = time.monotonic()
        stable_since = (stable_since if stable_since is not None else now) if ready else None
        if stable_since is not None and now - stable_since >= BACKEND_STARTUP_STABILITY_SECONDS:
            return True, False, "后端初始化完成、HTTP 端口可连接且稳定存活，启动检查通过。"
        time.sleep(0.1)
    return False, True, "后端启动检查超时：未在 60 秒内完成初始化、端口监听和稳定存活检查。"


def _repair_hints(backend_root: Path, root: Path) -> list[str]:
    """根据真实工程路径提供配置和启动类线索，兼顾 Backend 大小写目录。"""
    paths = [backend_root / "pom.xml"]
    resources = backend_root / "src/main/resources"
    paths.extend(p for p in resources.glob("application*") if p.is_file())
    for path in (backend_root / "src/main/java").rglob("*.java"):
        try:
            if "@SpringBootApplication" in path.read_text(encoding="utf-8", errors="replace"):
                paths.append(path)
        except OSError:
            continue
    return [path.relative_to(root).as_posix() for path in paths][:20]


def run_backend_startup_check(
    *, root: Path, backend_root: Path, log_root: Path, run_id: str,
    maven_command: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """启动本轮构建产物并回收进程，将结构化错误与独立日志交给现有质量门禁。"""
    result: dict[str, Any] = {
        "id": CHECK_ID, "name": CHECK_NAME, "layer": "backend", "language": "java",
        "passed": False, "skipped": False, "required": True, "blocking": True,
        "command": None, "evidence": "正在启动临时 Java 进程并验证就绪。",
    }
    _report(on_progress, result, "running")
    attempt_root = log_root / CHECK_ID / uuid4().hex
    attempt_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    stdout_path, stderr_path = attempt_root / "stdout.log", attempt_root / "stderr.log"
    stdout_path.touch(mode=0o600)
    stderr_path.touch(mode=0o600)
    environment: dict[str, str] = {}
    process: subprocess.Popen[Any] | None = None
    execution: dict[str, Any] = {
        "tool": "subprocess", "argv": [], "cwd": backend_root.relative_to(root).as_posix(),
        "returncode": None, "timed_out": False, "error": None,
        "started_at": datetime.now(UTC).isoformat(),
    }
    category = "backend_startup"
    try:
        environment, config_error = _backend_runtime_environment(root)
        if config_error:
            raise RuntimeError(f"后端启动配置不可用：{config_error}")
        java = shutil.which("java")
        if not java:
            raise RuntimeError("未找到 Java 命令：java。")
        jar, _candidates = _find_backend_snapshot_jar(backend_root / "target")
        if jar is None:
            raise RuntimeError("本轮构建未产生唯一的 SNAPSHOT 主 JAR。")
        if _jar_has_main_class(jar) is False:
            repackage = _run_backend_repackage(
                maven_command=maven_command, workspace=root, cwd=backend_root,
                runtime_root=attempt_root, run_id=run_id, skip_tests=True,
            )
            execution["argv"] = repackage["argv"]
            execution["returncode"] = repackage["returncode"]
            execution["timed_out"] = repackage["timed_out"]
            for key, destination in (("stdout_log", stdout_path), ("stderr_log", stderr_path)):
                destination.write_bytes(Path(repackage[key]).read_bytes())
            if repackage["returncode"] != 0:
                category = "backend_repackage"
                raise RuntimeError("后端 JAR 补打包失败。")
            jar, _candidates = _find_backend_snapshot_jar(backend_root / "target")
        if jar is None or _jar_has_main_class(jar) is not True:
            raise RuntimeError("后端 JAR 损坏或缺少可执行 Main-Class。")
        argv = [java, "-jar", str(jar), "--server.address=127.0.0.1", "--server.port=0"]
        execution["argv"] = [java, "-jar", jar.relative_to(root).as_posix(), *argv[3:]]
        execution["cwd"] = jar.parent.relative_to(root).as_posix()
        execution["returncode"] = None
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            with workspace_process_registry.managed_process(
                argv, workspace=root, run_id=run_id, cwd=jar.parent, env=environment,
                stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
            ) as process:
                passed, timed_out, evidence = wait_for_startup(
                    process, stdout_path, stderr_path, run_id,
                )
                result.update(passed=passed, evidence=evidence)
                execution["timed_out"] = timed_out
                # 保存主动回收之前的退出码；通过后的 SIGTERM 不是应用启动失败。
                execution["returncode"] = process.poll()
                if passed and (
                    execution["returncode"] is not None
                    or workspace_process_registry.is_run_cancelled(run_id)
                ):
                    result.update(passed=False, evidence="检测完成前进程已退出或当前运行已停止。")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(passed=False, evidence=f"后端启动检查失败：{exc}")
        execution["error"] = str(exc)
    finally:
        # 源日志也清理，避免修复 Agent 通过虚拟路径读到数据库密码。
        for path in attempt_root.glob("*.log"):
            sanitize_startup_log(path, environment)
    stdout_text, stderr_text = read_log_tail(stdout_path), read_log_tail(stderr_path)
    root_cause = startup_root_cause(stdout_text, stderr_text) if not result["passed"] else ""
    result["evidence"] = redact_startup_text(
        str(result["evidence"]) + ("\n" + root_cause if root_cause else ""), environment,
    )[:OUTPUT_LIMIT]
    result["failure_category"] = None if result["passed"] else category
    result["command"] = " ".join(execution["argv"]) or None
    execution.update(
        root_cause=root_cause, stdout_tail=stdout_text[-OUTPUT_LIMIT:],
        stderr_tail=stderr_text[-OUTPUT_LIMIT:], finished_at=datetime.now(UTC).isoformat(),
        cleanup_succeeded=process is None or process.poll() is not None,
        error=redact_startup_text(str(execution["error"]), environment) if execution["error"] else None,
    )
    for key, path in (("stdout_log", stdout_path), ("stderr_log", stderr_path)):
        execution[key] = "/" + path.relative_to(root).as_posix()
        execution[key + "_virtual"] = execution[key]
    result["execution"] = execution
    result["source_fingerprint"] = startup_source_fingerprint(root)
    result["repair_hints"] = _repair_hints(backend_root, root)
    _report(on_progress, result, "passed" if result["passed"] else "failed")
    return result


def _report(
    reporter: Callable[[dict[str, Any]], None] | None, check: dict[str, Any], status: str,
) -> None:
    """发送沿用检查清单契约的增量；展示回调失败不改变检测结论。"""
    if reporter:
        try:
            reporter({"check": dict(check), "status": status})
        except Exception:
            pass
