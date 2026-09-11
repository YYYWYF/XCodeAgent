"""TechnicalPlan 模板能力与当前 Engine 请求配置之间的确定性映射。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_SUPPORTED_CAPABILITIES = frozenset({"login", "authorization"})


class TemplateCapabilityError(ValueError):
    """表示 TechnicalPlan 的模板能力不符合当前 Engine 支持范围。"""


def compile_template_capabilities(
    application_config: Any,
    authorization_manifest: Any,
) -> dict[str, dict[str, Any]]:
    """从已提交 application.json 的能力开关确定性编译模板 Desired Capability。"""

    if not isinstance(authorization_manifest, dict):
        raise TemplateCapabilityError("TechnicalPlan.authorization_manifest 必须是对象。")
    if not isinstance(application_config, dict):
        raise TemplateCapabilityError("application.json 必须是对象。")
    manifest_enabled = authorization_manifest.get("enabled")
    if not isinstance(manifest_enabled, bool):
        raise TemplateCapabilityError("TechnicalPlan.authorization_manifest.enabled 必须是布尔值。")
    auth = application_config.get("auth")
    authorization = application_config.get("authorization")
    if not isinstance(auth, dict) or type(auth.get("enable")) is not bool:
        raise TemplateCapabilityError("application.json.auth.enable 必须是布尔值。")
    if not isinstance(authorization, dict) or type(authorization.get("enabled")) is not bool:
        raise TemplateCapabilityError("application.json.authorization.enabled 必须是布尔值。")
    if manifest_enabled != (authorization.get("enabled") is True):
        raise TemplateCapabilityError("权限 Manifest 必须与 application.json.authorization.enabled 一致。")
    capabilities: dict[str, dict[str, Any]] = {}
    if auth.get("enable") is True:
        capabilities["login"] = {"enabled": True, "config": {}}
    if authorization.get("enabled") is True:
        capabilities["authorization"] = {"enabled": True, "config": {}}
    return capabilities


def initial_template_capabilities(authorization_manifest: Any) -> dict[str, dict[str, Any]]:
    """兼容旧调用：仅按权限 Manifest 构造不含登录的首次能力集合。"""

    return compile_template_capabilities(
        {"auth": {"enable": False}, "authorization": {"enabled": isinstance(authorization_manifest, dict) and authorization_manifest.get("enabled") is True}},
        authorization_manifest,
    )


def normalize_template_capabilities(value: Any) -> dict[str, dict[str, Any]]:
    """校验并复制当前 Engine 支持的显式启用模板能力。"""

    if not isinstance(value, dict):
        raise TemplateCapabilityError("TechnicalPlan.template_capabilities 必须是对象。")
    normalized: dict[str, dict[str, Any]] = {}
    for capability_id in sorted(value):
        capability = value[capability_id]
        if capability_id not in _SUPPORTED_CAPABILITIES:
            raise TemplateCapabilityError(
                f"当前 Template Engine 不支持能力：{capability_id}。"
            )
        if not isinstance(capability, dict) or set(capability) != {"enabled", "config"}:
            raise TemplateCapabilityError(
                f"TechnicalPlan.template_capabilities.{capability_id} 必须只包含 enabled 和 config。"
            )
        if capability.get("enabled") is not True:
            raise TemplateCapabilityError(
                f"TechnicalPlan.template_capabilities.{capability_id}.enabled 必须为 true；停用能力请删除该项。"
            )
        if capability.get("config") != {}:
            raise TemplateCapabilityError(
                f"当前 Template Engine 不支持 {capability_id} 的非空 config。"
            )
        normalized[capability_id] = {"enabled": True, "config": {}}
    return normalized


def template_capability_errors(plan: dict[str, Any]) -> list[str]:
    """返回 TechnicalPlan 能力结构及权限 Manifest 一致性的全部错误。"""

    try:
        capabilities = normalize_template_capabilities(plan.get("template_capabilities"))
    except TemplateCapabilityError as exc:
        return [str(exc)]
    manifest = plan.get("authorization_manifest")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("enabled"), bool):
        return ["TechnicalPlan.authorization_manifest.enabled 必须是布尔值。"]
    if manifest["enabled"] != ("authorization" in capabilities):
        return [
            "TechnicalPlan.authorization_manifest.enabled 必须与 "
            "template_capabilities.authorization 是否启用一致。"
        ]
    return []


def requested_config_from_technical_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """从已校验 TechnicalPlan 直接编译当前 Engine RequestedConfig。"""

    errors = template_capability_errors(plan)
    if errors:
        raise TemplateCapabilityError("；".join(errors))
    return {
        "capabilities": deepcopy(
            normalize_template_capabilities(plan["template_capabilities"])
        )
    }
