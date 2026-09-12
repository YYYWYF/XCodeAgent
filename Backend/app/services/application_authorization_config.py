from __future__ import annotations

from typing import Any

from app.services.application_config import ApplicationConfigService
from app.services.application_config.schema import ApplicationConfigError


class ApplicationAuthorizationConfigError(ValueError):
    """表示应用权限配置无法安全更新。"""


def authorization_configuration_can_enable(workspace_root: str | Path) -> bool:
    """读取当前应用数据源，判断是否满足启用内置权限的数据库前提。"""

    try:
        current = ApplicationConfigService(workspace_root).read()
    except ApplicationConfigError:
        return False
    datasource = current.get("datasource") if isinstance(current, dict) else None
    return isinstance(datasource, dict) and datasource.get("type") == "database"


def authorization_configuration_is_enabled(workspace_root: str | Path) -> bool:
    """读取当前应用的权限配置状态，供自然语言需求与运行时配置对齐。"""

    try:
        current = ApplicationConfigService(workspace_root).read()
    except ApplicationConfigError:
        return False
    authorization = current.get("authorization") if isinstance(current, dict) else None
    return isinstance(authorization, dict) and authorization.get("enabled") is True


def persist_authorization_configuration(
    workspace_root: str | Path,
    *,
    initial_administrator_subjects: list[str],
) -> dict[str, Any]:
    """委托通用 Mutation Service 原子启用认证、权限与初始管理员配置。"""

    try:
        service = ApplicationConfigService(workspace_root)
        service.read()
    except ApplicationConfigError as exc:
        raise ApplicationAuthorizationConfigError(str(exc)) from exc
    try:
        return service.apply(
            changes=service.changes_for_targets(
                {"authorization.enabled": True},
                reason="权限初始化需要启用认证与权限管理",
                evidence="authorization initialization",
            ),
            initial_administrator_subjects=initial_administrator_subjects,
        )
    except ApplicationConfigError as exc:
        raise ApplicationAuthorizationConfigError(str(exc)) from exc
