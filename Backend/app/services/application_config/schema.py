"""application.json 当前 Schema 与能力依赖校验。"""

from __future__ import annotations

from typing import Any


CURRENT_APPLICATION_SCHEMA_VERSION = 6


class ApplicationConfigError(ValueError):
    """表示应用配置不符合当前唯一事实源契约。"""


def validate_application_snapshot(application: Any) -> dict[str, Any]:
    """验证可读取快照的当前版本和可变能力字段类型。"""

    if (
        not isinstance(application, dict)
        or type(application.get("schemaVersion")) is not int
        or application.get("schemaVersion") != CURRENT_APPLICATION_SCHEMA_VERSION
    ):
        raise ApplicationConfigError(
            f"仅支持当前 schemaVersion {CURRENT_APPLICATION_SCHEMA_VERSION} 的 application.json。"
        )
    if type(application.get("configRevision")) is not int or application["configRevision"] < 1:
        raise ApplicationConfigError("application.json 的 configRevision 必须是大于零的整数。")
    values: dict[str, bool] = {}
    for path in ("auth.enable", "authorization.enabled"):
        section, field = path.split(".")
        value = application.get(section, {}).get(field) if isinstance(application.get(section), dict) else None
        if type(value) is not bool:
            raise ApplicationConfigError(f"application.json 的 {path} 必须是布尔值。")
        values[path] = value
    return application


def validate_application_configuration(application: Any) -> dict[str, Any]:
    """验证提交前完整配置及能力依赖，并返回已验证的对象。"""

    application = validate_application_snapshot(application)
    values = {
        "auth.enable": application["auth"]["enable"],
        "authorization.enabled": application["authorization"]["enabled"],
    }
    if values["authorization.enabled"] and not values["auth.enable"]:
        raise ApplicationConfigError("启用权限管理时必须启用登录认证。")
    if values["authorization.enabled"]:
        datasource = application.get("datasource")
        if not isinstance(datasource, dict) or datasource.get("type") != "database":
            raise ApplicationConfigError("启用权限控制时必须使用数据库数据源。")
        authorization = application.get("authorization")
        subjects = authorization.get("initialAdministratorSubjects") if isinstance(authorization, dict) else None
        if not isinstance(subjects, list) or not any(
            isinstance(item, str) and item.strip() and item != "current-user" for item in subjects
        ):
            raise ApplicationConfigError("启用权限控制时至少需要一个真实初始管理员 subjectId。")
    authorization = application.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "enabled",
        "initialAdministratorSubjects",
    }:
        raise ApplicationConfigError("application.json authorization 必须只包含 enabled 和 initialAdministratorSubjects。")
    return application
