"""TechnicalPlan 模板能力与当前 Engine 请求配置之间的确定性映射。"""

from __future__ import annotations

from typing import Any


_SUPPORTED_CAPABILITIES = frozenset({"login", "authorization"})


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

    return {"capabilities": compile_template_capabilities(application_config)}
