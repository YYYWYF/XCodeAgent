"""TypeScript AST 类型结构投射辅助函数。"""

from __future__ import annotations

from typing import Any


def annotation_value(node: Any | None) -> Any | None:
    """从 type_annotation 中取得实际类型节点。"""

    if node is None:
        return None
    return node.named_children[0] if node.type == "type_annotation" and node.named_children else node


def type_shape(
    node: Any | None,
    source: bytes,
    declarations: dict[str, tuple[Any, bytes]],
    visited: set[str],
) -> dict[str, Any] | None:
    """把受支持的 TypeScript 类型 AST 转为语言无关结构。"""

    if node is None:
        return None
    if node.type == "type_annotation":
        return type_shape(annotation_value(node), source, declarations, visited)
    if node.type == "predefined_type":
        return {"type": _text(node, source)}
    if node.type == "array_type":
        child = node.named_children[0] if node.named_children else None
        return {"type": "array", "items": type_shape(child, source, declarations, visited) or {}}
    if node.type == "generic_type":
        name = node.child_by_field_name("name")
        generic_name = _text(name, source) if name is not None else ""
        arguments = node.child_by_field_name("type_arguments")
        first = arguments.named_children[0] if arguments is not None and arguments.named_children else None
        if generic_name in {"Array", "ReadonlyArray"}:
            return {"type": "array", "items": type_shape(first, source, declarations, visited) or {}}
        if generic_name == "Promise":
            return type_shape(first, source, declarations, visited)
    if node.type in {"object_type", "interface_body"}:
        return {"type": "object", "properties": _object_properties(node, source, declarations, visited)}
    if node.type == "union_type":
        values = [
            _literal(child.named_children[0], source)
            for child in node.named_children
            if child.type == "literal_type" and child.named_children
        ]
        return {"type": "union", **({"enum": values} if values else {})}
    if node.type == "literal_type" and node.named_children:
        return {"type": "union", "enum": [_literal(node.named_children[0], source)]}
    if node.type == "type_identifier":
        name = _text(node, source)
        if name in visited or name not in declarations:
            return {"type": name}
        declaration, declaration_source = declarations[name]
        return _declaration_shape(declaration, declaration_source, declarations, {*visited, name})
    return {"type": _text(node, source)}


def _declaration_shape(
    declaration: Any,
    source: bytes,
    declarations: dict[str, tuple[Any, bytes]],
    visited: set[str],
) -> dict[str, Any] | None:
    """解析 interface/type 声明并合并 interface extends 字段。"""

    if declaration.type == "type_alias_declaration":
        value = declaration.child_by_field_name("value")
        if value is None:
            candidates = [
                child
                for child in declaration.named_children
                if child.type not in {"type_identifier", "type_parameters"}
            ]
            value = candidates[-1] if candidates else None
        return type_shape(value, source, declarations, visited)
    body = declaration.child_by_field_name("body")
    properties: dict[str, Any] = {}
    for child in declaration.named_children:
        if child.type != "extends_type_clause":
            continue
        for base in child.named_children:
            base_shape = type_shape(base, source, declarations, visited)
            if isinstance(base_shape, dict):
                properties.update(base_shape.get("properties") or {})
    if body is not None:
        properties.update(_object_properties(body, source, declarations, visited))
    return {"type": "object", "properties": properties}


def _object_properties(
    body: Any,
    source: bytes,
    declarations: dict[str, tuple[Any, bytes]],
    visited: set[str],
) -> dict[str, Any]:
    """读取对象或 interface 的属性结构与可选性。"""

    properties: dict[str, Any] = {}
    for child in body.named_children:
        if child.type != "property_signature":
            continue
        name = child.child_by_field_name("name")
        if name is None:
            continue
        field_shape = type_shape(child.child_by_field_name("type"), source, declarations, visited) or {}
        field_shape["required"] = not any(token.type == "?" for token in child.children)
        properties[_literal(name, source)] = field_shape
    return properties


def _text(node: Any, source: bytes) -> str:
    """读取类型 AST 节点对应源码。"""

    return source[node.start_byte : node.end_byte].decode("utf-8")


def _literal(node: Any, source: bytes) -> str:
    """移除类型属性或联合字面量的外围引号。"""

    text = _text(node, source).strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"} else text
