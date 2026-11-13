"""编译并校验 TechnicalPlan 的 V1 RBAC 权限资源目录。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.services.requirement_spec import validate_authorization_requirements
from app.services.application_config.initial_administrators import parse_admins_for_initialization


AUTHORIZATION_MANIFEST_SCHEMA_VERSION = "authorization-manifest.v3"
SYSTEM_ADMIN_ROLE_SEED_KEY = "system_admin"
SYSTEM_RESOURCE_KEY = "system_authorization_management"
RESOURCE_KEY_MAX_LENGTH = 64
_LOWER_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_FORBIDDEN_FIELDS = {"dataRules", "dataPolicyBindings", "dataRuleKey", "policyKey", "requiredSubjectAttributes", "authorization_data_bindings"}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信数组中仅保留对象项。"""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """归一化字符串列表并去重排序。"""
    return sorted({str(item).strip() for item in value if str(item).strip()}) if isinstance(value, list) else []


def authorization_resource_key(page_id: str, *, action_id: str | None = None) -> str:
    """按现有规则派生资源键，并在预览、规划和正式编译中复用同一键长约束。"""

    target = f"页面 {page_id}" if action_id is None else f"操作 {page_id}/{action_id}"
    key = page_id if action_id is None else f"{page_id}_{action_id}"
    if len(key) > RESOURCE_KEY_MAX_LENGTH:
        hint = "请缩短该页面的操作 ID，可省去重复的页面前缀。" if action_id is not None else "请缩短该页面 ID。"
        raise ValueError(f"{target} 的权限资源键 {key} 长度为 {len(key)}，上限为 {RESOURCE_KEY_MAX_LENGTH}。{hint}")
    if not _LOWER_SNAKE_CASE.fullmatch(key):
        raise ValueError(f"{target} 的权限资源键 {key} 必须使用 lower_snake_case。")
    return key


def _canonical_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """按当前契约排序并去重 manifest，供指纹和严格比较共用。"""
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    authorization = manifest.get("defaultRoleAuthorization") if isinstance(manifest.get("defaultRoleAuthorization"), dict) else {}
    system_authorization = manifest.get("systemAuthorization") if isinstance(manifest.get("systemAuthorization"), dict) else {}
    return {"schema_version": manifest.get("schema_version"),
            "resources": sorted(_dict_items(manifest.get("resources")), key=lambda item: str(item.get("resourceKey") or "")),
            "bindings": {"pages": sorted(_dict_items(bindings.get("pages")), key=lambda item: str(item.get("pageId") or "")),
                         "actions": sorted(_dict_items(bindings.get("actions")), key=lambda item: (str(item.get("pageId") or ""), str(item.get("actionId") or ""))),
                         "endpoints": sorted(_dict_items(bindings.get("endpoints")), key=lambda item: str(item.get("endpointId") or ""))},
            "systemAuthorization": {
                "adminRoleSeedKey": str(system_authorization.get("adminRoleSeedKey") or ""),
                "managementResourceKey": str(system_authorization.get("managementResourceKey") or ""),
            },
            "defaultRoleAuthorization": {"roles": sorted(_dict_items(authorization.get("roles")), key=lambda item: str(item.get("roleSeedKey") or "")),
                                          "roleResourceGrants": sorted(_dict_items(authorization.get("roleResourceGrants")), key=lambda item: str(item.get("roleSeedKey") or ""))}}


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


def _authorization_errors(requirement_spec: dict[str, Any], *, authorization_enabled: bool) -> list[str]:
    """拒绝尚未通过上游确认门禁的数据权限和非法角色事实。"""
    authorization = requirement_spec.get("authorization_requirements") if isinstance(requirement_spec.get("authorization_requirements"), dict) else {}
    errors: list[str] = []
    if any(key in authorization for key in _FORBIDDEN_FIELDS):
        errors.append("DATA_AUTHORIZATION_NOT_SUPPORTED：RequirementSpec 不得包含数据权限字段。")
    issues = _dict_items(requirement_spec.get("authorization_capability_issues"))
    if any(str(item.get("code") or "") == "DATA_AUTHORIZATION_NOT_SUPPORTED" for item in issues):
        errors.append("DATA_AUTHORIZATION_NOT_SUPPORTED：存在未解决的数据权限能力问题。")
    if authorization_enabled:
        roles = _dict_items(requirement_spec.get("user_roles")); role_ids = {str(item.get("id") or "").strip() for item in roles}
        for rule in _dict_items(authorization.get("restrictedPages")) + _dict_items(authorization.get("restrictedOperations")):
            if not _text_items(rule.get("sourceRefs")) or not _text_items(rule.get("defaultGrantedRoleIds")):
                errors.append("权限规则必须包含来源和非空 defaultGrantedRoleIds。")
            if set(_text_items(rule.get("defaultGrantedRoleIds"))) - role_ids:
                errors.append("权限规则 defaultGrantedRoleIds 引用了未知角色。")
    return errors


def authorization_enabled_from_application_config(application_config: Any) -> bool:
    """只从 application.json 提取权限能力开关，拒绝以业务文档替代平台配置。"""

    authorization = application_config.get("authorization") if isinstance(application_config, dict) else None
    enabled = authorization.get("enabled") if isinstance(authorization, dict) else None
    if type(enabled) is not bool:
        raise ValueError("application.json.authorization.enabled 必须是布尔值。")
    return enabled


def validate_authorization_configuration_projection(
    application_config: Any,
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    *,
    validate_requirement_details: bool = True,
) -> list[str]:
    """校验待确认配置与需求、产品权限事实一致，阻止错误进入 TechnicalPlan。"""

    try:
        enabled = authorization_enabled_from_application_config(application_config)
    except ValueError as exc:
        return [str(exc)]
    errors = (
        validate_authorization_requirements(
            requirement_spec,
            authorization_enabled=enabled,
        )
        if enabled and validate_requirement_details
        else []
    )
    if enabled:
        subjects = application_config.get("authorization", {}).get("initialAdministratorSubjects")
        try:
            if not parse_admins_for_initialization(subjects):
                raise ValueError("初始管理员不能为空。")
        except ValueError:
            errors.append("启用权限时 application.json 必须包含有效且无身份冲突的初始管理员。")
    return errors


def compile_authorization_resources(requirement_spec: dict[str, Any], product_plan: dict[str, Any], *, application_config: dict[str, Any]) -> dict[str, Any]:
    """在需求文档联合确认前编译角色、页面/操作资源及默认授权，不依赖技术接口。"""

    enabled = authorization_enabled_from_application_config(application_config)
    projection_errors = validate_authorization_configuration_projection(
        application_config,
        requirement_spec,
        product_plan,
        validate_requirement_details=False,
    )
    if projection_errors:
        raise ValueError("；".join(projection_errors))
    errors = _authorization_errors(requirement_spec, authorization_enabled=enabled)
    if errors:
        raise ValueError("；".join(errors))
    authorization = requirement_spec.get("authorization_requirements") if isinstance(requirement_spec.get("authorization_requirements"), dict) else {}
    if not enabled:
        return _with_fingerprint({"schema_version": AUTHORIZATION_MANIFEST_SCHEMA_VERSION, "resources": [], "bindings": {"pages": [], "actions": [], "endpoints": []}, "systemAuthorization": {"adminRoleSeedKey": "", "managementResourceKey": ""}, "defaultRoleAuthorization": {"roles": [], "roleResourceGrants": []}})
    targets = product_plan.get("authorizationTargets") if isinstance(product_plan.get("authorizationTargets"), dict) else {}
    page_rules = _dict_items(targets.get("pageRules"))
    action_rules = _dict_items(targets.get("operationRules"))
    page_targets = {str(item.get("ruleId") or "").strip(): str(item.get("pageId") or "").strip() for item in page_rules}
    action_targets = {
        str(item.get("ruleId") or "").strip(): (
            str(item.get("pageId") or "").strip(),
            str(item.get("actionId") or "").strip(),
            str(item.get("mode") or "hidden").strip() or "hidden",
        )
        for item in action_rules
    }
    if len(page_targets) != len(page_rules) or len(action_targets) != len(action_rules):
        raise ValueError("权限 Manifest 的同一规则不能绑定多个目标。")
    product_pages = _dict_items(product_plan.get("pages"))
    page_ids = {str(page.get("pageId") or "").strip() for page in product_pages}
    action_ids = {
        (str(page.get("pageId") or "").strip(), str(action.get("actionId") or "").strip())
        for page in product_pages
        for action in _dict_items(page.get("actions"))
    }
    resources: dict[str, dict[str, Any]] = {SYSTEM_RESOURCE_KEY: {"resourceKey": SYSTEM_RESOURCE_KEY, "origin": "system", "type": "system", "name": "权限管理", "description": "管理角色、成员角色与角色资源关系。", "sourceRuleIds": [], "targetResourceRef": "system:authorization_management"}}
    page_bindings: dict[str, dict[str, str]] = {}; action_bindings: dict[tuple[str, str], dict[str, str]] = {}; grants: dict[str, set[str]] = {}
    for rule in _dict_items(authorization.get("restrictedPages")):
        rule_id = str(rule.get("ruleId") or "").strip(); page_id = page_targets.get(rule_id, "")
        if not rule_id or not page_id: raise ValueError("权限 manifest 的页面规则缺少已确认 pageId 目标。")
        if page_id not in page_ids or page_id != str(rule.get("targetPageId") or "").strip():
            raise ValueError(f"权限页面规则 {rule_id} 的页面引用与已确认目标不一致：{page_id}。")
        resource_key = authorization_resource_key(page_id)
        if resource_key in resources:
            raise ValueError(f"权限资源键重复：{resource_key}（规则 {rule_id}）。")
        resources[resource_key] = {"resourceKey": resource_key, "origin": "business", "type": "page", "name": str(rule.get("name") or page_id), "description": str(rule.get("description") or ""), "sourceRuleIds": [rule_id], "targetResourceRef": f"page:{page_id}"}
        page_bindings[page_id] = {"pageId": page_id, "resourceKey": resource_key}
        for role_id in _text_items(rule.get("defaultGrantedRoleIds")): grants.setdefault(role_id, set()).add(resource_key)
    for rule in _dict_items(authorization.get("restrictedOperations")):
        rule_id = str(rule.get("ruleId") or "").strip(); page_id, action_id, mode = action_targets.get(rule_id, ("", "", "hidden"))
        if not rule_id or not page_id or not action_id: raise ValueError("权限 manifest 的操作规则缺少已确认 pageId/actionId 目标。")
        if mode not in {"hidden", "disabled"}: raise ValueError("权限 manifest 的操作规则 mode 必须是 hidden 或 disabled。")
        if (page_id, action_id) not in action_ids:
            raise ValueError(f"权限操作规则 {rule_id} 引用了不存在的页面操作：{page_id}/{action_id}。")
        resource_key = authorization_resource_key(page_id, action_id=action_id)
        if resource_key in resources:
            raise ValueError(f"权限资源键重复：{resource_key}（规则 {rule_id}）。")
        resources[resource_key] = {"resourceKey": resource_key, "origin": "business", "type": "operation", "name": str(rule.get("name") or action_id), "description": str(rule.get("description") or ""), "sourceRuleIds": [rule_id], "targetResourceRef": f"action:{page_id}:{action_id}"}
        action_bindings[(page_id, action_id)] = {"pageId": page_id, "actionId": action_id, "resourceKey": resource_key, "mode": mode}
        for role_id in _text_items(rule.get("defaultGrantedRoleIds")): grants.setdefault(role_id, set()).add(resource_key)
    roles = _dict_items(requirement_spec.get("user_roles"))
    # 系统管理员固定创建；业务角色只得到需求规则明确授予的业务资源。
    return _with_fingerprint({"schema_version": AUTHORIZATION_MANIFEST_SCHEMA_VERSION, "resources": list(resources.values()), "bindings": {"pages": list(page_bindings.values()), "actions": list(action_bindings.values()), "endpoints": []}, "systemAuthorization": {"adminRoleSeedKey": SYSTEM_ADMIN_ROLE_SEED_KEY, "managementResourceKey": SYSTEM_RESOURCE_KEY}, "defaultRoleAuthorization": {"roles": [{"roleSeedKey": str(role.get("id") or ""), "name": str(role.get("name") or ""), "description": str(role.get("description") or "")} for role in roles], "roleResourceGrants": [{"roleSeedKey": role_id, "resourceKeys": sorted(keys)} for role_id, keys in grants.items()]}})


def compile_authorization_manifest(requirement_spec: dict[str, Any], product_plan: dict[str, Any], api_contracts: list[dict[str, Any]], pages: list[dict[str, Any]], *, application_config: dict[str, Any]) -> dict[str, Any]:
    """复用上游业务资源目录，仅追加技术规划操作到 Endpoint 的权限绑定。"""

    manifest = compile_authorization_resources(requirement_spec, product_plan, application_config=application_config)
    if not authorization_enabled_from_application_config(application_config):
        return manifest
    action_bindings = {(item["pageId"], item["actionId"]): item for item in manifest["bindings"]["actions"]}
    endpoint_resources: dict[str, set[str]] = {}; endpoint_control: dict[str, set[bool]] = {}
    for target, endpoint_ids in _action_endpoint_ids(pages).items():
        resource_key = action_bindings.get(target, {}).get("resourceKey")
        for endpoint_id in endpoint_ids:
            endpoint_control.setdefault(endpoint_id, set()).add(bool(resource_key))
            if resource_key: endpoint_resources.setdefault(endpoint_id, set()).add(resource_key)
    mixed = sorted(endpoint_id for endpoint_id, values in endpoint_control.items() if len(values) > 1)
    if mixed: raise ValueError("ENDPOINT_AUTHORIZATION_MIXED_CONTROL：Endpoint 同时被受控与未受控操作引用：" + "、".join(mixed))
    endpoint_bindings = [{"endpointId": endpoint_id, "operationResourceKeys": sorted(endpoint_resources.get(endpoint_id, set()))} for endpoint_id in sorted(endpoint_control)]
    return _with_fingerprint({**manifest, "bindings": {**manifest["bindings"], "endpoints": endpoint_bindings}})


def validate_authorization_manifest(manifest: Any, requirement_spec: dict[str, Any], product_plan: dict[str, Any], api_contracts: list[dict[str, Any]], pages: list[dict[str, Any]], *, application_config: dict[str, Any]) -> list[str]:
    """通过重新编译做严格比较，拒绝手写、旧版或漂移 manifest。"""
    if not isinstance(manifest, dict): return ["TechnicalPlan.authorization_manifest 必须是 JSON 对象。"]
    if any(key in json.dumps(manifest, ensure_ascii=False) for key in _FORBIDDEN_FIELDS): return ["DATA_AUTHORIZATION_NOT_SUPPORTED：TechnicalPlan manifest 不得包含数据权限字段。"]
    try: expected = compile_authorization_manifest(requirement_spec, product_plan, api_contracts, pages, application_config=application_config)
    except ValueError as exc: return [str(exc)]
    errors: list[str] = []
    if manifest.get("schema_version") != AUTHORIZATION_MANIFEST_SCHEMA_VERSION: errors.append("TechnicalPlan.authorization_manifest.schema_version 必须为 authorization-manifest.v3。")
    if _canonical_manifest(manifest) != _canonical_manifest(expected): errors.append("TechnicalPlan.authorization_manifest 必须由已确认规则、产品目标和技术绑定确定性编译。")
    if manifest.get("fingerprint") != expected.get("fingerprint"): errors.append("TechnicalPlan.authorization_manifest.fingerprint 与当前内容不一致。")
    return errors
