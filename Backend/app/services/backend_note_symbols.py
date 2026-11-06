"""从实际 Java 声明生成可复验的模块说明符号摘要。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from typing import Any

from tree_sitter_language_pack import get_parser

from app.services.business_acceptance_verifiers.common import MAX_FILE_BYTES
from app.services.business_acceptance_verifiers.java_ast import _text, _walk


def java_source_path(root: Path, relative: str) -> Path:
    """只允许引用项目 Java 源码，拒绝路径穿越、盘符和跨根链接。"""
    if (
        not isinstance(relative, str) or not relative.startswith("backend/src/main/java/")
        or "\\" in relative or ":" in relative or ".." in PurePosixPath(relative).parts
        or not relative.endswith(".java")
    ):
        raise ValueError("实现说明必须引用项目内的 Java 源码。")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("实现说明引用的 Java 源码不可读取。")
    return path


def _type_name(node: Any, source: bytes, package: str) -> str:
    """组合包名和嵌套类型名，获得可唯一定位的 Java 类型。"""
    names = []
    while node is not None:
        if node.type in {"class_declaration", "interface_declaration", "enum_declaration"}:
            name = node.child_by_field_name("name")
            if name is not None:
                names.append(_text(name, source))
        node = node.parent
    return ".".join(part for part in (package, *reversed(names)) if part)


def _parameter_types(node: Any, source: bytes) -> list[str]:
    """提取 Java 重载参数类型，保留数组和可变参数声明。"""
    parameters = node.child_by_field_name("parameters")
    result = []
    for parameter in parameters.named_children if parameters is not None else ():
        if parameter.type not in {"formal_parameter", "spread_parameter"}:
            continue
        type_node = parameter.child_by_field_name("type")
        if type_node is None and parameter.type == "spread_parameter":
            type_node = next(
                (child for child in parameter.named_children if child.type not in {"modifiers", "variable_declarator"}),
                None,
            )
        if type_node is None:
            raise ValueError("无法读取 Java 参数类型。")
        declared = _text(type_node, source)
        dimensions = parameter.child_by_field_name("dimensions")
        if dimensions is not None:
            declared += _text(dimensions, source)
        if parameter.type == "spread_parameter":
            declared += "..."
        result.append(re.sub(r"\s+", "", declared))
    return result


def symbol_evidence(root: Path, locator: dict, cache: dict) -> dict:
    """校验真实签名并对本方法、类型声明和直接引用的 import 取摘要。"""
    path = java_source_path(root, locator.get("path", ""))
    if path not in cache:
        source = path.read_bytes()
        parsed = get_parser("java").parse(source).root_node
        if parsed.has_error:
            raise ValueError("Java 源码存在无法解析的语法。")
        cache[path] = source, parsed
    source, parsed = cache[path]
    package = next(
        (_text(child, source) for node in parsed.named_children if node.type == "package_declaration"
         for child in node.named_children if child.type in {"identifier", "scoped_identifier"}),
        "",
    )
    types = [node for node in _walk(parsed)
             if node.type in {"class_declaration", "interface_declaration", "enum_declaration"}
             and _type_name(node, source, package) == locator.get("type_name")]
    if len(types) != 1:
        raise ValueError("实现说明引用的 Java 类型不存在或不唯一。")
    node = types[0]
    body = node.child_by_field_name("body")
    if body is None:
        raise ValueError("实现说明引用的 Java 类型缺少声明体。")
    header = source[node.start_byte:body.start_byte].decode("utf-8")
    declarations = [header, *[_text(child, source) for child in body.named_children
                             if child.type in {"field_declaration", "constant_declaration", "enum_constant"}]]
    method_name = locator.get("method", "")
    parameters = [re.sub(r"\s+", "", item) for item in locator.get("parameter_types", [])]
    return_type = ""
    if method_name:
        methods = []
        for child in body.named_children:
            if child.type != "method_declaration":
                continue
            name = child.child_by_field_name("name")
            if name is not None and _text(name, source) == method_name and _parameter_types(child, source) == parameters:
                methods.append(child)
        if len(methods) != 1:
            raise ValueError("实现说明引用的 Java 方法签名不存在或不唯一。")
        declaration = methods[0]
        return_node = declaration.child_by_field_name("type")
        return_type = _text(return_node, source) if return_node is not None else ""
        declarations.append(_text(declaration, source))
    # 摘要不纳入其他方法或其独用的 import，后续追加业务方法不会让整份说明失效。
    used_names = set(re.findall(r"[\w$]+", "\n".join(declarations)))
    imports = []
    for item in parsed.named_children:
        if item.type != "import_declaration":
            continue
        declaration = _text(item, source)
        imported_name = declaration.rstrip(";").rsplit(".", 1)[-1]
        if imported_name == "*" or imported_name in used_names:
            imports.append(declaration)
    text = "\n".join([package, *sorted(imports), *declarations])
    return {"source_sha256": sha256(text.encode("utf-8")).hexdigest(), "return_type": return_type}
