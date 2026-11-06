"""检查列表读取能否提供同一路径下记录操作所需的稳定 id。"""

from app.services.api_schema_refs import normalize_local_schema_ref


def _has_identity(schema, schemas: dict, contract_id: str, seen: frozenset) -> bool:
    """沿引用、数组和响应包络寻找记录身份，循环引用不能无限展开。"""

    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        ref = normalize_local_schema_ref(schema["$ref"], contract_id=contract_id)
        return ref not in seen and _has_identity(schemas.get(ref), schemas, contract_id, seen | {ref})
    if schema.get("type") == "array":
        return _has_identity(schema.get("items"), schemas, contract_id, seen)
    if any(_has_identity(child, schemas, contract_id, seen) for key in ("allOf", "anyOf", "oneOf")
            for child in (schema[key] if isinstance(schema.get(key), list) else [])):
        return True
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return False
    if any(name == "id" or (isinstance(field, dict) and str(field.get("entity_field_ref", "")).endswith(".id"))
            for name, field in properties.items()):
        return True
    return any(_has_identity(properties.get(key), schemas, contract_id, seen)
        for key in ("list", "items", "data", "records", "result", "content"))


def validate_record_identity_sources(contract: dict) -> list[str]:
    """当同一集合提供列表和路径 id 操作时，列表必须暴露可用于选择记录的身份。"""

    endpoints = contract.get("endpoints", [])
    targets = {endpoint["path"].split("/{id}", 1)[0].rstrip("/") for endpoint in endpoints
        if endpoint.get("method") != "GET" and "/{id}" in endpoint.get("path", "")}
    schemas = contract.get("schemas", {})
    contract_id = contract.get("id", "")
    errors = []
    for endpoint in endpoints:
        if endpoint.get("method") != "GET" or endpoint.get("path", "").rstrip("/") not in targets:
            continue
        ref = normalize_local_schema_ref(endpoint.get("response_schema_ref"), contract_id=contract_id)
        if ref in schemas and not _has_identity(schemas[ref], schemas, contract_id, frozenset({ref})):
            errors.append(f"Contract {contract_id} 的查询接口 {endpoint['id']} 未返回记录 id，无法为同一集合的路径 id 操作提供选择来源；请补齐读取 Schema 的记录身份。")
    return errors
