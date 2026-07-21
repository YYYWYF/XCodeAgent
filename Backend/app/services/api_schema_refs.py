from __future__ import annotations

from typing import Any


def normalize_local_schema_ref(value: Any, *, contract_id: str = "") -> str:
    """把本契约内的 JSON Pointer 或限定引用转换为 Schema 裸名称。

    兼容三种 ref 写法：
    - OpenAPI 标准 ``#/components/schemas/<Schema>``（大模型常产出）
    - 本协议 ``#/schemas/<Schema>`` 与 ``<contract_id>#/schemas/<Schema>``
    - 裸 Schema 名 ``<Schema>``
    """

    ref = str(value or "").strip()
    # 同时兼容 #/components/schemas/（OpenAPI 标准）与 #/schemas/（本协议）。
    for marker in ("#/components/schemas/", "#/schemas/"):
        if marker not in ref:
            continue
        ref_contract_id, schema_id = ref.split(marker, 1)
        if ref_contract_id and contract_id and ref_contract_id != contract_id:
            return ref
        return schema_id or ref
    return ref


def normalize_schema_references(value: Any, *, contract_id: str) -> Any:
    """递归复制 Schema 结构，并只规范化 ``$ref`` 字段。"""

    if isinstance(value, dict):
        return {
            key: (
                normalize_local_schema_ref(item, contract_id=contract_id)
                if key == "$ref"
                else normalize_schema_references(item, contract_id=contract_id)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            normalize_schema_references(item, contract_id=contract_id)
            for item in value
        ]
    return value


def schema_field_paths(
    schema: Any,
    schemas: dict[str, dict[str, Any]],
    *,
    prefix: str = "",
    contract_id: str = "",
    visited_refs: frozenset[str] = frozenset(),
) -> list[str]:
    """递归展开 Schema 字段路径，并处理本地引用、组合结构和循环引用。"""

    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str):
        schema_id = normalize_local_schema_ref(ref, contract_id=contract_id)
        if schema_id in visited_refs:
            return []
        return schema_field_paths(
            schemas.get(schema_id),
            schemas,
            prefix=prefix,
            contract_id=contract_id,
            visited_refs=visited_refs | {schema_id},
        )
    if schema.get("type") == "array":
        return schema_field_paths(
            schema.get("items"),
            schemas,
            prefix=f"{prefix}[]",
            contract_id=contract_id,
            visited_refs=visited_refs,
        )
    paths: list[str] = []
    branches = schema.get("allOf")
    for branch in branches if isinstance(branches, list) else []:
        paths.extend(
            schema_field_paths(
                branch,
                schemas,
                prefix=prefix,
                contract_id=contract_id,
                visited_refs=visited_refs,
            )
        )
    if schema.get("type") != "object":
        return list(dict.fromkeys([*paths, *([prefix] if prefix else [])]))
    properties = schema.get("properties")
    for name, child in properties.items() if isinstance(properties, dict) else []:
        child_prefix = f"{prefix}.{name}" if prefix else str(name)
        paths.append(child_prefix)
        child_paths = schema_field_paths(
            child,
            schemas,
            prefix=child_prefix,
            contract_id=contract_id,
            visited_refs=visited_refs,
        )
        paths.extend(path for path in child_paths if path != child_prefix)
    return list(dict.fromkeys(paths))
