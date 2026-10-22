"""TechnicalPlan 模板能力与当前 Engine 请求配置之间的确定性映射。"""

from __future__ import annotations

import re
from typing import Any


_SUPPORTED_CAPABILITIES = frozenset({"login", "authorization"})
_PRIMARY_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_YHT_AUTHN_SOURCE = "yst"
_YHT_CONFIG_FIELDS = (
    "clientId", "privateKey", "publicKey", "centerPublicKey", "centerUri", "authorizeUri",
    "redirectUri", "endUrl", "logoutShareJs",
)


class TemplateCapabilityError(ValueError):
    """表示 TechnicalPlan 的模板能力不符合当前 Engine 支持范围。"""


def compile_template_capabilities(application_config: Any) -> dict[str, dict[str, Any]]:
    """从已提交 application.json 的能力开关确定性编译模板 Desired Capability。"""

    if not isinstance(application_config, dict):
        raise TemplateCapabilityError("application.json 必须是对象。")
    auth = application_config.get("auth")
    authorization = application_config.get("authorization")
    if not isinstance(auth, dict) or type(auth.get("enable")) is not bool:
        raise TemplateCapabilityError("application.json.auth.enable 必须是布尔值。")
    if not isinstance(authorization, dict) or type(authorization.get("enabled")) is not bool:
        raise TemplateCapabilityError("application.json.authorization.enabled 必须是布尔值。")
    capabilities: dict[str, dict[str, Any]] = {}
    if auth.get("enable") is True:
        capabilities["login"] = {"enabled": True, "config": {}}
    if authorization.get("enabled") is True:
        capabilities["authorization"] = {"enabled": True, "config": {}}
    return capabilities


def requested_config_from_application_config(application_config: Any) -> dict[str, Any]:
    """只从 canonical application.json 编译 Template Engine RequestedConfig。"""

    return {
        "capabilities": compile_template_capabilities(application_config),
        "applicationConfig": compile_template_application_config(application_config),
    }


def compile_template_application_config(application_config: Any) -> dict[str, Any]:
    """编译首次 Generate 所需的基础信息和一号通登录配置。"""

    if not isinstance(application_config, dict):
        raise TemplateCapabilityError("application.json 必须是对象。")
    app_name = application_config.get("appName")
    app_icon = application_config.get("appIcon")
    theme = application_config.get("theme")
    primary_color = theme.get("primaryColor") if isinstance(theme, dict) else None
    if not isinstance(app_name, str) or not app_name.strip() or len(app_name) > 128:
        raise TemplateCapabilityError("application.json.appName 必须是长度不超过 128 的非空字符串。")
    if not isinstance(app_icon, str) or not app_icon.strip() or len(app_icon) > 128:
        raise TemplateCapabilityError("application.json.appIcon 必须是长度不超过 128 的非空字符串。")
    if not isinstance(primary_color, str) or _PRIMARY_COLOR_PATTERN.fullmatch(primary_color) is None:
        raise TemplateCapabilityError("application.json.theme.primaryColor 必须是 #RRGGBB 格式。")
    result = {
        "appName": app_name,
        "appIcon": app_icon,
        "theme": {"primaryColor": primary_color},
        "auth": _compile_template_auth_config(application_config.get("auth")),
    }
    authorization = application_config.get("authorization")
    if isinstance(authorization, dict) and isinstance(authorization.get("api"), dict):
        api = authorization["api"]
        if isinstance(api.get("baseUrl"), str) and isinstance(api.get("viewId"), str):
            # Generate 只接收工程可投影的 API 值，不泄露开关或管理员种子。
            result["authorization"] = {"api": {"baseUrl": api["baseUrl"], "viewId": api["viewId"]}}
    return result


def _compile_template_auth_config(value: Any) -> dict[str, Any]:
    """将 application.json 的一号通字段映射为模板引擎固定的认证请求形状。"""

    if not isinstance(value, dict) or type(value.get("enable")) is not bool:
        raise TemplateCapabilityError("application.json.auth.enable 必须是布尔值。")
    yht = value.get("yht")
    if not isinstance(yht, dict):
        yht = {}
    config: dict[str, str] = {}
    for field in _YHT_CONFIG_FIELDS:
        raw_value = yht.get(field, "")
        if not isinstance(raw_value, str):
            raise TemplateCapabilityError(f"application.json.auth.yht.{field} 必须是字符串。")
        if value["enable"] is True and field in {"clientId", "privateKey", "publicKey", "centerPublicKey"} and not raw_value.strip():
            raise TemplateCapabilityError(f"启用一号通登录时必须填写 application.json.auth.yht.{field}。")
        config[field] = raw_value
    return {
        "enable": value["enable"],
        # 认证来源不再由用户输入；Template Engine 仍要求固定来源标识。
        "authnSource": _YHT_AUTHN_SOURCE,
        "yht": config,
    }
