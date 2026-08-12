from __future__ import annotations

from typing import Any

from app.services.api_contracts import (
    dict_items,
    normalize_response_path,
    response_field_paths,
)
from app.services.api_schema_refs import normalize_local_schema_ref
from app.services.entity_definitions import (
    contract_data_source_id,
    normalize_entities,
    plan_data_sources,
)


def _string_items(value: Any) -> list[str]:
    """把未知值收窄为去重后的非空字符串列表。"""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def validate_api_contract_consistency(project_plan: dict[str, Any]) -> list[str]:
    """校验项目计划中的 API 契约、数据源和页面字段引用是否闭合。"""

    contracts = dict_items(project_plan.get("api_contracts"))
    data_sources = plan_data_sources(project_plan)
    errors: list[str] = []
    endpoint_index: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    schema_refs: set[str] = set()
    data_source_ids = {
        str(source.get("id")) for source in data_sources if source.get("id")
    }
    entity_ids_set = {
        str(entity.get("id") or "")
        for entity in normalize_entities(project_plan.get("entities"))
        if entity.get("id")
    }
    if data_sources and not contracts:
        errors.append("ProjectPlan defines data sources but api_contracts is empty.")
    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        entity_ids_list = _string_items(contract.get("entity_ids"))
        data_source_id = contract_data_source_id(project_plan, contract)
        if not contract_id:
            errors.append("API contract does not define id.")
        if data_sources and not entity_ids_list and not data_source_id:
            errors.append(f"API contract {contract_id} does not define entity_ids.")
        for entity_id in entity_ids_list:
            if entity_ids_set and entity_id not in entity_ids_set:
                errors.append(
                    f"API contract {contract_id} references unknown entity {entity_id}."
                )
        # 数据源只属于实体设计：未完成实体设计的契约允许暂缺 data_source_id，
        # 由接口详细设计入口强制先完成实体设计；已解析来源必须落在数据源清单内。
        if data_source_id and data_source_id not in data_source_ids:
            errors.append(
                f"API contract {contract_id} references unknown data source {data_source_id}."
            )
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
        if not dict_items(contract.get("endpoints")):
            errors.append(f"API contract {contract_id} does not define endpoints.")

    _validate_data_sources(project_plan, schema_refs, errors)
    _validate_page_api_dependencies(project_plan, endpoint_index, errors)
    _validate_page_bindings(project_plan, contracts, errors)
    return errors


def _validate_contract_schemas(
    contract_id: str,
    schemas: dict[str, Any],
    errors: list[str],
) -> None:
    """校验契约内所有 Schema 引用均能解析到同一契约。"""

    for schema_id, schema in schemas.items():
        for ref in _collect_schema_refs(schema):
            resolved_ref = normalize_local_schema_ref(ref, contract_id=contract_id)
            if resolved_ref not in schemas:
                errors.append(
                    f"Schema {contract_id}.{schema_id} references unknown schema {ref}."
                )


def _index_and_validate_endpoints(
    contract: dict[str, Any],
    *,
    endpoint_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    errors: list[str],
) -> None:
    """建立 Endpoint 索引并校验单个契约的 Endpoint。"""

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
        _validate_endpoint(
            contract_id,
            endpoint_id,
            endpoint,
            schemas,
            errors,
        )


def _validate_endpoint(
    contract_id: str,
    endpoint_id: str,
    endpoint: dict[str, Any],
    schemas: dict[str, Any],
    errors: list[str],
) -> None:
    """校验 Endpoint 方法、路径参数及 Schema 引用。"""

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
        resolved_ref = normalize_local_schema_ref(ref, contract_id=contract_id)
        if ref and resolved_ref not in schemas:
            errors.append(f"Endpoint {endpoint_id} references unknown schema {ref}.")


def _validate_data_sources(
    project_plan: dict[str, Any],
    schema_refs: set[str],
    errors: list[str],
) -> None:
    for source in plan_data_sources(project_plan):
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
        for endpoint_id in _page_endpoint_ids(page_detail):
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
        endpoint_ids = set(_page_endpoint_ids(page_detail))
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


def _page_endpoint_ids(page_detail: dict[str, Any]) -> list[str]:
    """统一读取页面详情的 endpoint 引用，不要求页面重复声明数据源依赖。"""

    references = (
        page_detail.get("references")
        if isinstance(page_detail.get("references"), dict)
        else {}
    )
    dependencies = [
        *dict_items(page_detail.get("endpoint_dependencies")),
        *dict_items(references.get("endpoint_dependencies")),
        *dict_items(page_detail.get("api_dependencies")),
    ]
    endpoint_ids: list[str] = []
    for dependency in dependencies:
        endpoint_id = str(dependency.get("endpoint_id") or "")
        if endpoint_id and endpoint_id not in endpoint_ids:
            endpoint_ids.append(endpoint_id)
    return endpoint_ids


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
