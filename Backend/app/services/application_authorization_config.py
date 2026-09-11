from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.application_config_change import ApplicationConfigChange
from app.services.application_config_mutation import (
    ApplicationConfigMutationError,
    apply_application_config_changes,
)


class ApplicationAuthorizationConfigError(ValueError):
    """表示应用权限配置无法安全更新。"""


def authorization_configuration_can_enable(workspace_root: str | Path) -> bool:
    """读取当前应用数据源，判断是否满足启用内置权限的数据库前提。"""

    target = Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    datasource = current.get("datasource") if isinstance(current, dict) else None
    return isinstance(datasource, dict) and datasource.get("type") == "database"


def authorization_configuration_is_enabled(workspace_root: str | Path) -> bool:
    """读取当前应用的权限配置状态，供自然语言需求与运行时配置对齐。"""

    target = Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    authorization = current.get("authorization") if isinstance(current, dict) else None
    return isinstance(authorization, dict) and authorization.get("enabled") is True


def persist_authorization_configuration(
    workspace_root: str | Path,
    *,
    initial_administrator_subjects: list[str],
) -> dict[str, Any]:
    """委托通用 Mutation Service 原子启用认证、权限与初始管理员配置。"""

    target = Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    if not target.is_file():
        raise ApplicationAuthorizationConfigError("当前工作区缺少 .xcodeagent/application.json。")
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationAuthorizationConfigError("当前工作区 application.json 无法读取或格式无效。") from exc
    if not isinstance(current, dict) or current.get("schemaVersion") != 5:
        raise ApplicationAuthorizationConfigError("仅支持当前 schemaVersion 5 的 application.json。")
    auth = current.get("auth")
    authorization = current.get("authorization")
    if not isinstance(auth, dict) or not isinstance(authorization, dict):
        raise ApplicationAuthorizationConfigError("application.json 缺少有效的认证或权限配置。")
    if set(authorization) != {"enabled", "initialAdministratorSubjects"}:
        raise ApplicationAuthorizationConfigError(
            "application.json authorization 必须只包含 enabled 和 initialAdministratorSubjects。"
        )

    changes: list[ApplicationConfigChange] = []
    for path, enabled in (("auth.enable", auth.get("enable")), ("authorization.enabled", authorization.get("enabled"))):
        if type(enabled) is not bool:
            raise ApplicationAuthorizationConfigError(f"application.json 的 {path} 必须是布尔值。")
        if not enabled:
            changes.append(ApplicationConfigChange(path=path, operation="set", **{"from": enabled, "to": True}, reason="权限初始化需要启用认证与权限管理", evidence="authorization initialization"))
    try:
        return apply_application_config_changes(
            workspace_root,
            changes=changes,
            initial_administrator_subjects=initial_administrator_subjects,
        )
    except ApplicationConfigMutationError as exc:
        raise ApplicationAuthorizationConfigError(str(exc)) from exc
