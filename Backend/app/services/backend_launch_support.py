"""验收预览和测试启动检测共享的 JAR、补打包及数据库环境能力。"""
from __future__ import annotations

import os
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.database_credentials import (
    DatabaseCredentialError, build_mysql_jdbc_url, resolve_application_mysql_config,
)
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.subprocess_output import subprocess_output_text

BACKEND_REPACKAGE_TIMEOUT_SECONDS = 600
_BACKEND_DATABASE_ENV_KEYS = frozenset(
    {
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PWD",
        "MYSQL_DATABASE",
        "MYSQL_JDBC_URL",
        "SPRING_DATASOURCE_URL",
        "SPRING_DATASOURCE_USERNAME",
        "SPRING_DATASOURCE_PASSWORD",
    }
)


def _run_backend_repackage(
    *,
    maven_command: str,
    workspace: Path,
    cwd: Path,
    runtime_root: Path,
    run_id: str = "",
    skip_tests: bool = False,
) -> dict[str, Any]:
    """为缺少 Main-Class 的普通 JAR 补执行 Spring Boot repackage。"""

    argv = [maven_command, "-B", "package", "spring-boot:repackage"]
    if skip_tests:
        argv.insert(2, "-Dmaven.test.skip=true")
    started_at = datetime.now(UTC).isoformat()
    try:
        completed = workspace_process_registry.run(
            argv,
            workspace=workspace,
            run_id=run_id,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=BACKEND_REPACKAGE_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = subprocess_output_text(completed.stdout)
        stderr = subprocess_output_text(completed.stderr)
        returncode = completed.returncode
        timed_out = False
        error = None
    except subprocess.TimeoutExpired as exc:
        stdout = subprocess_output_text(exc.stdout)
        stderr = subprocess_output_text(exc.stderr)
        returncode = None
        timed_out = True
        error = None
    except OSError as exc:
        stdout = ""
        stderr = str(exc)
        returncode = None
        timed_out = False
        error = str(exc)

    stdout_path = runtime_root / "backend-repackage.stdout.log"
    stderr_path = runtime_root / "backend-repackage.stderr.log"
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


def _jar_has_main_class(path: Path) -> bool | None:
    """读取 JAR 清单，返回是否含 Main-Class；损坏文件返回未知。"""

    try:
        with zipfile.ZipFile(path) as archive:
            manifest = archive.read("META-INF/MANIFEST.MF").decode(
                "utf-8", errors="replace"
            )
    except (OSError, KeyError, zipfile.BadZipFile):
        return None

    return any(
        line.casefold().startswith("main-class:")
        and line.split(":", 1)[1].strip()
        for line in manifest.splitlines()
    )


def _is_auxiliary_snapshot_jar(path: Path) -> bool:
    """识别 original、源码、文档和测试等不可直接启动的附属 JAR。"""

    name = path.name.lower()
    return name.startswith("original-") or any(
        name.endswith(suffix)
        for suffix in ("-sources.jar", "-javadoc.jar", "-tests.jar", "-test.jar")
    )


def _backend_runtime_environment(root: Path) -> tuple[dict[str, str], str | None]:
    """构造后端子进程环境，清除全局数据库变量并绑定当前应用配置。"""

    environment = os.environ.copy()
    for key in _BACKEND_DATABASE_ENV_KEYS:
        environment.pop(key, None)

    application_file = root / ".xcodeagent" / "application.json"
    if not application_file.is_file():
        return environment, None
    try:
        config = resolve_application_mysql_config(root)
    except DatabaseCredentialError as exc:
        return environment, str(exc)

    jdbc_url = build_mysql_jdbc_url(config)
    environment.update(
        {
            "MYSQL_HOST": config.host,
            "MYSQL_PORT": str(config.port),
            "MYSQL_USER": config.user,
            "MYSQL_PWD": config.password,
            "MYSQL_DATABASE": config.database,
            "MYSQL_JDBC_URL": jdbc_url,
            "SPRING_DATASOURCE_URL": jdbc_url,
            "SPRING_DATASOURCE_USERNAME": config.user,
            "SPRING_DATASOURCE_PASSWORD": config.password,
        }
    )
    return environment, None

