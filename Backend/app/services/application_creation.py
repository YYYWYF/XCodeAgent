"""新应用先在隔离暂存区完成初始化，成功后才提交目标工作区。"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.application_lifecycle import ApplicationLifecycle
from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import validate_application_configuration
from app.services.application_config.initial_administrators import validate_new_admins
from app.services.application_lifecycle import ensure_application_lifecycle, load_application_lifecycle
from app.services.backend_instance import current_backend_instance
from app.services.managed_database_registration import ensure_managed_database_registration
from app.services.runtime_temporary import remove_temporary_tree
from app.utils.atomic_json import atomic_write_json

_CREATION_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)
# 注册已返回成功但本地提交失败时保留暂存区，重试复用原身份和绑定。


def _assert_empty_target(target: Path) -> None:
    """只读校验目标是未占用的真实目录，不更改已有项目。"""

    if target.is_symlink():
        raise ValueError("新应用项目目录不能是符号链接。")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("新应用项目目录必须是不存在或为空的独立目录。")
    if not target.parent.is_dir():
        raise ValueError("项目创建位置的上级目录不存在。")


def _cleanup_creation_staging(staging: Path, *, committed: bool, uses_managed_database: bool) -> None:
    """仅清理已提交、明确失败或尚未开始注册的事务。"""
    try:
        if not committed and uses_managed_database:
            application_file = staging / WORKSPACE_ARTIFACT_DIR / "application.json"
            try:
                application = json.loads(application_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                # 注册前必须先持久化配置及 REGISTERING，没有配置时尚未发起注册。
                application = {}
            if not isinstance(application, dict):
                raise ValueError("应用创建暂存配置格式无效。")
            if "managedDatabase" in application:
                registration = ManagedDatabaseRegistration.model_validate(application["managedDatabase"])
                if registration.status in {"REGISTERING", "UNKNOWN", "REGISTERED"}:
                    return
        remove_temporary_tree(staging)
    except (OSError, ValueError) as exc:
        # 无法确认注册事实时保留暂存，且清理异常不得覆盖原业务结果。
        _LOGGER.warning(
            "temporary.cleanup_failed resource=application-creation instanceId=%s errorType=%s",
            current_backend_instance().instance_id, type(exc).__name__,
        )


def initialize_application(
    workspace: str, *, application_id: str, application_name: str,
    configuration: dict[str, Any], thread_id: str,
) -> tuple[ApplicationLifecycle, dict[str, Any]]:
    """隔离应用配置、生命周期和数据库注册，全部就绪才原子提交元数据目录。"""

    validate_application_configuration(configuration)
    configuration = deepcopy(configuration)
    configuration["authorization"]["initialAdministratorSubjects"] = validate_new_admins(configuration["authorization"]["initialAdministratorSubjects"])
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
        if staging.is_symlink() or staging.is_junction():
            raise ValueError("应用创建暂存必须是真实目录。")
        request_file = staging / "request.json"
        try:
            pending = json.loads(request_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pending = None
        else:
            if pending["configuration"] != configuration:
                raise ValueError("该创建请求的数据库注册尚未收口，请保持原配置重试。")
            application_id, thread_id = pending["applicationId"], pending["threadId"]
        committed = False

        try:
            if pending is None:
                staging.mkdir(mode=0o700, exist_ok=True)
                atomic_write_json(request_file, {
                    "configuration": configuration, "applicationId": application_id,
                    "threadId": thread_id,
                })
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
            _cleanup_creation_staging(staging, committed=committed, uses_managed_database=uses_managed_database)
