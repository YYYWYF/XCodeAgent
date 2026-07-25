from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.backend_process_registry import (
    backend_launch_lock,
    register_backend_process,
    stop_previous_backend_process,
    terminate_backend_process,
)
from app.utils.subprocess_output import subprocess_output_text


BACKEND_BUILD_TIMEOUT_SECONDS = 600
BACKEND_READY_TIMEOUT_SECONDS = 60
BACKEND_READY_INTERVAL_SECONDS = 1
BACKEND_READY_MARKERS = ("Spring Boot Version", "ZA21 Version")


def launch_backend_project(workspace_path: str | Path) -> dict[str, Any]:
    """按普通工作目录构建并启动 Java 后端，不依赖 LangGraph 状态。"""

    root = Path(workspace_path).expanduser().resolve()
    backend_root = _find_backend_root(root)
    pom_path = backend_root / "pom.xml"
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    if not pom_path.is_file():
        return _failed_backend_launch(
            "未找到后端 Maven 工程：backend/pom.xml。",
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_validation",
        )
    maven_command = _find_maven_command(backend_root)
    if not maven_command:
        return _failed_backend_launch(
            "未找到 Maven 命令：mvn。",
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_validation",
        )
    java_command = shutil.which("java")
    if not java_command:
        return _failed_backend_launch(
            "未找到 Java 命令：java。",
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_validation",
        )

    runtime_root.mkdir(parents=True, exist_ok=True)
    with backend_launch_lock(root):
        return _launch_backend_project_locked(
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            maven_command=maven_command,
            java_command=java_command,
        )


def _find_backend_root(root: Path) -> Path:
    """显式识别 backend/Backend，避免依赖文件系统大小写折叠行为。"""

    candidates = [
        candidate
        for candidate in (root / "backend", root / "Backend")
        if candidate.is_dir()
    ]
    if len(candidates) == 1:
        return candidates[0]
    return root / "backend"


def _find_maven_command(backend_root: Path) -> str | None:
    """优先使用项目 Maven wrapper，再回退到当前 PATH 的全局 Maven。"""

    windows_wrapper = backend_root / "mvnw.cmd"
    posix_wrapper = backend_root / "mvnw"
    if os.name == "nt" and windows_wrapper.is_file():
        return str(windows_wrapper)
    if os.name != "nt" and posix_wrapper.is_file():
        return str(posix_wrapper)
    return shutil.which("mvn")


def _launch_backend_project_locked(
    *,
    root: Path,
    backend_root: Path,
    pom_path: Path,
    runtime_root: Path,
    maven_command: str,
    java_command: str,
) -> dict[str, Any]:
    """在工作区锁内完成旧进程清理、Maven 构建和新 Java 进程启动。"""

    prebuild_cleanup = stop_previous_backend_process(
        workspace=root,
        backend_root=backend_root,
        runtime_root=runtime_root,
    )
    if not prebuild_cleanup["success"]:
        return _failed_backend_launch(
            "无法安全停止上一次 Java 后端进程，已中止 Maven 构建。",
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_cleanup",
            prebuild_cleanup=prebuild_cleanup,
        )

    build_result = _run_backend_build(
        maven_command=maven_command,
        cwd=backend_root,
        runtime_root=runtime_root,
    )
    if build_result["returncode"] != 0:
        message = (
            "后端 Maven 构建命令执行失败。"
            if build_result.get("error")
            else "后端 Maven 构建失败。"
        )
        return _failed_backend_launch(
            message,
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_build",
            build=build_result,
            prebuild_cleanup=prebuild_cleanup,
        )

    target_root = backend_root / "target"
    jar_path, candidates = _find_backend_snapshot_jar(target_root)
    if jar_path is None:
        message = (
            "backend/target 中未找到可启动的 *-SNAPSHOT.jar。"
            if not candidates
            else "backend/target 中存在多个可启动的 *-SNAPSHOT.jar，无法确定主程序。"
        )
        return _failed_backend_launch(
            message,
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_jar",
            build=build_result,
            target_root=target_root,
            jar_candidates=candidates,
            prebuild_cleanup=prebuild_cleanup,
        )

    server_result, process = _start_backend_server(
        java_command=java_command,
        jar_path=jar_path,
        runtime_root=runtime_root,
    )
    if process is not None:
        register_backend_process(root, process)
    ready = process is not None and _wait_for_backend_ready(
        process,
        stdout_log=Path(str(server_result.get("stdout_log") or "")),
        stdout_offset=int(server_result.get("stdout_offset") or 0),
        stderr_log=Path(str(server_result.get("stderr_log") or "")),
        stderr_offset=int(server_result.get("stderr_offset") or 0),
    )
    returncode = process.poll() if process is not None else None
    process_running = process is not None and returncode is None
    server = {
        **server_result,
        "ready": ready,
        "returncode": returncode,
        "ready_checked_at": datetime.now(UTC).isoformat(),
    }
    if not ready or not process_running:
        if returncode is not None:
            message = f"Java 后端进程已退出（退出码：{returncode}）。"
        elif process is None:
            message = "Java 后端启动命令执行失败。"
        else:
            message = "Java 后端就绪检查超时。"
        if process is not None:
            pid_file_value = server_result.get("pid_file")
            server["cleanup"] = terminate_backend_process(
                workspace=root,
                process=process,
                pid_file=Path(str(pid_file_value)) if pid_file_value else None,
            )
        return _failed_backend_launch(
            message,
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
            failed_stage="backend_start",
            build=build_result,
            target_root=target_root,
            jar_path=jar_path,
            jar_candidates=candidates,
            server=server,
            prebuild_cleanup=prebuild_cleanup,
        )

    return {
        **_base_backend_launch_payload(
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
        ),
        "status": "running",
        "message": "Java 后端项目已启动并就绪。",
        "prebuild_cleanup": prebuild_cleanup,
        "build": build_result,
        "target_path": str(target_root),
        "jar_path": str(jar_path),
        "jar_relative_path": _relative(jar_path, root),
        "jar_candidates": [str(path) for path in candidates],
        "server": server,
        # 仅供 launch_project 在前端失败时回滚，节点写入状态前必须移除此对象。
        "_process": process,
    }


def stop_backend_project(
    launch_result: dict[str, Any],
    process: subprocess.Popen[bytes] | None,
) -> dict[str, Any]:
    """停止本次启动的 Java 进程，并把清理证据补充到后端启动结果。"""

    server = launch_result.get("server")
    if not isinstance(server, dict):
        server = {}
        launch_result["server"] = server
    pid_file_value = server.get("pid_file")
    workspace_value = launch_result.get("workspace")
    workspace = (
        Path(str(workspace_value)).expanduser().resolve()
        if workspace_value
        else None
    )
    pid_file = Path(str(pid_file_value)) if pid_file_value else None
    if workspace is None:
        cleanup = terminate_backend_process(
            workspace=None,
            process=process,
            pid_file=pid_file,
        )
    else:
        with backend_launch_lock(workspace):
            cleanup = terminate_backend_process(
                workspace=workspace,
                process=process,
                pid_file=pid_file,
            )
    server["cleanup"] = cleanup
    launch_result["status"] = "stopped"
    launch_result["message"] = "前端启动失败，已停止本次 Java 后端进程。"
    return cleanup


def _run_backend_build(
    *,
    maven_command: str,
    cwd: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    """执行 Maven clean install，并把完整输出写入后端构建日志。"""

    argv = [maven_command, "clean", "install"]
    started_at = datetime.now(UTC).isoformat()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=BACKEND_BUILD_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = subprocess_output_text(completed.stdout)
        stderr = subprocess_output_text(completed.stderr)
        returncode = completed.returncode
        timed_out = False
        error = None
    except subprocess.TimeoutExpired as exc:
        # Maven 超时时仍需保留已产生的输出，便于用户定位下载或构建卡点。
        stdout = subprocess_output_text(exc.stdout)
        stderr = subprocess_output_text(exc.stderr)
        returncode = None
        timed_out = True
        error = None
    except OSError as exc:
        # Windows 上 mvn 通常解析为 mvn.cmd；启动异常必须转换为业务失败而非中断 Workflow。
        stdout = ""
        stderr = str(exc)
        returncode = None
        timed_out = False
        error = str(exc)

    stdout_path = runtime_root / "backend-build.stdout.log"
    stderr_path = runtime_root / "backend-build.stderr.log"
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


def _find_backend_snapshot_jar(target_root: Path) -> tuple[Path | None, list[Path]]:
    """筛选 Maven target 中唯一可执行的 SNAPSHOT 主 JAR。"""

    if not target_root.is_dir():
        return None, []
    candidates = sorted(
        (
            path
            for path in target_root.glob("*-SNAPSHOT.jar")
            if path.is_file() and not _is_auxiliary_snapshot_jar(path)
        ),
        key=lambda path: path.name,
    )
    return (candidates[0] if len(candidates) == 1 else None), candidates


def _is_auxiliary_snapshot_jar(path: Path) -> bool:
    """识别 original、源码、文档和测试等不可直接启动的附属 JAR。"""

    name = path.name.lower()
    return name.startswith("original-") or any(
        name.endswith(suffix)
        for suffix in ("-sources.jar", "-javadoc.jar", "-tests.jar", "-test.jar")
    )


def _start_backend_server(
    *,
    java_command: str,
    jar_path: Path,
    runtime_root: Path,
) -> tuple[dict[str, Any], subprocess.Popen[bytes] | None]:
    """在 target 目录后台启动 Java JAR，并记录本次日志偏移量。"""

    argv = [java_command, "-jar", str(jar_path.resolve())]
    stdout_path = runtime_root / "backend.stdout.log"
    stderr_path = runtime_root / "backend.stderr.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    stdout_offset = stdout.tell()
    stderr_offset = stderr.tell()
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(jar_path.parent),
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            # macOS Java 服务继承 Electron 后端进程组，应用退出时可统一回收。
            start_new_session=os.name == "nt",
        )
    except OSError as exc:
        stdout.close()
        stderr.close()
        return (
            {
                "argv": argv,
                "cwd": str(jar_path.parent),
                "pid": None,
                "error": str(exc),
                "stdout_log": str(stdout_path),
                "stdout_offset": stdout_offset,
                "stderr_log": str(stderr_path),
                "stderr_offset": stderr_offset,
            },
            None,
        )
    stdout.close()
    stderr.close()
    pid_path = runtime_root / "backend.pid"
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return (
        {
            "argv": argv,
            "cwd": str(jar_path.parent),
            "pid": process.pid,
            "pid_file": str(pid_path),
            "stdout_log": str(stdout_path),
            "stdout_offset": stdout_offset,
            "stderr_log": str(stderr_path),
            "stderr_offset": stderr_offset,
            "started_at": datetime.now(UTC).isoformat(),
        },
        process,
    )


def _wait_for_backend_ready(
    process: subprocess.Popen[bytes],
    *,
    stdout_log: Path,
    stdout_offset: int,
    stderr_log: Path,
    stderr_offset: int,
) -> bool:
    """监督 Java 进程，并等待本次日志出现约定的版本标志。"""

    deadline = time.monotonic() + BACKEND_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _backend_logs_are_ready(
            stdout_log=stdout_log,
            stdout_offset=stdout_offset,
            stderr_log=stderr_log,
            stderr_offset=stderr_offset,
        ):
            return process.poll() is None
        time.sleep(BACKEND_READY_INTERVAL_SECONDS)
    return False


def _backend_logs_are_ready(
    *,
    stdout_log: Path,
    stdout_offset: int,
    stderr_log: Path,
    stderr_offset: int,
) -> bool:
    """仅检查本次 Java 启动新增的 stdout/stderr 是否包含版本就绪标志。"""

    return _log_contains_backend_ready_marker(stdout_log, stdout_offset) or (
        _log_contains_backend_ready_marker(stderr_log, stderr_offset)
    )


def _log_contains_backend_ready_marker(path: Path, offset: int) -> bool:
    """从指定偏移读取日志，并按大小写精确匹配后端版本标志。"""

    try:
        with path.open("rb") as stream:
            stream.seek(max(0, offset))
            content = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in content for marker in BACKEND_READY_MARKERS)


def _base_backend_launch_payload(
    *,
    root: Path,
    backend_root: Path,
    pom_path: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    """构造稳定的 Java 后端启动结果公共字段。"""

    return {
        "workspace": str(root),
        "backend_path": str(backend_root),
        "backend_relative_path": _relative(backend_root, root),
        "pom_path": str(pom_path),
        "pom_relative_path": _relative(pom_path, root),
        "runtime_root": str(runtime_root),
    }


def _failed_backend_launch(
    message: str,
    *,
    root: Path,
    backend_root: Path,
    pom_path: Path,
    runtime_root: Path,
    failed_stage: str,
    build: dict[str, Any] | None = None,
    target_root: Path | None = None,
    jar_path: Path | None = None,
    jar_candidates: list[Path] | None = None,
    server: dict[str, Any] | None = None,
    prebuild_cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造包含失败阶段和可审计证据的 Java 后端失败结果。"""

    return {
        **_base_backend_launch_payload(
            root=root,
            backend_root=backend_root,
            pom_path=pom_path,
            runtime_root=runtime_root,
        ),
        "status": "failed",
        "message": message,
        "failed_stage": failed_stage,
        "prebuild_cleanup": prebuild_cleanup,
        "build": build,
        "target_path": str(target_root) if target_root else None,
        "jar_path": str(jar_path) if jar_path else None,
        "jar_candidates": [str(path) for path in (jar_candidates or [])],
        "server": server,
    }


def _relative(path: Path, root: Path) -> str:
    """尽可能返回相对工作区路径，越界时保留绝对路径。"""

    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
