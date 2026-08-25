"""编译并校验 TechnicalPlan 的当前 RBAC 资源目录。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


AUTHORIZATION_MANIFEST_SCHEMA_VERSION = "authorization-manifest.v1"
_SYSTEM_RESOURCES = (
    ("system.authorization.page", "page", "权限管理", "访问系统权限管理页面。"),
    ("system.authorization.resources.read", "operation", "查看资源目录", "查看固定权限资源目录。"),
    ("system.authorization.roles.read", "operation", "查看角色", "查看运行态角色。"),
    ("system.authorization.roles.write", "operation", "维护角色", "创建、修改、启用或停用角色。"),
    ("system.authorization.member_roles.read", "operation", "查看成员角色", "查看成员角色关系。"),
    ("system.authorization.member_roles.write", "operation", "维护成员角色", "设置成员角色关系。"),
    ("system.authorization.role_resources.read", "operation", "查看角色资源", "查看角色资源关系。"),
    ("system.authorization.role_resources.write", "operation", "维护角色资源", "设置角色资源关系。"),
    ("system.authorization.effective_permissions.read", "operation", "查看有效权限", "查看成员最终有效权限。"),
    ("system.authorization.audit.read", "operation", "查看授权审计", "查看授权审计与当前修订。"),
)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信数组中仅保留对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """归一化字符串列表并去重排序。"""

    return sorted({str(item).strip() for item in value if str(item).strip()}) if isinstance(value, list) else []


def _action_endpoint_ids(pages: list[dict[str, Any]]) -> dict[str, list[str]]:
    """从 TechnicalPlan action 实现中收集每个 action 的真实 endpoint。"""

    result: dict[str, set[str]] = {}
    for page in pages:
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        for item in _dict_items(references.get("action_implementations")):
            action_id = str(item.get("actionId") or "").strip()
            if not action_id:
                continue
            endpoint_ids = result.setdefault(action_id, set())
            endpoint_id = str(item.get("endpointId") or "").strip()
            if endpoint_id:
                endpoint_ids.add(endpoint_id)
            for step in _dict_items(item.get("stepBindings")):
                step_endpoint_id = str(step.get("endpointId") or "").strip()
                if step_endpoint_id:
                    endpoint_ids.add(step_endpoint_id)
    return {action_id: sorted(endpoint_ids) for action_id, endpoint_ids in result.items()}


def _endpoint_entity_ids(api_contracts: list[dict[str, Any]]) -> dict[str, set[str]]:
    """构造 endpoint 到所属 API Contract 实体集合的索引。"""

    result: dict[str, set[str]] = {}
    for contract in api_contracts:
        entity_ids = set(_text_items(contract.get("entity_ids")))
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "").strip()
            if endpoint_id:
                result[endpoint_id] = entity_ids
    return result


def _canonical_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """按当前契约稳定排序 manifest，供指纹和严格比较复用。"""

    value = {key: item for key, item in manifest.items() if key != "fingerprint"}
    bindings = value.get("bindings") if isinstance(value.get("bindings"), dict) else {}
    value["resources"] = sorted(
        _dict_items(value.get("resources")), key=lambda item: str(item.get("resourceKey") or "")
    )
    value["bindings"] = {
        "pages": sorted(_dict_items(bindings.get("pages")), key=lambda item: str(item.get("pageId") or "")),
        "actions": sorted(_dict_items(bindings.get("actions")), key=lambda item: str(item.get("actionId") or "")),
        "endpoints": sorted(_dict_items(bindings.get("endpoints")), key=lambda item: str(item.get("endpointId") or "")),
        "dataRules": sorted(_dict_items(bindings.get("dataRules")), key=lambda item: str(item.get("ruleId") or "")),
        "systemPages": sorted(_dict_items(bindings.get("systemPages")), key=lambda item: str(item.get("pageId") or "")),
    }
    for binding in value["bindings"]["endpoints"]:
        binding["requiredOperationResourceKeys"] = _text_items(binding.get("requiredOperationResourceKeys"))
        binding["dataPolicyKeys"] = _text_items(binding.get("dataPolicyKeys"))
    for binding in value["bindings"]["dataRules"]:
        binding["entityIds"] = _text_items(binding.get("entityIds"))
        binding["endpointIds"] = _text_items(binding.get("endpointIds"))
    return value


def _with_fingerprint(manifest: dict[str, Any]) -> dict[str, Any]:
    """为稳定 manifest 计算当前 SHA-256 指纹。"""

    canonical = _canonical_manifest(manifest)
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**canonical, "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def compile_authorization_manifest(
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    api_contracts: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    data_rule_bindings: Any = None,
) -> dict[str, Any]:
    """从已确认上游产物确定性编译当前权限资源目录与绑定。"""

    authorization = requirement_spec.get("authorization_requirements")
    authorization = authorization if isinstance(authorization, dict) else {}
    enabled = authorization.get("enabled") is True
    if not enabled:
        return _with_fingerprint({
            "schema_version": AUTHORIZATION_MANIFEST_SCHEMA_VERSION,
            "enabled": False,
            "unauthorizedBehavior": {},
            "resources": [],
            "bindings": {"pages": [], "actions": [], "endpoints": [], "dataRules": [], "systemPages": []},
        })

    targets = product_plan.get("authorizationTargets")
    targets = targets if isinstance(targets, dict) else {}
    page_rules = _dict_items(authorization.get("restrictedPages"))
    operation_rules = _dict_items(authorization.get("restrictedOperations"))
    data_rules = _dict_items(authorization.get("dataRules"))
    page_target_by_rule = {
        str(item.get("ruleId") or "").strip(): str(item.get("pageId") or "").strip()
        for item in _dict_items(targets.get("pageRules"))
    }
    action_target_by_rule = {
        str(item.get("ruleId") or "").strip(): str(item.get("actionId") or "").strip()
        for item in _dict_items(targets.get("operationRules"))
    }
    resources: list[dict[str, Any]] = []
    page_bindings: list[dict[str, str]] = []
    action_bindings: list[dict[str, str]] = []
    endpoint_operations: dict[str, set[str]] = {}
    action_endpoints = _action_endpoint_ids(pages)

    def add_business_resource(resource_key: str, resource_type: str, name: str, description: str, semantic_definition: str, rule_ids: list[str], target: str, policy_key: str = "") -> None:
        """追加带稳定来源的业务资源，避免模型参与资源键选择。"""

        resource: dict[str, Any] = {
            "resourceKey": resource_key,
            "origin": "business",
            "type": resource_type,
            "name": name,
            "description": description,
            "semanticDefinition": semantic_definition,
            "sourceRuleIds": sorted(rule_ids),
            "targetResourceRef": target,
        }
        if policy_key:
            resource["policyKey"] = policy_key
        resources.append(resource)

    page_rule_groups: dict[str, list[dict[str, Any]]] = {}
    for rule in page_rules:
        rule_id = str(rule.get("ruleId") or "").strip()
        page_id = page_target_by_rule.get(rule_id, "")
        if rule_id and page_id:
            page_rule_groups.setdefault(page_id, []).append(rule)
    for page_id, rules in page_rule_groups.items():
        key = f"business.page.{page_id}"
        add_business_resource(key, "page", str(rules[0].get("name") or page_id), str(rules[0].get("description") or ""), str(rules[0].get("rationale") or rules[0].get("description") or ""), [str(rule.get("ruleId")) for rule in rules], f"page:{page_id}")
        page_bindings.append({"pageId": page_id, "resourceKey": key})

    operation_rule_groups: dict[str, list[dict[str, Any]]] = {}
    for rule in operation_rules:
        rule_id = str(rule.get("ruleId") or "").strip()
        action_id = action_target_by_rule.get(rule_id, "")
        if rule_id and action_id:
            operation_rule_groups.setdefault(action_id, []).append(rule)
    for action_id, rules in operation_rule_groups.items():
        key = f"business.operation.{action_id}"
        add_business_resource(key, "operation", str(rules[0].get("name") or action_id), str(rules[0].get("description") or ""), str(rules[0].get("rationale") or rules[0].get("description") or ""), [str(rule.get("ruleId")) for rule in rules], f"action:{action_id}")
        action_bindings.append({"actionId": action_id, "resourceKey": key})
        for endpoint_id in action_endpoints.get(action_id, []):
            endpoint_operations.setdefault(endpoint_id, set()).add(key)

    raw_data_bindings = {
        str(item.get("ruleId") or "").strip(): item
        for item in _dict_items(data_rule_bindings)
        if str(item.get("ruleId") or "").strip()
    }
    data_bindings: list[dict[str, Any]] = []
    endpoint_policies: dict[str, set[str]] = {}
    for rule in data_rules:
        rule_id = str(rule.get("ruleId") or "").strip()
        if not rule_id:
            continue
        resource_key = f"business.data.{rule_id}"
        policy_key = f"business.data-policy.{rule_id}"
        add_business_resource(resource_key, "data", str(rule.get("name") or rule_id), str(rule.get("description") or rule.get("ruleDescription") or ""), str(rule.get("ruleDescription") or rule.get("description") or ""), [rule_id], f"data-rule:{rule_id}", policy_key)
        raw_binding = raw_data_bindings.get(rule_id, {})
        entity_ids = _text_items(raw_binding.get("entityIds"))
        endpoint_ids = _text_items(raw_binding.get("endpointIds"))
        data_bindings.append({"ruleId": rule_id, "entityIds": entity_ids, "endpointIds": endpoint_ids, "resourceKey": resource_key, "policyKey": policy_key})
        for endpoint_id in endpoint_ids:
            endpoint_policies.setdefault(endpoint_id, set()).add(policy_key)

    for resource_key, resource_type, name, description in _SYSTEM_RESOURCES:
        resources.append({"resourceKey": resource_key, "origin": "system", "type": resource_type, "name": name, "description": description, "semanticDefinition": description, "sourceRuleIds": []})
    endpoint_bindings = [
        {"endpointId": endpoint_id, "requiredOperationResourceKeys": sorted(endpoint_operations.get(endpoint_id, set())), "dataPolicyKeys": sorted(endpoint_policies.get(endpoint_id, set()))}
        for endpoint_id in sorted(set(endpoint_operations) | set(endpoint_policies))
    ]
    behavior = authorization.get("unauthorizedBehavior")
    return _with_fingerprint({
        "schema_version": AUTHORIZATION_MANIFEST_SCHEMA_VERSION,
        "enabled": True,
        "unauthorizedBehavior": dict(behavior) if isinstance(behavior, dict) else {},
        "resources": resources,
        "bindings": {
            "pages": page_bindings,
            "actions": action_bindings,
            "endpoints": endpoint_bindings,
            "dataRules": data_bindings,
            "systemPages": [{"pageId": "system_authorization_management", "route": "/roles", "resourceKey": "system.authorization.page"}],
        },
    })


def validate_authorization_manifest(
    manifest: Any,
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    api_contracts: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> list[str]:
    """校验 manifest 覆盖、技术目标和指纹，拒绝任何手写资源差异。"""

    if not isinstance(manifest, dict):
        return ["TechnicalPlan.authorization_manifest 必须是 JSON 对象。"]
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    authorization = requirement_spec.get("authorization_requirements")
    authorization = authorization if isinstance(authorization, dict) else {}
    enabled = authorization.get("enabled") is True
    if manifest.get("enabled") is not enabled:
        return ["TechnicalPlan.authorization_manifest.enabled 必须与 RequirementSpec 一致。"]
    if enabled:
        targets = product_plan.get("authorizationTargets") if isinstance(product_plan.get("authorizationTargets"), dict) else {}
        expected_page_rule_ids = {
            str(item.get("ruleId") or "").strip()
            for item in _dict_items(authorization.get("restrictedPages"))
            if str(item.get("ruleId") or "").strip()
        }
        expected_operation_rule_ids = {
            str(item.get("ruleId") or "").strip()
            for item in _dict_items(authorization.get("restrictedOperations"))
            if str(item.get("ruleId") or "").strip()
        }
        expected_data_rule_ids = {
            str(item.get("ruleId") or "").strip()
            for item in _dict_items(authorization.get("dataRules"))
            if str(item.get("ruleId") or "").strip()
        }
        target_page_rule_ids = {str(item.get("ruleId") or "").strip() for item in _dict_items(targets.get("pageRules"))}
        target_operation_rule_ids = {str(item.get("ruleId") or "").strip() for item in _dict_items(targets.get("operationRules"))}
        if target_page_rule_ids != expected_page_rule_ids:
            errors = ["ProductPlan.authorizationTargets.pageRules 必须与 RequirementSpec 页面权限规则一一对应。"]
        elif target_operation_rule_ids != expected_operation_rule_ids:
            errors = ["ProductPlan.authorizationTargets.operationRules 必须与 RequirementSpec 操作权限规则一一对应。"]
        else:
            errors = []
        actual_page_rule_ids = {
            rule_id for resource in _dict_items(manifest.get("resources"))
            if resource.get("origin") == "business" and resource.get("type") == "page"
            for rule_id in _text_items(resource.get("sourceRuleIds"))
        }
        actual_operation_rule_ids = {
            rule_id for resource in _dict_items(manifest.get("resources"))
            if resource.get("origin") == "business" and resource.get("type") == "operation"
            for rule_id in _text_items(resource.get("sourceRuleIds"))
        }
        actual_data_rule_ids = {str(item.get("ruleId") or "").strip() for item in _dict_items(bindings.get("dataRules"))}
        if actual_page_rule_ids != expected_page_rule_ids:
            errors.append("权限 manifest 的页面资源必须完整且仅覆盖当前页面权限规则。")
        if actual_operation_rule_ids != expected_operation_rule_ids:
            errors.append("权限 manifest 的操作资源必须完整且仅覆盖当前操作权限规则。")
        if actual_data_rule_ids != expected_data_rule_ids:
            errors.append("权限 manifest 的数据规则绑定必须完整且仅覆盖当前数据权限规则。")
    else:
        errors = []
    expected = compile_authorization_manifest(requirement_spec, product_plan, api_contracts, pages, bindings.get("dataRules"))
    if manifest.get("schema_version") != AUTHORIZATION_MANIFEST_SCHEMA_VERSION:
        errors.append("TechnicalPlan.authorization_manifest.schema_version 必须为 authorization-manifest.v1。")
    if _canonical_manifest(manifest) != _canonical_manifest(expected):
        errors.append("TechnicalPlan.authorization_manifest 必须由已确认规则、产品目标和技术绑定确定性编译。")
    if manifest.get("fingerprint") != expected.get("fingerprint"):
        errors.append("TechnicalPlan.authorization_manifest.fingerprint 与当前内容不一致。")
    endpoint_entities = _endpoint_entity_ids(api_contracts)
    for binding in _dict_items(bindings.get("dataRules")):
        rule_id = str(binding.get("ruleId") or "").strip()
        entity_ids = set(_text_items(binding.get("entityIds")))
        endpoint_ids = _text_items(binding.get("endpointIds"))
        if not entity_ids or not endpoint_ids:
            errors.append(f"数据规则 {rule_id or 'unknown'} 必须绑定非空 entityIds 和 endpointIds。")
        for endpoint_id in endpoint_ids:
            if endpoint_id not in endpoint_entities:
                errors.append(f"数据规则 {rule_id or 'unknown'} 引用了不存在的 endpoint {endpoint_id}。")
            elif not entity_ids.intersection(endpoint_entities[endpoint_id]):
                errors.append(f"数据规则 {rule_id or 'unknown'} 的 endpoint {endpoint_id} 未覆盖绑定实体。")
    return errors
