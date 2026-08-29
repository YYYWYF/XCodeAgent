"""将已确认权限 manifest 投射为 Build Unit 的只读权限切片。"""

from __future__ import annotations

from typing import Any

from app.services.authorization_frontend_projection import (
    compile_frontend_authorization_projection,
    resource_constant_reference,
)


def compile_authorization_overlay(
    project_plan: dict[str, Any], build_context: dict[str, Any]
) -> dict[str, Any]:
    """在叶子任务生成前按当前目标裁剪页面、操作和接口权限事实。"""

    manifest = project_plan.get("authorization_manifest")
    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return {key: value for key, value in build_context.items() if key != "authorization_constraints"}

    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("已确认 TechnicalPlan 缺少 authorization_manifest.bindings。")
    page_ids = _page_ids_in_scope(build_context)
    endpoint_refs = _endpoint_refs_in_scope(project_plan, build_context)
    endpoint_keys = {endpoint_id for _api_contract_id, endpoint_id in endpoint_refs}
    page_bindings = _binding_items(bindings.get("pages"), "pageId")
    action_bindings = _binding_items(bindings.get("actions"), "actionId")
    endpoint_bindings = _endpoint_binding_map(bindings.get("endpoints"))

    pages = [
        {"pageId": page_id, "resourceKey": str(page_bindings[page_id]["resourceKey"])}
        for page_id in sorted(page_ids)
        if page_id in page_bindings
    ]
    actions = [
        {
            "pageId": str(binding["pageId"]),
            "actionId": str(binding["actionId"]),
            "resourceKey": str(binding["resourceKey"]),
            "mode": str(binding.get("mode") or "hidden"),
        }
        for binding in action_bindings.values()
        if str(binding.get("pageId") or "") in page_ids
    ]
    endpoints = [
        {
            "apiContractId": api_contract_id,
            "endpointId": endpoint_id,
            **_endpoint_http_identity(project_plan, api_contract_id, endpoint_id),
            "operationResourceKeys": list(endpoint_bindings.get(endpoint_id, [])),
            "semantics": "ANY_OF",
        }
        for api_contract_id, endpoint_id in endpoint_refs
        if endpoint_id in endpoint_keys
    ]
    auth_constants_projection = _auth_constants_projection(endpoints)
    return {
        **build_context,
        "authorization_constraints": {
            "pages": pages,
            "actions": sorted(
                [
                    {
                        **item,
                        "mode": str(item["mode"]),
                        "resourceConstant": resource_constant_reference(
                            str(item["resourceKey"]),
                            "operation",
                            page_id=str(item["pageId"]),
                            action_id=str(item["actionId"]),
                        ),
                    }
                    for item in actions
                ],
                key=lambda item: (item["pageId"], item["actionId"]),
            ),
            "endpoints": endpoints,
            "frontendProjection": compile_frontend_authorization_projection(project_plan),
            "authConstantsProjection": auth_constants_projection,
        },
    }


def unit_authorization_slice(unit_id: str, build_context: dict[str, Any]) -> dict[str, Any] | None:
    """仅向页面或后端 Endpoint Unit 投射其实际需要的权限切片。"""

    constraints = build_context.get("authorization_constraints")
    if not isinstance(constraints, dict):
        return None
    if unit_id.startswith("page:"):
        page_id = unit_id.removeprefix("page:")
        return {
            "pages": [
                dict(item)
                for item in _dict_items(constraints.get("pages"))
                if str(item.get("pageId") or "") == page_id
            ],
            "actions": [
                dict(item)
                for item in _dict_items(constraints.get("actions"))
                if str(item.get("pageId") or "") == page_id
            ],
        }
    if unit_id.startswith("backend:endpoint:"):
        identity = unit_id.removeprefix("backend:endpoint:").split(":", 1)
        if len(identity) != 2:
            raise ValueError(f"Endpoint Unit 标识无效，无法投射权限切片：{unit_id}。")
        api_contract_id, endpoint_id = identity
        endpoints = [
                dict(item)
                for item in _dict_items(constraints.get("endpoints"))
                if str(item.get("apiContractId") or "") == api_contract_id
                and str(item.get("endpointId") or "") == endpoint_id
            ]
        resource_keys = {
            str(resource_key)
            for endpoint in endpoints
            for resource_key in endpoint.get("operationResourceKeys") or []
        }
        return {
            "endpoints": endpoints,
            "authConstants": [
                dict(item)
                for item in _dict_items(constraints.get("authConstantsProjection"))
                if str(item.get("resourceKey") or "") in resource_keys
            ],
        }
    return None


def _page_ids_in_scope(build_context: dict[str, Any]) -> set[str]:
    """从当前目标和 Unit 范围提取待构建页面，不扩展到其他页面。"""

    target = build_context.get("target")
    page_ids = {
        str(target.get("id") or "").strip()
        for target in [target]
        if isinstance(target, dict) and target.get("type") == "page"
    }
    page_ids.update(
        str(unit_id).removeprefix("page:")
        for unit_id in build_context.get("required_unit_ids") or []
        if str(unit_id).startswith("page:")
    )
    return {page_id for page_id in page_ids if page_id}


def _endpoint_refs_in_scope(
    project_plan: dict[str, Any], build_context: dict[str, Any]
) -> list[tuple[str, str]]:
    """按当前上下文或 Endpoint Unit 解析唯一的接口身份。"""

    endpoint_ids = {
        str(endpoint_id).strip()
        for endpoint_id in build_context.get("endpoint_ids") or []
        if str(endpoint_id).strip()
    }
    for unit_id in build_context.get("required_unit_ids") or []:
        text = str(unit_id)
        if text.startswith("backend:endpoint:"):
            identity = text.removeprefix("backend:endpoint:").split(":", 1)
            if len(identity) == 2:
                endpoint_ids.add(identity[1])
    required_contract_id = ""
    target = build_context.get("target")
    if isinstance(target, dict) and target.get("type") == "endpoint":
        required_contract_id = str(target.get("api_contract_id") or "").strip()
    result: list[tuple[str, str]] = []
    for contract in _dict_items(project_plan.get("api_contracts")):
        api_contract_id = str(contract.get("id") or "").strip()
        if required_contract_id and api_contract_id != required_contract_id:
            continue
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "").strip()
            if api_contract_id and endpoint_id and endpoint_id in endpoint_ids:
                result.append((api_contract_id, endpoint_id))
    return sorted(set(result))


def _binding_items(value: Any, identity_key: str) -> dict[str, dict[str, Any]]:
    """读取页面或操作绑定并拒绝缺失资源键的确认产物。"""

    result: dict[str, dict[str, Any]] = {}
    for item in _dict_items(value):
        identity = str(item.get(identity_key) or "").strip()
        resource_key = str(item.get("resourceKey") or "").strip()
        if not identity or not resource_key:
            raise ValueError("已确认 TechnicalPlan 的权限绑定缺少稳定目标或 resourceKey。")
        key = identity if identity_key == "pageId" else f"{item.get('pageId')}\0{identity}"
        result[key] = dict(item)
    return result


def _endpoint_binding_map(value: Any) -> dict[str, list[str]]:
    """读取 Endpoint ANY-OF 资源绑定，并保持 manifest 中的稳定排序。"""

    result: dict[str, list[str]] = {}
    for item in _dict_items(value):
        endpoint_id = str(item.get("endpointId") or "").strip()
        resource_keys = sorted(
            {
                str(resource_key).strip()
                for resource_key in item.get("operationResourceKeys") or []
                if str(resource_key).strip()
            }
        )
        if endpoint_id:
            result[endpoint_id] = resource_keys
    return result


def _endpoint_http_identity(
    project_plan: dict[str, Any], api_contract_id: str, endpoint_id: str
) -> dict[str, str]:
    """从确认 API Contract 读取 Endpoint 的唯一 HTTP 定位信息。"""

    for contract in _dict_items(project_plan.get("api_contracts")):
        if str(contract.get("id") or "").strip() != api_contract_id:
            continue
        for endpoint in _dict_items(contract.get("endpoints")):
            if str(endpoint.get("id") or "").strip() != endpoint_id:
                continue
            method = str(endpoint.get("method") or "GET").strip().upper()
            path = str(endpoint.get("path") or "").strip()
            if not path.startswith("/"):
                raise ValueError(f"受控 Endpoint {api_contract_id}:{endpoint_id} 缺少合法 HTTP Path。")
            return {"httpMethod": method, "path": path}
    raise ValueError(f"受控 Endpoint {api_contract_id}:{endpoint_id} 不存在于确认 API Contract。")


def _auth_constants_projection(endpoints: list[dict[str, Any]]) -> list[dict[str, str]]:
    """从非空 Endpoint 操作资源点并集编译后端常量，不包含页面或系统资源。"""

    resource_keys = sorted(
        {
            str(resource_key).strip()
            for endpoint in endpoints
            for resource_key in endpoint.get("operationResourceKeys") or []
            if str(resource_key).strip()
            and str(resource_key).strip() != "system_authorization_management"
        }
    )
    constants: list[dict[str, str]] = []
    names: set[str] = set()
    for resource_key in resource_keys:
        constant_name = f"{resource_key.upper()}_RESOURCE"
        if constant_name in names:
            raise ValueError(f"操作资源常量名冲突：{constant_name}。")
        names.add(constant_name)
        constants.append({"name": constant_name, "resourceKey": resource_key})
    return constants


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """将不可信列表收敛为字典列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
