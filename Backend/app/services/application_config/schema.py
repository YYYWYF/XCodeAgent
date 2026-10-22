"""application.json 当前 Schema 与能力依赖校验。"""

from __future__ import annotations

from typing import Any
from copy import deepcopy
from urllib.parse import urlparse

from pydantic import ValidationError

from app.domain.managed_database import ManagedDatabaseRegistration


CURRENT_APPLICATION_SCHEMA_VERSION = 6

# 模板 1016 的固定默认值；读取旧 v6 快照时只补本期可选地址字段。
LOGIN_AUTHORIZATION_DEFAULTS = {
    "centerUri": "http://one-account-gateway.paasuat.cmbchina.cn/auth-server",
    "authorizeUri": "https://one-account-gateway.paasuat.cmbchina.cn/auth-server/auth",
    "redirectUri": "http://localhost",
    "endUrl": "https://one-account-gateway.paasuat.cmbchina.cn/auth-server/endsession",
    "logoutShareJs": "https://one-account-gateway.paasuat.cmbchina.cn/logout-share/js/logoutShare.js",
    "baseUrl": "http://one-account-gateway.paasuat.cmbchina.cn/api-server",
    "viewId": "2c9f50c875c0dfce0175de6a4df1057e",
}


class ApplicationConfigError(ValueError):
    """表示应用配置不符合当前唯一事实源契约。"""


def normalize_application_configuration(application: Any) -> dict[str, Any]:
    """为旧 v6 快照补全本期地址默认值，不改变旧凭据或能力开关。"""

    if not isinstance(application, dict):
        return application
    result = deepcopy(application)
    auth = result.get("auth")
    if isinstance(auth, dict):
        yht = auth.setdefault("yht", {})
        if isinstance(yht, dict):
            for field in ("clientId", "privateKey", "publicKey", "centerPublicKey"):
                yht.setdefault(field, "")
            for field in ("centerUri", "authorizeUri", "redirectUri", "endUrl", "logoutShareJs"):
                yht.setdefault(field, LOGIN_AUTHORIZATION_DEFAULTS[field])
    authorization = result.get("authorization")
    if isinstance(authorization, dict) and authorization.get("enabled") is True:
        api = authorization.setdefault("api", {})
        if isinstance(api, dict):
            api.setdefault("baseUrl", LOGIN_AUTHORIZATION_DEFAULTS["baseUrl"])
            api.setdefault("viewId", LOGIN_AUTHORIZATION_DEFAULTS["viewId"])
    return result


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
    registration = application.get("managedDatabase")
    if registration is not None:
        try:
            ManagedDatabaseRegistration.model_validate(registration)
        except ValidationError:
            raise ApplicationConfigError("application.json 的 managedDatabase 注册绑定无效。") from None
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
    auth = application.get("auth")
    yht = auth.get("yht") if isinstance(auth, dict) else None
    if not isinstance(yht, dict):
        raise ApplicationConfigError("application.json.auth.yht 必须是对象。")
    required_yht = ("clientId", "privateKey", "publicKey", "centerPublicKey")
    url_yht = ("centerUri", "authorizeUri", "redirectUri", "endUrl", "logoutShareJs")
    for field in required_yht:
        value = yht.get(field)
        if not isinstance(value, str) or (values["auth.enable"] and not value.strip()):
            raise ApplicationConfigError(f"application.json.auth.yht.{field} 无效。")
    for field in url_yht:
        if not isinstance(yht.get(field), str) or (yht[field] and not _is_http_url(yht[field])):
            raise ApplicationConfigError(f"application.json.auth.yht.{field} 必须是 http 或 https URL。")
    authorization = application.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) not in ({"enabled", "initialAdministratorSubjects"}, {"enabled", "initialAdministratorSubjects", "api"}):
        raise ApplicationConfigError("application.json authorization 只能包含 enabled、initialAdministratorSubjects 和可选 api。")
    api = authorization.get("api")
    if api is not None:
        if not isinstance(api, dict) or set(api) != {"baseUrl", "viewId"} or not isinstance(api.get("baseUrl"), str) or (api["baseUrl"] and not _is_http_url(api["baseUrl"])) or not isinstance(api.get("viewId"), str):
            raise ApplicationConfigError("application.json authorization.api 无效。")
    if values["authorization.enabled"] and api is None:
        raise ApplicationConfigError("启用权限控制时必须填写 authorization.api。")
    return application


def _is_http_url(value: Any) -> bool:
    """允许模板服务使用的公网或内网 http/https URL。"""

    if not isinstance(value, str) or not value.strip() or any(char.isspace() for char in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
