from __future__ import annotations

from copy import deepcopy
from typing import Any


def dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalize_api_contracts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize compact business contracts without duplicating field definitions."""

    normalized: list[dict[str, Any]] = []
    for item in items:
        contract_id = str(item.get("id") or "resource_api")
        schemas = _normalize_schemas(item.get("schemas"))
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
    contract, endpoint = _find_endpoint(contracts, endpoint_id)
    if not contract or not endpoint:
        return []
    schema_ref = endpoint.get("response_schema_ref")
    schema = contract.get("schemas", {}).get(schema_ref)
    return _schema_paths(schema, contract.get("schemas", {}))


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
    if provided_bindings:
        return [
            {
                "endpoint_id": str(binding.get("endpoint_id") or ""),
                "source_path": normalize_response_path(binding.get("source_path")),
                "page_field": str(
                    binding.get("page_field")
                    or normalize_response_path(binding.get("source_path")).rsplit(
                        ".",
                        1,
                    )[-1]
                ),
            }
            for binding in provided_bindings
        ]
    normalized: list[dict[str, str]] = []
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


def _normalize_schemas(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(schema_id): _normalize_schema(schema)
        for schema_id, schema in value.items()
        if isinstance(schema, dict)
    }


def _normalize_schema(value: dict[str, Any]) -> dict[str, Any]:
    schema = {**value, "type": str(value.get("type") or "object")}
    if schema["type"] == "object":
        properties = value.get("properties")
        schema["properties"] = properties if isinstance(properties, dict) else {}
        required = value.get("required")
        schema["required"] = [str(item) for item in required] if isinstance(required, list) else []
    return schema


def _normalize_endpoint(
    endpoint: dict[str, Any],
    *,
    contract_id: str,
    base_path: str,
    contract_authentication: dict[str, Any],
) -> dict[str, Any]:
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
        "request_schema_ref": _optional_text(endpoint.get("request_schema_ref")),
        "response_schema_ref": _optional_text(endpoint.get("response_schema_ref")),
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


def _schema_paths(
    schema: Any,
    schemas: dict[str, dict[str, Any]],
    *,
    prefix: str = "",
) -> list[str]:
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return _schema_paths(schemas.get(ref), schemas, prefix=prefix)
    if schema.get("type") == "array":
        return _schema_paths(schema.get("items"), schemas, prefix=f"{prefix}[]")
    if schema.get("type") != "object":
        return [prefix] if prefix else []
    paths: list[str] = []
    for name, child in schema.get("properties", {}).items():
        child_prefix = f"{prefix}.{name}" if prefix else str(name)
        paths.append(child_prefix)
        child_paths = _schema_paths(child, schemas, prefix=child_prefix)
        paths.extend(path for path in child_paths if path != child_prefix)
    return paths
