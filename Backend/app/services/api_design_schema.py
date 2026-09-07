"""供 Endpoint 叶子字段枚举使用的本地组合 Schema 解析。"""

from typing import Any

from app.services.api_schema_refs import normalize_local_schema_ref


def resolve_mapping_schema(
    schema: dict[str, Any],
    schemas: dict[str, Any],
    visited_refs: frozenset[str],
) -> dict[str, Any]:
    """展开当前层引用和 allOf，合并必填集合并保留子字段的组合约束。"""

    parts: list[dict[str, Any]] = []
    reference = str(schema.get("$ref") or "").strip()
    if reference:
        schema_id = normalize_local_schema_ref(reference)
        if schema_id not in visited_refs and isinstance(schemas.get(schema_id), dict):
            resolved = resolve_mapping_schema(
                schemas[schema_id], schemas, visited_refs | {schema_id}
            )
            parts.append(resolved)
    for branch in schema.get("allOf") or []:
        if isinstance(branch, dict):
            resolved = resolve_mapping_schema(branch, schemas, visited_refs)
            parts.append(resolved)
    own = {key: value for key, value in schema.items() if key not in {"$ref", "allOf"}}
    # 在各分支自己的祖先链内解析子结构，兄弟分支复用同一 Schema 不属于递归。
    if isinstance(own.get("properties"), dict):
        own["properties"] = {
            name: resolve_mapping_schema(child, schemas, visited_refs) if isinstance(child, dict) else child
            for name, child in own["properties"].items()
        }
    if isinstance(own.get("items"), dict):
        own["items"] = resolve_mapping_schema(own["items"], schemas, visited_refs)
    parts.append(own)
    merged: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    required: set[str] = set()
    for part in parts:
        required.update(str(name) for name in part.get("required") or [])
        for name, child in (part.get("properties") or {}).items():
            # 同名字段的约束需叠加，不能用后一个分支覆盖前一个分支的结构。
            properties[name] = resolve_mapping_schema(
                {"allOf": [properties[name], child]}, schemas, visited_refs
            ) if name in properties else child
        if isinstance(part.get("items"), dict) and isinstance(merged.get("items"), dict):
            part = {**part, "items": resolve_mapping_schema(
                {"allOf": [merged["items"], part["items"]]}, schemas, visited_refs
            )}
        merged.update({key: value for key, value in part.items() if key not in {"properties", "required"}})
    if properties:
        merged["properties"] = properties
    if required:
        merged["required"] = sorted(required)
    return merged
