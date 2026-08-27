"""TechnicalPlan 确认前的权限语义与可交付性确定性门禁。"""

from __future__ import annotations

from typing import Any

from app.services.authorization_manifest import (
    SYSTEM_RESOURCE_KEY,
    validate_authorization_manifest,
)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信数组中只保留对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """归一化非空文本列表并保持稳定顺序。"""

    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, list) else []


def _check(check_id: str, label: str, failures: list[str], notes: list[str] | None = None) -> dict[str, Any]:
    """构造前端可只读渲染的一项门禁检查结果。"""

    return {
        "id": check_id,
        "label": label,
        "status": "fail" if failures else "pass",
        "details": [
            *[{"status": "fail", "message": message} for message in failures],
            *[{"status": "info", "message": message} for message in (notes or [])],
        ],
    }


def _endpoint_catalog(api_contracts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """建立 Endpoint 到所属 API Contract 的索引，用于唯一 Controller Task 可交付性判断。"""

    result: dict[str, list[str]] = {}
    for contract in _dict_items(api_contracts):
        contract_id = str(contract.get("id") or "").strip()
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "").strip()
            if endpoint_id:
                result.setdefault(endpoint_id, []).append(contract_id)
    return result


def _action_endpoint_ids(pages: list[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """收集 action 的直接业务 Endpoint 与 sequence 业务步骤 Endpoint。"""

    result: dict[tuple[str, str], list[str]] = {}
    for page in _dict_items(pages):
        page_id = str(page.get("pageId") or "").strip()
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        for implementation in _dict_items(references.get("action_implementations")):
            action_id = str(implementation.get("actionId") or "").strip()
            if not page_id or not action_id:
                continue
            endpoint_ids = result.setdefault((page_id, action_id), [])
            for endpoint_id in [
                implementation.get("endpointId"),
                *[step.get("endpointId") for step in _dict_items(implementation.get("stepBindings"))],
            ]:
                normalized = str(endpoint_id or "").strip()
                if normalized and normalized not in endpoint_ids:
                    endpoint_ids.append(normalized)
    return result


def _page_endpoint_ids(pages: list[dict[str, Any]], page_id: str) -> list[str]:
    """读取页面显式声明的 Endpoint 依赖，保留未授权 Endpoint 的默认可访问语义。"""

    page = next(
        (item for item in _dict_items(pages) if str(item.get("pageId") or "").strip() == page_id),
        {},
    )
    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    return [
        str(item.get("endpoint_id") or item.get("endpointId") or "").strip()
        for item in _dict_items(references.get("endpoint_dependencies"))
        if str(item.get("endpoint_id") or item.get("endpointId") or "").strip()
    ]


def authorization_deliverability_report(
    manifest: Any,
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    api_contracts: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    page_implementation_contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成步骤 4E 的只读报告；报告本身不写入任何正式权限事实。"""

    raw_manifest = manifest if isinstance(manifest, dict) else {}
    bindings = raw_manifest.get("bindings") if isinstance(raw_manifest.get("bindings"), dict) else {}
    resources = {
        str(resource.get("resourceKey") or "").strip()
        for resource in _dict_items(raw_manifest.get("resources"))
        if str(resource.get("resourceKey") or "").strip()
    }
    page_bindings = _dict_items(bindings.get("pages"))
    action_bindings = _dict_items(bindings.get("actions"))
    endpoint_bindings = _dict_items(bindings.get("endpoints"))
    endpoint_binding_map = {
        str(item.get("endpointId") or "").strip(): _text_items(item.get("operationResourceKeys"))
        for item in endpoint_bindings
        if str(item.get("endpointId") or "").strip()
    }
    product_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_items(product_plan.get("pages"))
        if str(page.get("pageId") or "").strip()
    }
    product_actions = {
        (page_id, str(action.get("actionId") or "").strip())
        for page_id, page in product_pages.items()
        for action in _dict_items(page.get("actions"))
        if str(action.get("actionId") or "").strip()
    }
    endpoints = _endpoint_catalog(api_contracts)
    action_endpoints = _action_endpoint_ids(pages)
    action_resource_keys = {
        (str(item.get("pageId") or "").strip(), str(item.get("actionId") or "").strip()): str(item.get("resourceKey") or "").strip()
        for item in action_bindings
        if str(item.get("pageId") or "").strip() and str(item.get("actionId") or "").strip()
    }

    resource_failures = [
        f"{target} 引用了不存在的 ResourceKey：{resource_key}。"
        for target, resource_key in [
            *[(f"Page {item.get('pageId')}", str(item.get("resourceKey") or "").strip()) for item in page_bindings],
            *[(f"Action {item.get('pageId')}/{item.get('actionId')}", str(item.get("resourceKey") or "").strip()) for item in action_bindings],
            *[(f"Endpoint {item.get('endpointId')}", resource_key) for item in endpoint_bindings for resource_key in _text_items(item.get("operationResourceKeys"))],
        ]
        if not resource_key or resource_key not in resources
    ]
    page_failures = [
        f"Page binding 引用了未确认页面：{item.get('pageId')}。"
        for item in page_bindings
        if str(item.get("pageId") or "").strip() not in product_pages
    ]
    action_failures = [
        f"Action binding 引用了无效顶层 action：{item.get('pageId')}/{item.get('actionId')}。"
        for item in action_bindings
        if (str(item.get("pageId") or "").strip(), str(item.get("actionId") or "").strip()) not in product_actions
    ]
    endpoint_failures = [
        f"Endpoint binding 引用了不存在或不唯一的 API Endpoint：{endpoint_id}。"
        for endpoint_id in endpoint_binding_map
        if len(endpoints.get(endpoint_id, [])) != 1
    ]
    closure_failures: list[str] = []
    for target, resource_key in action_resource_keys.items():
        if not resource_key:
            continue
        for endpoint_id in action_endpoints.get(target, []):
            if resource_key not in endpoint_binding_map.get(endpoint_id, []):
                closure_failures.append(
                    f"受控 Action {target[0]}/{target[1]} 未把资源 {resource_key} 闭合到 Endpoint {endpoint_id}。"
                )
    for endpoint_id, resource_keys in endpoint_binding_map.items():
        for resource_key in resource_keys:
            owners = [target for target, key in action_resource_keys.items() if key == resource_key]
            if not any(endpoint_id in action_endpoints.get(target, []) for target in owners):
                closure_failures.append(
                    f"Endpoint {endpoint_id} 的资源 {resource_key} 无法反向追踪到引用它的 Action。"
                )
    guard_failures = [
        f"Endpoint {endpoint_id} 无法编译为唯一 Controller Task 的 ANY-OF guard 约束。"
        for endpoint_id, resource_keys in endpoint_binding_map.items()
        if resource_keys and len(endpoints.get(endpoint_id, [])) != 1
    ]
    projection_failures: list[str] = []
    contract_bindings = {
        (str(contract.get("pageId") or "").strip(), str(binding.get("targetType") or "").strip(), str(binding.get("actionId") or "").strip(), str(binding.get("resourceKey") or "").strip())
        for contract in _dict_items(page_implementation_contracts)
        for binding in _dict_items(contract.get("permissionBindings"))
    }
    for page_id, resource_key in [(str(item.get("pageId") or "").strip(), str(item.get("resourceKey") or "").strip()) for item in page_bindings]:
        if (page_id, "page", "", resource_key) not in contract_bindings:
            projection_failures.append(f"PageImplementationContract 未投影页面权限：{page_id}/{resource_key}。")
    for (page_id, action_id), resource_key in action_resource_keys.items():
        if (page_id, "action", action_id, resource_key) not in contract_bindings:
            projection_failures.append(f"PageImplementationContract 未投影操作权限：{page_id}/{action_id}/{resource_key}。")
    controlled_pages = {str(item.get("pageId") or "").strip() for item in page_bindings}
    default_access_notes = [
        f"受控 Page {page_id} 调用 Endpoint {endpoint_id}：无 Endpoint 授权绑定，按当前契约默认可访问。"
        for page_id in controlled_pages
        for endpoint_id in _page_endpoint_ids(pages, page_id)
        if endpoint_id not in endpoint_binding_map or not endpoint_binding_map[endpoint_id]
    ]
    endpoint_controls: dict[str, set[bool]] = {}
    for target, endpoint_ids in action_endpoints.items():
        controlled = bool(action_resource_keys.get(target))
        for endpoint_id in endpoint_ids:
            endpoint_controls.setdefault(endpoint_id, set()).add(controlled)
    mixed_failures = [
        "ENDPOINT_AUTHORIZATION_MIXED_CONTROL：Endpoint 同时被受控与未受控操作引用：" + endpoint_id + "。"
        for endpoint_id, controls in sorted(endpoint_controls.items())
        if len(controls) > 1
    ]
    authorization = raw_manifest.get("defaultRoleAuthorization") if isinstance(raw_manifest.get("defaultRoleAuthorization"), dict) else {}
    initial_role = str(authorization.get("initialAdminRoleSeedKey") or "").strip()
    grants = {
        str(item.get("roleSeedKey") or "").strip(): set(_text_items(item.get("resourceKeys")))
        for item in _dict_items(authorization.get("roleResourceGrants"))
    }
    initial_admin_failures = [] if not raw_manifest.get("enabled") else [
        "Initial Admin 未拥有 system_authorization_management 系统资源。"
    ] if not initial_role or SYSTEM_RESOURCE_KEY not in grants.get(initial_role, set()) else []
    integrity_failures = validate_authorization_manifest(manifest, requirement_spec, product_plan, api_contracts, pages)

    checks = [
        _check("manifest_integrity", "Manifest、数据权限与 fingerprint 完整性", integrity_failures),
        _check("resource_keys", "ResourceKey 是否存在", resource_failures),
        _check("page_bindings", "Page binding 是否有效", page_failures),
        _check("action_bindings", "Action binding 是否有效", action_failures),
        _check("endpoint_bindings", "Endpoint binding 是否有效", endpoint_failures),
        _check("action_endpoint_resource_closure", "Action → Endpoint → Resource 是否闭环", closure_failures, default_access_notes),
        _check("controller_guard_delivery", "受控能力是否可交付后端 guard", [*guard_failures, *projection_failures]),
        _check("mixed_endpoint_control", "Endpoint 是否混用受控/未受控 action", mixed_failures),
        _check("initial_admin_system_resource", "Initial Admin 是否拥有 system resource", initial_admin_failures),
    ]
    return {
        "schemaVersion": "authorization-deliverability.v1",
        "passed": all(check["status"] == "pass" for check in checks),
        "checks": checks,
    }


def authorization_deliverability_errors(report: dict[str, Any]) -> list[str]:
    """把只读报告中的阻断明细转换为现有自动修复链路使用的错误文本。"""

    return [
        f"4E {check.get('label')}：{detail.get('message')}"
        for check in _dict_items(report.get("checks"))
        for detail in _dict_items(check.get("details"))
        if detail.get("status") == "fail"
    ]
