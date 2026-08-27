"""后端领域映射检查使用的 Java 分层识别和转换证据提取工具。"""

from __future__ import annotations

import re
from typing import Any

from app.services.business_acceptance_verifiers.java_ast import (
    JavaAnnotation,
    JavaField,
    JavaMethod,
    JavaType,
)
from app.services.business_acceptance_verifiers.java_inspection_support import _java_type_matches


WRITE_METHODS = {"POST", "PUT", "PATCH"}


def type_role(java_type: JavaType) -> str:
    """结合类名、路径和 ORM 注解识别当前 Java 类型所属分层。"""

    name = java_type.name
    path = f"/{java_type.source_path.casefold()}/"
    if name.endswith(("Converter", "Assembler", "Mapper", "Adapter")):
        return "conversion"
    if name.endswith(("PO", "Po", "DO", "Do")) or "/po/" in path or _has_annotation(java_type, "TableName"):
        return "po"
    if name.endswith(("DTO", "Dto", "Request", "Response")) or "/dto/" in path:
        return "dto"
    if name.endswith("Entity") or "/domain/" in path or "/entity/" in path:
        return "entity"
    return "other"


def belongs_to_entity(java_type: JavaType, entity_id: str, entity_count: int) -> bool:
    """按规范化业务名关联类型，单实体检查允许 DTO 使用附加用途后缀。"""

    if entity_count == 1:
        return type_role(java_type) in {"entity", "po", "dto"}
    expected = canonical(entity_id)
    actual = canonical(re.sub(r"(?:Entity|PO|Po|DO|Do|DTO|Dto|Request|Response)$", "", java_type.name))
    return bool(expected and (actual.startswith(expected) or expected.startswith(actual)))


def find_role_field(types: list[JavaType], names: list[str]) -> JavaField | None:
    """在指定分层内按规范化属性名查找字段。"""

    expected = {canonical(name) for name in names if name}
    return next((field for item in types for field in item.fields if canonical(field.name) in expected), None)


def find_bound_po_field(types: list[JavaType], entity_field: str, column: str) -> JavaField | None:
    """优先按显式列注解查找 PO 字段，再使用当前 MyBatis-Plus 驼峰约定。"""

    explicit = next(
        (
            field
            for item in types
            for field in item.fields
            if annotation_value(field.annotations, "TableField") == column
        ),
        None,
    )
    return explicit or find_role_field(types, [entity_field, snake_to_camel(column)])


def column_mapping_present(types: list[JavaType], field: JavaField, column: str) -> bool:
    """验证列注解或 MyBatis-Plus TableName 类型上的默认驼峰列策略。"""

    explicit = annotation_value(field.annotations, "TableField")
    if explicit:
        return explicit == column
    return any(_has_annotation(item, "TableName") for item in types) and (
        canonical(field.name) == canonical(snake_to_camel(column))
    )


def conversion_edges(converters: list[JavaType], roles: dict[str, list[JavaType]]) -> list[dict[str, Any]]:
    """从转换方法签名和方法体提取带字段对的有向分层转换边。"""

    type_roles = {item.name: role for role, values in roles.items() for item in values}
    fields_by_type = {
        item.name: {field.name for field in item.fields}
        for values in roles.values()
        for item in values
    }
    edges: list[dict[str, Any]] = []
    for converter in converters:
        for method in converter.methods:
            source_name = simple_type(method.parameters[0].type_name) if method.parameters else ""
            target_name = simple_type(method.return_type)
            if source_name not in type_roles or target_name not in type_roles:
                continue
            mappings = _method_mappings(
                method,
                source_name,
                target_name,
                fields_by_type.get(source_name, set()),
                fields_by_type.get(target_name, set()),
            )
            edges.append(
                {
                    "method": f"{converter.name}.{method.name}",
                    "source_role": type_roles[source_name],
                    "target_role": type_roles[target_name],
                    "mappings": sorted([list(item) for item in mappings]),
                }
            )
    return edges


def require_edge(
    errors: list[str], entity_id: str, edges: list[dict[str, Any]], source_role: str,
    target_role: str, source_property: str, target_property: str, formal_field: str,
) -> None:
    """要求指定分层方向存在同一方法内的字段映射。"""

    found = any(
        edge["source_role"] == source_role
        and edge["target_role"] == target_role
        and any(
            canonical(pair[0]) == canonical(source_property)
            and canonical(pair[1]) == canonical(target_property)
            for pair in edge["mappings"]
        )
        for edge in edges
    )
    if not found:
        errors.append(f"实体 {entity_id} 缺少 {source_role}->{target_role} 字段转换 {formal_field}。")


def schema_entity_fields(endpoints: list[dict[str, Any]], entity_fields: set[str]) -> dict[str, str]:
    """递归读取请求和响应 Schema 中属于当前实体的字段类型。"""

    expected = {canonical(name): name for name in entity_fields}
    result: dict[str, str] = {}

    def visit(schema: Any) -> None:
        """递归遍历当前已解析 Schema 的对象和数组节点。"""

        if not isinstance(schema, dict):
            return
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for name, value in properties.items():
            if canonical(name) in expected and isinstance(value, dict):
                result[expected[canonical(name)]] = str(value.get("type") or "")
            visit(value)
        visit(schema.get("items"))

    for endpoint in endpoints:
        visit(endpoint.get("request_schema"))
        visit(endpoint.get("response_schema"))
    return result


def append_type_error(
    errors: list[str], entity_id: str, role: str, field: str, expected: Any, actual: str
) -> None:
    """把单个分层字段类型不一致追加为可修复错误。"""

    if actual and not _java_type_matches(expected, actual):
        errors.append(f"实体 {entity_id} 的 {role} 字段 {field} 类型 {actual} 不匹配正式类型 {expected}。")


def entity_facts(
    entity_id: str, roles: dict[str, list[JavaType]], edges: list[dict[str, Any]],
    fields: list[dict[str, Any]], bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """输出分层类型和映射边证据，供 Repair 精确定位。"""

    return {
        "entity_id": entity_id,
        "field_count": len(fields),
        "binding_count": len(bindings),
        "types": {role: [item.name for item in values] for role, values in roles.items()},
        "conversion_edges": edges,
    }


def conversion_mentions_types(converter: JavaType, type_names: set[str]) -> bool:
    """判断转换类型的方法签名是否连接当前实体的任一数据类型。"""

    return any(
        simple_type(method.return_type) in type_names
        or any(simple_type(parameter.type_name) in type_names for parameter in method.parameters)
        for method in converter.methods
    )


def annotation_value(annotations: list[JavaAnnotation], name: str) -> str:
    """读取指定注解的 value 参数或首个简写字符串参数。"""

    annotation = next((item for item in annotations if item.name == name), None)
    if annotation is None:
        return ""
    return annotation.arguments.get("value", "") or (annotation.strings[0] if annotation.strings else "")


def simple_type(type_name: str) -> str:
    """移除 Java 泛型、数组和包名前缀，获得可关联的简单类型名。"""

    text = re.sub(r"<.*>", "", str(type_name or "")).replace("[]", "")
    return text.rsplit(".", 1)[-1].strip()


def snake_to_camel(value: str) -> str:
    """把数据库 snake_case 列名转换为 Java 属性名。"""

    parts = str(value or "").split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:]) if parts else ""


def canonical(value: str) -> str:
    """生成仅用于确定性名称关联的大小写和分隔符无关键。"""

    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _method_mappings(
    method: JavaMethod, source_type: str, target_type: str,
    source_fields: set[str], target_fields: set[str],
) -> set[tuple[str, str]]:
    """联合 MapStruct、JavaBean、builder 和直接赋值提取单方法字段映射。"""

    mappings: set[tuple[str, str]] = set()
    mapstruct = any(annotation.name in {"Mapping", "Mappings"} for annotation in method.annotations)
    if mapstruct or not method.assignments and not method.calls:
        mappings.update(
            (source, target)
            for source in source_fields
            for target in target_fields
            if canonical(source) == canonical(target)
        )
    for annotation in method.annotations:
        if annotation.name == "Mapping":
            source = annotation.arguments.get("source", "")
            target = annotation.arguments.get("target", "")
            if source and target:
                mappings.add((source.rsplit(".", 1)[-1], target.rsplit(".", 1)[-1]))
        elif annotation.name == "Mappings":
            mappings.update(_nested_mapstruct_mappings(annotation.text))
    source_parameter = method.parameters[0].name if method.parameters else ""
    target_variables = {
        name for name, type_name in method.local_variables.items() if simple_type(type_name) == target_type
    }
    source_reads = {
        (_property_from_accessor(call.method), call.method)
        for call in method.calls
        if call.object_name == source_parameter and _property_from_accessor(call.method) in source_fields
    }
    for call in method.calls:
        target_property = ""
        if call.object_name in target_variables and call.method.startswith("set"):
            target_property = _property_from_accessor(call.method)
        elif call.method in target_fields and target_type in call.object_name:
            target_property = call.method
        if target_property:
            for source_property, accessor in source_reads:
                if accessor in call.arguments:
                    mappings.add((source_property, target_property))
    for left, right in method.assignments:
        target_match = re.search(r"\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\s*$", left)
        source_match = (
            re.search(rf"\b{re.escape(source_parameter)}\.([A-Za-z_$][\w$]*)", right)
            if source_parameter else None
        )
        if target_match and target_match.group(1) in target_variables and source_match:
            mappings.add((source_match.group(1), target_match.group(2)))
    return mappings


def _nested_mapstruct_mappings(text: str) -> set[tuple[str, str]]:
    """在已定位的 @Mappings 注解文本内提取成对 source/target 参数。"""

    return {
        (source.rsplit(".", 1)[-1], target.rsplit(".", 1)[-1])
        for source, target in re.findall(
            r"@Mapping\s*\(\s*source\s*=\s*\"([^\"]+)\"\s*,\s*target\s*=\s*\"([^\"]+)\"", text
        )
    }


def _property_from_accessor(method: str) -> str:
    """把 JavaBean getter/setter/is 方法名还原为属性名。"""

    match = re.fullmatch(r"(?:get|set|is)([A-Z].*)", method)
    return match.group(1)[:1].lower() + match.group(1)[1:] if match else method


def _has_annotation(java_type: JavaType, name: str) -> bool:
    """判断 Java 类型是否具有指定简单注解。"""

    return any(annotation.name == name for annotation in java_type.annotations)
