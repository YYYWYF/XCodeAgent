from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.api_schema_refs import (
    normalize_local_schema_ref,
    normalize_schema_references,
    schema_field_paths,
)


def dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalize_api_contracts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化紧凑业务契约，并把契约内 Schema 引用统一为裸名称。"""

    normalized: list[dict[str, Any]] = []
    for item in items:
        contract_id = str(item.get("id") or "resource_api")
        schemas = _normalize_schemas(item.get("schemas"), contract_id=contract_id)
        authentication = _normalize_authentication(item.get("authentication"))
        endpoints = [
            _normalize_endpoint(
                endpoint,
                contract_id=contract_id,
                base_path=str(item.get("base_path") or "/api/resource"),
                contract_authentication=authentication,
            )
            for endpoint in dict_items(item.get("endpoints"))
        ]
        normalized.append(
            {
                **item,
                "id": contract_id,
                "data_source_id": str(item.get("data_source_id") or ""),
                "resource": str(item.get("resource") or contract_id),
                "base_path": str(item.get("base_path") or "/api/resource"),
                "schemas": schemas,
                "authentication": authentication,
                "endpoints": endpoints,
            }
        )
    return normalized


def endpoint_dependencies_for_contracts(
    contracts: list[dict[str, Any]],
    data_source_ids: list[str],
    *,
    page_path: str = "",
    page_name: str = "",
) -> list[dict[str, Any]]:
    allowed_sources = set(data_source_ids)
    is_detail_page = "{id}" in page_path or ":id" in page_path or "详情" in page_name
    preferred_suffix = ".detail" if is_detail_page else ".list"
    dependencies: list[dict[str, Any]] = []
    for contract in contracts:
        if contract.get("data_source_id") not in allowed_sources:
            continue
        read_endpoints = [
            endpoint
            for endpoint in dict_items(contract.get("endpoints"))
            if endpoint.get("method") == "GET"
        ]
        preferred = [
            endpoint
            for endpoint in read_endpoints
            if str(endpoint.get("id") or "").endswith(preferred_suffix)
        ]
        for endpoint in preferred or read_endpoints:
            dependencies.append(
                {
                    "api_contract_id": contract.get("id"),
                    "endpoint_id": endpoint.get("id"),
                    "usage": "page_load" if not is_detail_page else "load_detail",
                    "required": True,
                }
            )
    return dependencies


def normalize_page_api_dependencies(
    contracts: list[dict[str, Any]],
    data_source_ids: list[str],
    api_dependencies: Any,
    *,
    page_path: str = "",
    page_name: str = "",
) -> list[dict[str, Any]]:
    """Normalize page-level API usage while preserving model-supplied intent."""

    endpoint_index = {
        str(endpoint.get("id")): (contract, endpoint)
        for contract in contracts
        for endpoint in dict_items(contract.get("endpoints"))
        if endpoint.get("id")
    }
    normalized: list[dict[str, Any]] = []
    for item in dict_items(api_dependencies):
        endpoint_id = str(item.get("endpoint_id") or "")
        if endpoint_id not in endpoint_index:
            continue
        contract, endpoint = endpoint_index[endpoint_id]
        normalized.append(
            _page_api_dependency_from_endpoint(
                contract,
                endpoint,
                usage=str(item.get("usage") or _endpoint_usage(endpoint)),
                trigger=str(item.get("trigger") or ""),
                binds_to=_string_items(item.get("binds_to")),
                required_for_initial_load=_required_for_initial_load(
                    item,
                    endpoint,
                    str(item.get("usage") or _endpoint_usage(endpoint)),
                ),
            )
        )
    if normalized:
        return _dedupe_page_api_dependencies(normalized)

    return [
        _page_api_dependency_from_endpoint(
            contract,
            endpoint,
            usage=str(dependency.get("usage") or _endpoint_usage(endpoint)),
            trigger=_default_endpoint_trigger(endpoint, str(dependency.get("usage") or "")),
            binds_to=_default_endpoint_bindings(endpoint),
            required_for_initial_load=bool(dependency.get("required")),
        )
        for dependency in endpoint_dependencies_for_contracts(
            contracts,
            data_source_ids,
            page_path=page_path,
            page_name=page_name,
        )
        for contract, endpoint in [endpoint_index.get(str(dependency.get("endpoint_id")), ({}, {}))]
        if endpoint
    ]


def endpoint_dependencies_from_page_api_dependencies(
    api_dependencies: Any,
) -> list[dict[str, Any]]:
    return [
        {
            "api_contract_id": item.get("api_contract_id"),
            "endpoint_id": item.get("endpoint_id"),
            "usage": item.get("usage") or "read",
            "required": bool(item.get("required_for_initial_load")),
        }
        for item in dict_items(api_dependencies)
        if item.get("api_contract_id") and item.get("endpoint_id")
    ]


def _page_api_dependency_from_endpoint(
    contract: dict[str, Any],
    endpoint: dict[str, Any],
    *,
    usage: str,
    trigger: str,
    binds_to: list[str],
    required_for_initial_load: bool,
) -> dict[str, Any]:
    endpoint_id = str(endpoint.get("id") or "")
    return {
        "api_contract_id": str(contract.get("id") or ""),
        "endpoint_id": endpoint_id,
        "method": str(endpoint.get("method") or "GET").upper(),
        "path": str(endpoint.get("path") or ""),
        "summary": str(endpoint.get("summary") or endpoint_id),
        "usage": usage,
        "trigger": trigger or _default_endpoint_trigger(endpoint, usage),
        "required_for_initial_load": required_for_initial_load,
        "request_schema_ref": endpoint.get("request_schema_ref") or "",
        "response_schema_ref": endpoint.get("response_schema_ref") or "",
        "binds_to": binds_to or _default_endpoint_bindings(endpoint),
    }


def _endpoint_usage(endpoint: dict[str, Any]) -> str:
    method = str(endpoint.get("method") or "GET").upper()
    endpoint_id = str(endpoint.get("id") or "")
    if method == "POST":
        return "create"
    if method in {"PUT", "PATCH"}:
        return "update"
    if method == "DELETE":
        return "delete"
    if endpoint_id.endswith(".detail"):
        return "load_detail"
    return "read"


def _required_for_initial_load(
    dependency: dict[str, Any],
    endpoint: dict[str, Any],
    usage: str,
) -> bool:
    if "required_for_initial_load" in dependency:
        return bool(dependency.get("required_for_initial_load"))
    if "required" in dependency:
        return bool(dependency.get("required"))
    method = str(endpoint.get("method") or "GET").upper()
    return method == "GET" and usage in {"page_load", "load_detail", "read"}


def _default_endpoint_trigger(endpoint: dict[str, Any], usage: str) -> str:
    method = str(endpoint.get("method") or "GET").upper()
    if usage == "page_load":
        return "进入页面或提交筛选条件"
    if usage == "load_detail":
        return "点击查看详情或进入详情页"
    if method == "POST":
        return "提交新增表单"
    if method in {"PUT", "PATCH"}:
        return "提交编辑表单"
    if method == "DELETE":
        return "确认删除操作"
    return "页面交互触发"


def _default_endpoint_bindings(endpoint: dict[str, Any]) -> list[str]:
    method = str(endpoint.get("method") or "GET").upper()
    endpoint_id = str(endpoint.get("id") or "")
    if method == "GET" and endpoint_id.endswith(".list"):
        return ["主内容列表", "分页信息"]
    if method == "GET":
        return ["详情内容", "表单初值"]
    if method == "POST":
        return ["新增交互"]
    if method in {"PUT", "PATCH"}:
        return ["编辑交互"]
    if method == "DELETE":
        return ["删除交互"]
    return ["页面交互"]


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dedupe_page_api_dependencies(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        endpoint_id = str(item.get("endpoint_id") or "")
        if not endpoint_id or endpoint_id in seen:
            continue
        seen.add(endpoint_id)
        result.append(item)
    return result


def contract_endpoints_for_dependencies(
    contract: dict[str, Any],
    endpoint_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        deepcopy(endpoint)
        for endpoint in dict_items(contract.get("endpoints"))
        if not endpoint_ids or endpoint.get("id") in endpoint_ids
    ]


def schema_refs_for_data_source(
    contracts: list[dict[str, Any]],
    data_source_id: str,
) -> list[str]:
    refs: list[str] = []
    for contract in contracts:
        if contract.get("data_source_id") != data_source_id:
            continue
        refs.extend(
            f"{contract['id']}#/schemas/{schema_id}"
            for schema_id in contract.get("schemas", {})
        )
    return refs


def response_field_paths(
    contracts: list[dict[str, Any]],
    endpoint_id: str,
) -> list[str]:
    """返回 Endpoint 响应 Schema 的全部可绑定字段路径。"""

    contract, endpoint = _find_endpoint(contracts, endpoint_id)
    if not contract or not endpoint:
        return []
    contract_id = str(contract.get("id") or "")
    schemas = contract.get("schemas", {})
    schema_ref = normalize_local_schema_ref(
        endpoint.get("response_schema_ref"),
        contract_id=contract_id,
    )
    schema = schemas.get(schema_ref)
    return schema_field_paths(schema, schemas, contract_id=contract_id)


def normalize_response_path(value: Any) -> str:
    """Normalize model/user supplied response paths to contract field paths.

    API contracts store field paths in a compact dotted form such as
    ``items`` or ``items[].name``. Detail design agents may emit JSONPath-like
    variants such as ``$.items`` or a malformed trailing dot ``$.items.``.
    Keep the contract format deterministic while tolerating those harmless
    surface differences at integration boundaries.
    """

    path = str(value or "").strip()
    while path.endswith("."):
        path = path[:-1].strip()
    if path == "$":
        return ""
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:]
        if path.startswith("."):
            path = path[1:]
    while path.endswith("."):
        path = path[:-1].strip()
    return path


def normalize_response_bindings(
    contracts: list[dict[str, Any]],
    endpoint_dependencies: list[dict[str, Any]],
    bindings: Any,
) -> list[dict[str, str]]:
    allowed_by_endpoint = {
        str(item.get("endpoint_id")): set(
            response_field_paths(contracts, str(item.get("endpoint_id")))
        )
        for item in endpoint_dependencies
        if isinstance(item, dict) and item.get("endpoint_id")
    }
    provided_bindings = dict_items(bindings)
    normalized: list[dict[str, str]] = []
    if provided_bindings:
        seen: set[tuple[str, str, str]] = set()
        for binding in provided_bindings:
            endpoint_id = str(binding.get("endpoint_id") or "")
            source_path = normalize_response_path(binding.get("source_path"))
            if (
                endpoint_id not in allowed_by_endpoint
                or source_path not in allowed_by_endpoint[endpoint_id]
            ):
                continue
            page_field = str(
                binding.get("page_field")
                or source_path.rsplit(".", 1)[-1].replace("[]", "")
            )
            key = (endpoint_id, source_path, page_field)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "endpoint_id": endpoint_id,
                    "source_path": source_path,
                    "page_field": page_field,
                }
            )
        if normalized:
            return normalized

    for endpoint_id, source_paths in allowed_by_endpoint.items():
        for source_path in sorted(source_paths):
            normalized.append(
                {
                    "endpoint_id": endpoint_id,
                    "source_path": source_path,
                    "page_field": source_path.rsplit(".", 1)[-1].replace("[]", ""),
                }
            )
    return normalized


def _normalize_schemas(
    value: Any,
    *,
    contract_id: str,
) -> dict[str, dict[str, Any]]:
    """规范化 Schema 集合及其中的本地引用。"""

    if not isinstance(value, dict):
        return {}
    return {
        str(schema_id): _normalize_schema(schema, contract_id=contract_id)
        for schema_id, schema in value.items()
        if isinstance(schema, dict)
    }


def _normalize_schema(
    value: dict[str, Any],
    *,
    contract_id: str,
) -> dict[str, Any]:
    """规范化单个 Schema，同时递归统一嵌套的 ``$ref``。"""

    normalized_value = normalize_schema_references(value, contract_id=contract_id)
    schema = {
        **normalized_value,
        "type": str(normalized_value.get("type") or "object"),
    }
    if schema["type"] == "object":
        properties = normalized_value.get("properties")
        schema["properties"] = properties if isinstance(properties, dict) else {}
        required = normalized_value.get("required")
        schema["required"] = [str(item) for item in required] if isinstance(required, list) else []
    return schema


def _normalize_endpoint(
    endpoint: dict[str, Any],
    *,
    contract_id: str,
    base_path: str,
    contract_authentication: dict[str, Any],
) -> dict[str, Any]:
    """规范化 Endpoint 字段及其请求、响应 Schema 引用。"""

    method = str(endpoint.get("method") or "GET").upper()
    path = str(endpoint.get("path") or endpoint.get("url") or base_path)
    endpoint_id = str(endpoint.get("id") or f"{contract_id}.{method.lower()}")
    authentication = endpoint.get("authentication")
    return {
        **endpoint,
        "id": endpoint_id,
        "method": method,
        "path": path,
        "summary": str(endpoint.get("summary") or endpoint.get("description") or endpoint_id),
        "parameters": _normalize_parameters(endpoint.get("parameters")),
        "request_schema_ref": _optional_text(
            normalize_local_schema_ref(
                endpoint.get("request_schema_ref"),
                contract_id=contract_id,
            )
        ),
        "response_schema_ref": _optional_text(
            normalize_local_schema_ref(
                endpoint.get("response_schema_ref"),
                contract_id=contract_id,
            )
        ),
        "error_codes": [str(item) for item in endpoint.get("error_codes", [])]
        if isinstance(endpoint.get("error_codes"), list)
        else [],
        "authentication": _normalize_authentication(authentication)
        if isinstance(authentication, dict)
        else deepcopy(contract_authentication),
    }


def _normalize_parameters(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for parameter in dict_items(value):
        location = str(parameter.get("in") or "query")
        normalized.append(
            {
                "name": str(parameter.get("name") or "parameter"),
                "in": location,
                "required": True if location == "path" else bool(parameter.get("required", False)),
                "schema": parameter.get("schema")
                if isinstance(parameter.get("schema"), dict)
                else {"type": "string"},
            }
        )
    return normalized


def _normalize_authentication(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    roles = source.get("roles")
    return {
        "required": bool(source.get("required", False)),
        "roles": [str(role) for role in roles] if isinstance(roles, list) else [],
    }


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _find_endpoint(
    contracts: list[dict[str, Any]], endpoint_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for contract in contracts:
        for endpoint in dict_items(contract.get("endpoints")):
            if endpoint.get("id") == endpoint_id:
                return contract, endpoint
    return None, None
