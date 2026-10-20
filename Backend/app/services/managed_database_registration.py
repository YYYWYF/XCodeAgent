"""在 application.json 内保存 SaaS 注册绑定及阻止重复创建的在途事实。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.config import Settings
from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.managed_database_client import ManagedDatabaseClient, ManagedDatabaseRegistrationError


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _registration_lock(workspace: str | Path) -> threading.Lock:
    """串行处理同一工作区的注册请求，第二个调用只能复用已持久化结果。"""

    key = str(Path(workspace).expanduser().resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _save_registration(
    repository: ApplicationConfigRepository, registration: dict[str, Any],
) -> dict[str, Any]:
    """重新读取权威配置后原子保存注册事实，保留当前其他配置并增加版本。"""

    current = repository.load()
    current["managedDatabase"] = registration
    current["configRevision"] += 1
    repository.save(current)
    return current


def ensure_managed_database_registration(
    workspace: str | Path, *, settings: Settings | None = None,
    client: ManagedDatabaseClient | None = None,
) -> dict[str, Any]:
    """只为已确认启用模拟数据库的应用注册一次，已成功或结果未知时不再次 POST。"""

    with _registration_lock(workspace):
        repository = ApplicationConfigRepository(workspace)
        application = repository.load()
        datasource = application.get("datasource") or {}
        database = datasource.get("db") or {}
        if datasource.get("type") != "database" or database.get("useBuiltin") is not True:
            raise ManagedDatabaseRegistrationError("当前应用未启用模拟数据库，不执行 SaaS 注册。")
        stored = application.get("managedDatabase")
        if stored is not None:
            registration = ManagedDatabaseRegistration.model_validate(stored)
            if registration.status == "REGISTERED":
                return application
            if registration.status in {"REGISTERING", "UNKNOWN"}:
                raise ManagedDatabaseRegistrationError(
                    "该应用存在进行中或结果未知的注册，请先人工核对，不能再次创建。", uncertain=True
                )
        resolved_client = client or ManagedDatabaseClient(settings or Settings.from_env())
        # 在外部副作用之前落盘；中断后即使没有拿到 datasourceKey，也不会自动再次申请。
        _save_registration(repository, {"status": "REGISTERING"})
        try:
            # 应用名称只作关联展示，不生成 appKey；当前 Studio 仅提交权威配置中的 appName。
            binding = resolved_client.register(application.get("appName"))
        except ManagedDatabaseRegistrationError as exc:
            _save_registration(repository, {"status": "UNKNOWN" if exc.uncertain else "FAILED"})
            raise
        try:
            return _save_registration(repository, binding)
        except Exception:
            # REGISTERING 已经落盘，保存成功结果失败时仍阻止重新注册；身份可供人工核对。
            raise ManagedDatabaseRegistrationError(
                f"SaaS 注册成功但本地保存失败，datasourceKey={binding['datasourceKey']}，"
                f"datasourceId={binding['datasourceId']}；请先人工恢复绑定，不要重新创建。",
                uncertain=True,
            ) from None
