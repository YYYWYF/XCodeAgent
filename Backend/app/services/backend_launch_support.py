"""验收预览和测试启动检测共享的 JAR、补打包及数据库环境能力。"""
from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR

import os
import subprocess
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.database_credentials import (
    DatabaseCredentialError, build_mysql_jdbc_url,
)
from app.services.database_runtime_source import select_runtime_database, resolve_runtime_mysql_config
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
        "XCODEAGENT_DB_URL",
        "XCODEAGENT_DB_PORT",
        "XCODEAGENT_DB_SCHEMA",
        "XCODEAGENT_DB_USERNAME",
        "XCODEAGENT_DB_PASSWORD",
        "DATASOURCE_DS1_URL",
        "DATASOURCE_DS1_PORT",
        "DATASOURCE_DS1_SCHEMA",
        "DATASOURCE_DS1_USERNAME",
        "DATASOURCE_DS1_PASSWORD",
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


def _backend_runtime_environment(
    root: Path, *, managed_snapshot=None, on_database_check: Callable[[dict], None] | None = None,
) -> tuple[dict[str, str], str | None]:
    """清除全局数据库变量，仅在应用配置了连接时注入数据库环境。"""

    environment = os.environ.copy()
    for key in _BACKEND_DATABASE_ENV_KEYS:
        environment.pop(key, None)
    # SaaS 服务密钥只属于 Studio，不能继承给生成的业务应用。
    environment.pop("MANAGED_DATABASE_SERVICE_KEY", None)

    application_file = root / WORKSPACE_ARTIFACT_DIR / "application.json"
    if not application_file.is_file():
        return environment, None
    try:
        from app.services.managed_database_runtime import prepare_managed_startup, resolve_managed_runtime

        snapshot = managed_snapshot or prepare_managed_startup(root, on_readiness=on_database_check)
        if snapshot is not None:
            connection = resolve_managed_runtime(root, snapshot, on_readiness=on_database_check)
            environment.update(_database_runtime_variables(
                host=connection.host, port=connection.port, schema=connection.database,
                username=connection.username, password=connection.password.get_secret_value(),
                jdbc_url=connection.jdbc_url(),
            ))
            environment.update({
                "SPRING_SQL_INIT_MODE": "never", "SPRING_JPA_HIBERNATE_DDL_AUTO": "none",
                "SPRING_FLYWAY_ENABLED": "false", "SPRING_LIQUIBASE_ENABLED": "false",
            })
            return environment, None
        # 外部连接只从应用内当前数据源目录获取，尚未新增数据库时不阻断启动。
        source = select_runtime_database(root)
        if source is None:
            return environment, None
        config = resolve_runtime_mysql_config(root, source)
    except DatabaseCredentialError as exc:
        return environment, str(exc)

    jdbc_url = build_mysql_jdbc_url(config)
    environment.update(_database_runtime_variables(
        host=config.host, port=config.port, schema=config.database,
        username=config.user, password=config.password, jdbc_url=jdbc_url,
    ))
    return environment, None


def _database_runtime_variables(
    *, host: str, port: int, schema: str, username: str, password: str, jdbc_url: str,
) -> dict[str, str]:
    """将同一连接投影为 MySQL、Spring、xcode-agent.db 及 datasource.ds1 的运行时环境变量。"""

    return {
        "MYSQL_HOST": host, "MYSQL_PORT": str(port), "MYSQL_DATABASE": schema,
        "MYSQL_USER": username, "MYSQL_PWD": password, "MYSQL_JDBC_URL": jdbc_url,
        "SPRING_DATASOURCE_URL": jdbc_url, "SPRING_DATASOURCE_USERNAME": username,
        "SPRING_DATASOURCE_PASSWORD": password,
        # Spring 环境变量绑定去掉配置名中的连字符；url 是地址，端口和 Schema 单独注入。
        "XCODEAGENT_DB_URL": host, "XCODEAGENT_DB_PORT": str(port),
        "XCODEAGENT_DB_SCHEMA": schema, "XCODEAGENT_DB_USERNAME": username,
        "XCODEAGENT_DB_PASSWORD": password,
        "DATASOURCE_DS1_URL": host, "DATASOURCE_DS1_PORT": str(port),
        "DATASOURCE_DS1_SCHEMA": schema, "DATASOURCE_DS1_USERNAME": username,
        "DATASOURCE_DS1_PASSWORD": password,
    }
