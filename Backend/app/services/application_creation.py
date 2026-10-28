"""新应用先在隔离暂存区完成初始化，成功后才提交目标工作区。"""

from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.application_lifecycle import ApplicationLifecycle
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import validate_application_configuration
from app.services.application_lifecycle import ensure_application_lifecycle, load_application_lifecycle
from app.services.managed_database_registration import ensure_managed_database_registration

_CREATION_LOCK = threading.Lock()
# 注册可能已经在远端成功时保留独立暂存区，重试复用原身份，绝不重新申请资源。


def _assert_empty_target(target: Path) -> None:
    """只读校验目标是未占用的真实目录，不更改已有项目。"""

    if target.is_symlink():
        raise ValueError("新应用项目目录不能是符号链接。")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("新应用项目目录必须是不存在或为空的独立目录。")
    if not target.parent.is_dir():
        raise ValueError("项目创建位置的上级目录不存在。")


def initialize_application(
    workspace: str, *, application_id: str, application_name: str,
    configuration: dict[str, Any], thread_id: str,
) -> tuple[ApplicationLifecycle, dict[str, Any]]:
    """隔离应用配置、生命周期和数据库注册，全部就绪才原子提交元数据目录。"""

    validate_application_configuration(configuration)
    if "managedDatabase" in configuration:
        raise ValueError("创建请求不能提供模拟数据库注册结果。")
    if configuration.get("appName") != application_name:
        raise ValueError("应用名称与创建配置不一致。")
    raw_target = Path(workspace).expanduser().absolute()
    if raw_target.is_symlink():
        raise ValueError("新应用项目目录不能是符号链接。")
    target = raw_target.resolve()
    with _CREATION_LOCK:
        existing = load_application_lifecycle(target)
        if existing is not None and existing.application.id == application_id:
            return existing, ApplicationConfigRepository(target).load()
        _assert_empty_target(target)
        database = configuration.get("datasource", {}).get("db") or {}
        uses_managed_database = (
            configuration.get("datasource", {}).get("type") == "database"
            and database.get("useBuiltin") is True
        )
        # 固定事务目录让进程重启后仍能识别未知的远端注册，避免重新申请。
        key = hashlib.sha256(str(target).encode()).hexdigest()
        # 无模拟库创建不依赖远端注册，独立暂存以免被旧注册锁住，同时保留原注册证据。
        if not uses_managed_database:
            key += "-local"
        staging = Path(tempfile.gettempdir()) / f"devagentstudio-create-{key}"
        request_file = staging / "request.json"
        if request_file.exists():
            pending = json.loads(request_file.read_text(encoding="utf-8"))
            if pending["configuration"] != configuration:
                raise ValueError("该创建请求的数据库注册尚未收口，请保持原配置重试。")
            application_id, thread_id = pending["applicationId"], pending["threadId"]
        else:
            staging.mkdir(mode=0o700, exist_ok=True)
            request_file.write_text(json.dumps({
                "configuration": configuration, "applicationId": application_id,
                "threadId": thread_id,
            }), encoding="utf-8")
        committed = False
        try:
            metadata = staging / WORKSPACE_ARTIFACT_DIR
            metadata.mkdir(exist_ok=True)
            repository = ApplicationConfigRepository(staging)
            if not (metadata / "application.json").exists():
                repository.save(configuration)
            state = ensure_application_lifecycle(
                staging, application_id=application_id,
                application_name=application_name, initialization_thread_id=thread_id,
            )
            if uses_managed_database:
                saved = ensure_managed_database_registration(staging)
            else:
                saved = repository.load()
            # 暂存区可能位于另一磁盘；在目标同级准备完整副本后只提交一次 rename。
            with tempfile.TemporaryDirectory(prefix=".devagentstudio-create-", dir=target.parent) as local:
                prepared = Path(local) / WORKSPACE_ARTIFACT_DIR
                prepared.mkdir()
                for name in ("application.json", "application-lifecycle.json"):
                    (prepared / name).write_bytes((metadata / name).read_bytes())
                _assert_empty_target(target)
                created_target = not target.exists()
                if created_target:
                    target.mkdir()
                try:
                    prepared.rename(target / WORKSPACE_ARTIFACT_DIR)
                except BaseException:
                    if created_target:
                        target.rmdir()
                    raise
            committed = True
            return state, saved
        finally:
            # 成功或明确失败时清理暂存文件；外部注册结果未知/成功则保留供同请求续接。
            current_file = staging / WORKSPACE_ARTIFACT_DIR / "application.json"
            registration = json.loads(current_file.read_text(encoding="utf-8")).get("managedDatabase", {}) if current_file.exists() else {}
            retain = not committed and registration.get("status") in {"REGISTERING", "UNKNOWN", "REGISTERED"}
            if not retain:
                shutil.rmtree(staging, ignore_errors=True)
