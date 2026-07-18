from __future__ import annotations

from typing import Any

from app.services.api_contracts import (
    dict_items,
    normalize_response_path,
    response_field_paths,
)


def validate_api_contract_consistency(project_plan: dict[str, Any]) -> list[str]:
    contracts = project_plan.get("api_contracts", [])
    errors: list[str] = []
    endpoint_index: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    schema_refs: set[str] = set()
    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        schemas = contract.get("schemas", {})
        if not schemas:
            errors.append(f"API contract {contract_id} does not define schemas.")
        for schema_id in schemas:
            schema_refs.add(f"{contract_id}#/schemas/{schema_id}")
        _validate_contract_schemas(contract_id, schemas, errors)
        _index_and_validate_endpoints(
            contract,
            endpoint_index=endpoint_index,
            errors=errors,
        )

    _validate_data_sources(project_plan, schema_refs, errors)
    _validate_page_api_dependencies(project_plan, endpoint_index, errors)
    _validate_page_bindings(project_plan, contracts, errors)
    return errors


def _validate_contract_schemas(
    contract_id: str,
    schemas: dict[str, Any],
    errors: list[str],
) -> None:
    for schema_id, schema in schemas.items():
        for ref in _collect_schema_refs(schema):
            if ref not in schemas:
                errors.append(
                    f"Schema {contract_id}.{schema_id} references unknown schema {ref}."
                )


def _index_and_validate_endpoints(
    contract: dict[str, Any],
    *,
    endpoint_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    errors: list[str],
) -> None:
    contract_id = str(contract.get("id") or "")
    schemas = contract.get("schemas", {})
    for endpoint in dict_items(contract.get("endpoints")):
        endpoint_id = str(endpoint.get("id") or "")
        if not endpoint_id:
            errors.append(f"API contract {contract_id} contains an endpoint without id.")
            continue
        if endpoint_id in endpoint_index:
            errors.append(f"Duplicate endpoint id: {endpoint_id}.")
        endpoint_index[endpoint_id] = (contract, endpoint)
        _validate_endpoint(endpoint_id, endpoint, schemas, errors)


def _validate_endpoint(
    endpoint_id: str,
    endpoint: dict[str, Any],
    schemas: dict[str, Any],
    errors: list[str],
) -> None:
    method = str(endpoint.get("method") or "")
    path = str(endpoint.get("path") or "")
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        errors.append(f"Endpoint {endpoint_id} uses unsupported method {method}.")
    if not path.startswith("/"):
        errors.append(f"Endpoint {endpoint_id} path must start with '/'.")
    declared_path_params = {
        str(parameter.get("name"))
        for parameter in dict_items(endpoint.get("parameters"))
        if parameter.get("in") == "path"
    }
    for path_param in _path_parameters(path):
        if path_param not in declared_path_params:
            errors.append(
                f"Endpoint {endpoint_id} does not declare path parameter {path_param}."
            )
    if method in {"POST", "PUT", "PATCH"} and not endpoint.get(
        "request_schema_ref"
    ):
        errors.append(f"Endpoint {endpoint_id} does not define request schema.")
    if method != "DELETE" and not endpoint.get("response_schema_ref"):
        errors.append(f"Endpoint {endpoint_id} does not define response schema.")
    for key in ("request_schema_ref", "response_schema_ref"):
        ref = endpoint.get(key)
        if ref and ref not in schemas:
            errors.append(f"Endpoint {endpoint_id} references unknown schema {ref}.")


def _validate_data_sources(
    project_plan: dict[str, Any],
    schema_refs: set[str],
    errors: list[str],
) -> None:
    for source in project_plan.get("data_sources", []):
        if source.get("schema"):
            errors.append(f"Data source {source.get('id')} duplicates contract fields in schema.")
        for ref in source.get("schema_refs", []):
            if ref not in schema_refs:
                errors.append(f"Data source {source.get('id')} references unknown schema {ref}.")


def _validate_page_api_dependencies(
    project_plan: dict[str, Any],
    endpoint_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    errors: list[str],
) -> None:
    for page_detail in project_plan.get("page_detail_plans", []):
        for api_dependency in dict_items(page_detail.get("api_dependencies")):
            endpoint_id = str(api_dependency.get("endpoint_id") or "")
            if endpoint_id not in endpoint_index:
                errors.append(
                    f"Page {page_detail.get('pageId')} references unknown endpoint {endpoint_id}."
                )


def _validate_page_bindings(
    project_plan: dict[str, Any],
    contracts: list[dict[str, Any]],
    errors: list[str],
) -> None:
    for page_detail in project_plan.get("page_detail_plans", []):
        endpoint_ids = {
            str(item.get("endpoint_id"))
            for item in dict_items(page_detail.get("api_dependencies"))
            if item.get("endpoint_id")
        }
        for binding in dict_items(page_detail.get("response_bindings")):
            endpoint_id = str(binding.get("endpoint_id") or "")
            source_path = normalize_response_path(binding.get("source_path"))
            if endpoint_id not in endpoint_ids:
                errors.append(
                    f"Page {page_detail.get('pageId')} binds undeclared endpoint {endpoint_id}."
                )
            elif source_path not in {
                normalize_response_path(path)
                for path in response_field_paths(contracts, endpoint_id)
            }:
                errors.append(
                    f"Page {page_detail.get('pageId')} binds unknown response field {source_path}."
                )


def _collect_schema_refs(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    refs = [str(schema["$ref"])] if schema.get("$ref") else []
    for value in schema.values():
        if isinstance(value, dict):
            refs.extend(_collect_schema_refs(value))
        elif isinstance(value, list):
            for item in value:
                refs.extend(_collect_schema_refs(item))
    return refs


def _path_parameters(path: str) -> list[str]:
    return [
        segment.split("}", 1)[0]
        for segment in path.split("{")[1:]
        if "}" in segment
    ]
