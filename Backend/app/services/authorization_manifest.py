"""编译并校验 TechnicalPlan 的 V1 RBAC 权限资源目录。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


AUTHORIZATION_MANIFEST_SCHEMA_VERSION = "authorization-manifest.v2"
SYSTEM_RESOURCE_KEY = "system_authorization_management"
_LOWER_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_FORBIDDEN_FIELDS = {"dataRules", "dataPolicyBindings", "dataRuleKey", "policyKey", "requiredSubjectAttributes", "authorization_data_bindings"}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信数组中仅保留对象项。"""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """归一化字符串列表并去重排序。"""
    return sorted({str(item).strip() for item in value if str(item).strip()}) if isinstance(value, list) else []


def _canonical_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """按当前契约排序并去重 manifest，供指纹和严格比较共用。"""
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    authorization = manifest.get("defaultRoleAuthorization") if isinstance(manifest.get("defaultRoleAuthorization"), dict) else {}
    return {"schema_version": manifest.get("schema_version"), "enabled": manifest.get("enabled") is True,
            "resources": sorted(_dict_items(manifest.get("resources")), key=lambda item: str(item.get("resourceKey") or "")),
            "bindings": {"pages": sorted(_dict_items(bindings.get("pages")), key=lambda item: str(item.get("pageId") or "")),
                         "actions": sorted(_dict_items(bindings.get("actions")), key=lambda item: (str(item.get("pageId") or ""), str(item.get("actionId") or ""))),
                         "endpoints": sorted(_dict_items(bindings.get("endpoints")), key=lambda item: str(item.get("endpointId") or ""))},
            "defaultRoleAuthorization": {"roles": sorted(_dict_items(authorization.get("roles")), key=lambda item: str(item.get("roleSeedKey") or "")),
                                          "roleResourceGrants": sorted(_dict_items(authorization.get("roleResourceGrants")), key=lambda item: str(item.get("roleSeedKey") or "")),
                                          "initialAdminRoleSeedKey": str(authorization.get("initialAdminRoleSeedKey") or "")}}


def _with_fingerprint(manifest: dict[str, Any]) -> dict[str, Any]:
    """为规范化 manifest 计算稳定 SHA-256 指纹。"""
    canonical = _canonical_manifest(manifest)
    for endpoint in canonical["bindings"]["endpoints"]:
        endpoint["operationResourceKeys"] = _text_items(endpoint.get("operationResourceKeys"))
    for grant in canonical["defaultRoleAuthorization"]["roleResourceGrants"]:
        grant["resourceKeys"] = _text_items(grant.get("resourceKeys"))
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**canonical, "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def _action_endpoint_ids(pages: list[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """按页面和顶层 action 收集直接或 sequence 业务步骤的 Endpoint。"""
    result: dict[tuple[str, str], set[str]] = {}
    for page in pages:
        page_id = str(page.get("pageId") or "").strip()
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        for item in _dict_items(references.get("action_implementations")):
            action_id = str(item.get("actionId") or "").strip()
            if not page_id or not action_id:
                continue
            endpoints = result.setdefault((page_id, action_id), set())
            for endpoint_id in [item.get("endpointId"), *[step.get("endpointId") for step in _dict_items(item.get("stepBindings"))]]:
                if str(endpoint_id or "").strip():
                    endpoints.add(str(endpoint_id).strip())
    return {key: sorted(value) for key, value in result.items()}


def _authorization_errors(requirement_spec: dict[str, Any]) -> list[str]:
    """拒绝尚未通过上游确认门禁的数据权限和非法角色事实。"""
    authorization = requirement_spec.get("authorization_requirements") if isinstance(requirement_spec.get("authorization_requirements"), dict) else {}
    errors: list[str] = []
    if any(key in authorization for key in _FORBIDDEN_FIELDS):
        errors.append("DATA_AUTHORIZATION_NOT_SUPPORTED：RequirementSpec 不得包含数据权限字段。")
    issues = _dict_items(requirement_spec.get("authorization_capability_issues"))
    if any(str(item.get("code") or "") == "DATA_AUTHORIZATION_NOT_SUPPORTED" for item in issues):
        errors.append("DATA_AUTHORIZATION_NOT_SUPPORTED：存在未解决的数据权限能力问题。")
    if authorization.get("enabled") is True:
        roles = _dict_items(requirement_spec.get("user_roles")); role_ids = {str(item.get("id") or "").strip() for item in roles}
        initial = str(authorization.get("initialAdminRoleId") or "").strip(); initial_roles = [item for item in roles if item.get("isInitialAdminRole") is True]
        if not initial or initial not in role_ids or len(initial_roles) != 1:
            errors.append("权限启用时必须存在唯一有效的初始系统管理员角色。")
        for rule in _dict_items(authorization.get("restrictedPages")) + _dict_items(authorization.get("restrictedOperations")):
            if not _text_items(rule.get("sourceRefs")) or not _text_items(rule.get("defaultGrantedRoleIds")):
                errors.append("权限规则必须包含来源和非空 defaultGrantedRoleIds。")
            if set(_text_items(rule.get("defaultGrantedRoleIds"))) - role_ids:
                errors.append("权限规则 defaultGrantedRoleIds 引用了未知角色。")
    return errors


def compile_authorization_manifest(requirement_spec: dict[str, Any], product_plan: dict[str, Any], api_contracts: list[dict[str, Any]], pages: list[dict[str, Any]]) -> dict[str, Any]:
    """从已确认页面/操作规则确定性编译 V1 manifest。"""
    errors = _authorization_errors(requirement_spec)
    if errors:
        raise ValueError("；".join(errors))
    authorization = requirement_spec.get("authorization_requirements") if isinstance(requirement_spec.get("authorization_requirements"), dict) else {}
    if authorization.get("enabled") is not True:
        return _with_fingerprint({"schema_version": AUTHORIZATION_MANIFEST_SCHEMA_VERSION, "enabled": False, "resources": [], "bindings": {"pages": [], "actions": [], "endpoints": []}, "defaultRoleAuthorization": {"roles": [], "roleResourceGrants": [], "initialAdminRoleSeedKey": ""}})
    targets = product_plan.get("authorizationTargets") if isinstance(product_plan.get("authorizationTargets"), dict) else {}
    page_targets = {str(item.get("ruleId") or "").strip(): str(item.get("pageId") or "").strip() for item in _dict_items(targets.get("pageRules"))}
    action_targets = {
        str(item.get("ruleId") or "").strip(): (
            str(item.get("pageId") or "").strip(),
            str(item.get("actionId") or "").strip(),
            str(item.get("mode") or "hidden").strip() or "hidden",
        )
        for item in _dict_items(targets.get("operationRules"))
    }
    resources: dict[str, dict[str, Any]] = {SYSTEM_RESOURCE_KEY: {"resourceKey": SYSTEM_RESOURCE_KEY, "origin": "system", "type": "system", "name": "权限管理", "description": "管理角色、成员角色与角色资源关系。", "sourceRuleIds": [], "targetResourceRef": "system:authorization_management"}}
    page_bindings: dict[str, dict[str, str]] = {}; action_bindings: dict[tuple[str, str], dict[str, str]] = {}; grants: dict[str, set[str]] = {}
    for rule in _dict_items(authorization.get("restrictedPages")):
        rule_id = str(rule.get("ruleId") or "").strip(); page_id = page_targets.get(rule_id, "")
        if not rule_id or not page_id: raise ValueError("权限 manifest 的页面规则缺少已确认 pageId 目标。")
        resource = resources.setdefault(page_id, {"resourceKey": page_id, "origin": "business", "type": "page", "name": str(rule.get("name") or page_id), "description": str(rule.get("description") or ""), "sourceRuleIds": [], "targetResourceRef": f"page:{page_id}"})
        resource["sourceRuleIds"] = _text_items([*resource["sourceRuleIds"], rule_id]); page_bindings[page_id] = {"pageId": page_id, "resourceKey": page_id}
        for role_id in _text_items(rule.get("defaultGrantedRoleIds")): grants.setdefault(role_id, set()).add(page_id)
    for rule in _dict_items(authorization.get("restrictedOperations")):
        rule_id = str(rule.get("ruleId") or "").strip(); page_id, action_id, mode = action_targets.get(rule_id, ("", "", "hidden"))
        if not rule_id or not page_id or not action_id: raise ValueError("权限 manifest 的操作规则缺少已确认 pageId/actionId 目标。")
        if mode not in {"hidden", "disabled"}: raise ValueError("权限 manifest 的操作规则 mode 必须是 hidden 或 disabled。")
        resource_key = f"{page_id}_{action_id}"
        existing = resources.get(resource_key)
        if existing is not None and (
            existing.get("type") != "operation"
            or existing.get("targetResourceRef") != f"action:{page_id}:{action_id}"
        ):
            raise ValueError(f"权限资源键跨类型或跨目标碰撞：{resource_key}。")
        resource = resources.setdefault(resource_key, {"resourceKey": resource_key, "origin": "business", "type": "operation", "name": str(rule.get("name") or action_id), "description": str(rule.get("description") or ""), "sourceRuleIds": [], "targetResourceRef": f"action:{page_id}:{action_id}"})
        resource["sourceRuleIds"] = _text_items([*resource["sourceRuleIds"], rule_id]); action_bindings[(page_id, action_id)] = {"pageId": page_id, "actionId": action_id, "resourceKey": resource_key, "mode": mode}
        for role_id in _text_items(rule.get("defaultGrantedRoleIds")): grants.setdefault(role_id, set()).add(resource_key)
    if any(not _LOWER_SNAKE_CASE.match(key) for key in resources): raise ValueError("权限资源键必须全局唯一且使用 lower_snake_case。")
    endpoint_resources: dict[str, set[str]] = {}; endpoint_control: dict[str, set[bool]] = {}
    for target, endpoint_ids in _action_endpoint_ids(pages).items():
        resource_key = action_bindings.get(target, {}).get("resourceKey")
        for endpoint_id in endpoint_ids:
            endpoint_control.setdefault(endpoint_id, set()).add(bool(resource_key))
            if resource_key: endpoint_resources.setdefault(endpoint_id, set()).add(resource_key)
    mixed = sorted(endpoint_id for endpoint_id, values in endpoint_control.items() if len(values) > 1)
    if mixed: raise ValueError("ENDPOINT_AUTHORIZATION_MIXED_CONTROL：Endpoint 同时被受控与未受控操作引用：" + "、".join(mixed))
    endpoint_bindings = [{"endpointId": endpoint_id, "operationResourceKeys": sorted(endpoint_resources.get(endpoint_id, set()))} for endpoint_id in sorted(endpoint_control)]
    roles = _dict_items(requirement_spec.get("user_roles")); initial = str(authorization.get("initialAdminRoleId") or "").strip(); grants.setdefault(initial, set()).add(SYSTEM_RESOURCE_KEY)
    return _with_fingerprint({"schema_version": AUTHORIZATION_MANIFEST_SCHEMA_VERSION, "enabled": True, "resources": list(resources.values()), "bindings": {"pages": list(page_bindings.values()), "actions": list(action_bindings.values()), "endpoints": endpoint_bindings}, "defaultRoleAuthorization": {"roles": [{"roleSeedKey": str(role.get("id") or ""), "name": str(role.get("name") or ""), "description": str(role.get("description") or ""), "isSystemRole": role.get("isSystemRole") is True, "isInitialAdminRole": role.get("isInitialAdminRole") is True} for role in roles], "roleResourceGrants": [{"roleSeedKey": role_id, "resourceKeys": sorted(keys)} for role_id, keys in grants.items()], "initialAdminRoleSeedKey": initial}})


def validate_authorization_manifest(manifest: Any, requirement_spec: dict[str, Any], product_plan: dict[str, Any], api_contracts: list[dict[str, Any]], pages: list[dict[str, Any]]) -> list[str]:
    """通过重新编译做严格比较，拒绝手写、旧版或漂移 manifest。"""
    if not isinstance(manifest, dict): return ["TechnicalPlan.authorization_manifest 必须是 JSON 对象。"]
    if any(key in json.dumps(manifest, ensure_ascii=False) for key in _FORBIDDEN_FIELDS): return ["DATA_AUTHORIZATION_NOT_SUPPORTED：TechnicalPlan manifest 不得包含数据权限字段。"]
    try: expected = compile_authorization_manifest(requirement_spec, product_plan, api_contracts, pages)
    except ValueError as exc: return [str(exc)]
    errors: list[str] = []
    if manifest.get("schema_version") != AUTHORIZATION_MANIFEST_SCHEMA_VERSION: errors.append("TechnicalPlan.authorization_manifest.schema_version 必须为 authorization-manifest.v2。")
    if _canonical_manifest(manifest) != _canonical_manifest(expected): errors.append("TechnicalPlan.authorization_manifest 必须由已确认规则、产品目标和技术绑定确定性编译。")
    if manifest.get("fingerprint") != expected.get("fingerprint"): errors.append("TechnicalPlan.authorization_manifest.fingerprint 与当前内容不一致。")
    return errors
