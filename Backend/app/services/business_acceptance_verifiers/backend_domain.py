"""基于 Java AST 的后端领域对象、持久化对象和 DTO 映射检查。"""

from __future__ import annotations

from typing import Any

from app.services.business_acceptance_verifiers.backend_domain_support import (
    WRITE_METHODS,
    annotation_value,
    append_type_error,
    belongs_to_entity,
    column_mapping_present,
    conversion_edges,
    conversion_mentions_types,
    entity_facts,
    find_bound_po_field,
    find_role_field,
    require_edge,
    schema_entity_fields,
    type_role,
)
from app.services.business_acceptance_verifiers.common import verification_result
from app.services.business_acceptance_verifiers.java_ast import JavaType
from app.services.business_acceptance_verifiers.java_inspection_support import _dict_items, _inspect_or_block


def verify_domain_mapping_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """按实体隔离验证 Entity、PO、DTO、表列绑定和逐字段转换链。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    entities = _dict_items(expected.get("entities"))
    if not entities:
        return verification_result("blocked", "没有可验证的完整 EntityDesign 输入。")
    data_types = [item for item in model.types if type_role(item) != "conversion"]
    conversion_types = [item for item in model.types if type_role(item) == "conversion"]
    errors: list[str] = []
    blockers: list[str] = []
    checked: list[dict[str, Any]] = []
    endpoints = _dict_items(expected.get("endpoints"))
    for entity in entities:
        entity_errors, entity_blockers, facts = _verify_entity(
            entity, endpoints, data_types, conversion_types, len(entities)
        )
        errors.extend(entity_errors)
        blockers.extend(entity_blockers)
        checked.append(facts)
    if blockers:
        return verification_result(
            "blocked", "；".join(blockers),
            facts={"reason_code": "domain_mapping_evidence_incomplete", "entities": checked},
        )
    if errors:
        return verification_result("failed", "；".join(errors), facts={"entities": checked})
    return verification_result(
        "passed", "已通过 AST 验证分层字段、API DTO、表列绑定和逐字段转换链。",
        facts={"entities": checked},
    )


def _verify_entity(
    expected: dict[str, Any], endpoints: list[dict[str, Any]], data_types: list[JavaType],
    conversion_types: list[JavaType], entity_count: int,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """验证单个正式实体，避免不同实体或不同分层之间互借字段证据。"""

    entity_id = str(expected.get("entity_id") or "")
    expected_fields = _dict_items(expected.get("fields"))
    bindings = _dict_items(expected.get("database_bindings"))
    related = [item for item in data_types if belongs_to_entity(item, entity_id, entity_count)]
    roles = {role: [item for item in related if type_role(item) == role] for role in ("entity", "po", "dto")}
    errors: list[str] = []
    blockers: list[str] = []
    for role, label in (("entity", "Entity"), ("po", "PO"), ("dto", "DTO")):
        if not roles[role]:
            errors.append(f"实体 {entity_id} 缺少 {label} 类型。")
    if errors:
        return errors, blockers, entity_facts(entity_id, roles, [], expected_fields, bindings)

    field_expectations = {str(item.get("name") or ""): item for item in expected_fields}
    if str(expected.get("data_source_type") or "").casefold() == "database":
        bound_fields = {str(item.get("entity_field") or "") for item in bindings}
        missing = sorted(set(field_expectations) - bound_fields)
        if missing:
            blockers.append(f"实体 {entity_id} 的正式数据库绑定缺少字段：{', '.join(missing)}。")
    dto_schema_types = schema_entity_fields(endpoints, set(field_expectations))
    entity_properties: dict[str, str] = {}
    po_properties: dict[str, str] = {}
    dto_properties: dict[str, str] = {}
    for name, field in field_expectations.items():
        match = find_role_field(roles["entity"], [name])
        if match is None:
            errors.append(f"实体 {entity_id} 的 Entity 缺少字段 {name}。")
        else:
            entity_properties[name] = match.name
            append_type_error(errors, entity_id, "Entity", name, field.get("type"), match.type_name)
    for binding in bindings:
        field_name = str(binding.get("entity_field") or "")
        column = str(binding.get("table_column") or "")
        if not field_name or not column:
            blockers.append(f"实体 {entity_id} 的正式数据库绑定缺少 entity_field 或 table_column。")
            continue
        match = find_bound_po_field(roles["po"], field_name, column)
        if match is None:
            errors.append(f"实体 {entity_id} 的 PO 缺少字段或列映射 {field_name} -> {column}。")
            continue
        po_properties[field_name] = match.name
        append_type_error(
            errors, entity_id, "PO", field_name,
            field_expectations.get(field_name, {}).get("type"), match.type_name,
        )
        if not column_mapping_present(roles["po"], match, column):
            errors.append(f"实体 {entity_id} 的 PO 字段 {match.name} 缺少数据库列映射 {column}。")
    dto_expected = dto_schema_types or {
        name: str(field.get("type") or "") for name, field in field_expectations.items()
    }
    for name, expected_type in dto_expected.items():
        match = find_role_field(roles["dto"], [name])
        if match is None:
            errors.append(f"实体 {entity_id} 的 DTO 缺少 API Schema 字段 {name}。")
        else:
            dto_properties[name] = match.name
            append_type_error(errors, entity_id, "DTO", name, expected_type, match.type_name)
    table = str(expected.get("database_table") or "")
    if table and not any(annotation_value(item.annotations, "TableName") == table for item in roles["po"]):
        errors.append(f"实体 {entity_id} 的 PO 缺少表映射 {table}。")

    related_names = {item.name for values in roles.values() for item in values}
    converters = [item for item in conversion_types if conversion_mentions_types(item, related_names)]
    if not converters:
        errors.append(f"实体 {entity_id} 缺少连接当前 Entity/PO/DTO 的转换层。")
        return errors, blockers, entity_facts(entity_id, roles, [], expected_fields, bindings)
    edges = conversion_edges(converters, roles)
    if not edges:
        blockers.append(f"实体 {entity_id} 的转换语法当前无法形成可验证的类型化映射边。")
        return errors, blockers, entity_facts(entity_id, roles, edges, expected_fields, bindings)
    read_required = any(str(item.get("method") or "GET").upper() == "GET" for item in endpoints)
    write_required = any(str(item.get("method") or "").upper() in WRITE_METHODS for item in endpoints)
    if (read_required or write_required) and edges and all(not edge["mappings"] for edge in edges):
        blockers.append(f"实体 {entity_id} 的转换语法当前无法形成可验证的逐字段映射证据。")
        return errors, blockers, entity_facts(entity_id, roles, edges, expected_fields, bindings)
    for name in field_expectations:
        entity_property = entity_properties.get(name)
        po_property = po_properties.get(name)
        dto_property = dto_properties.get(name)
        if read_required and po_property and entity_property:
            require_edge(errors, entity_id, edges, "po", "entity", po_property, entity_property, name)
        if read_required and entity_property and dto_property:
            require_edge(errors, entity_id, edges, "entity", "dto", entity_property, dto_property, name)
        if write_required and dto_property and entity_property:
            require_edge(errors, entity_id, edges, "dto", "entity", dto_property, entity_property, name)
        if write_required and entity_property and po_property:
            require_edge(errors, entity_id, edges, "entity", "po", entity_property, po_property, name)
    return errors, blockers, entity_facts(entity_id, roles, edges, expected_fields, bindings)


__all__ = ["verify_domain_mapping_source"]
