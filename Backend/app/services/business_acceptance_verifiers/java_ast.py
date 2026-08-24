"""基于 tree-sitter 的 Java/XML 有界结构提取。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from tree_sitter_language_pack import get_parser


class JavaAstError(ValueError):
    """表示 Java 或 XML 源码无法被当前 grammar 安全解析。"""


@dataclass
class JavaAnnotation:
    """记录 Java 注解名和其中的字符串字面量。"""

    name: str
    strings: list[str]
    text: str


@dataclass
class JavaCall:
    """记录一个 Java 方法调用的对象、方法和参数事实。"""

    object_name: str
    method: str
    arguments: str
    strings: list[str]


@dataclass
class JavaParameter:
    """记录方法参数类型、名称和参数注解。"""

    name: str
    type_name: str
    annotations: list[JavaAnnotation]


@dataclass
class JavaMethod:
    """记录 Java 方法签名、注解、调用和方法体标识符。"""

    name: str
    return_type: str
    annotations: list[JavaAnnotation]
    parameters: list[JavaParameter]
    calls: list[JavaCall]
    identifiers: set[str]
    literals: set[str]


@dataclass
class JavaField:
    """记录 Java 字段名称和声明类型。"""

    name: str
    type_name: str


@dataclass
class JavaType:
    """记录类、接口或枚举的结构化成员。"""

    kind: str
    name: str
    annotations: list[JavaAnnotation]
    fields: list[JavaField]
    methods: list[JavaMethod]
    enum_values: set[str] = field(default_factory=set)


@dataclass
class JavaAstModel:
    """汇总 Java/XML verifier 所需的结构化事实。"""

    types: list[JavaType]
    identifiers: set[str]
    literals: set[str]
    xml_tags: set[str]
    xml_operation_ids: dict[str, set[str]]


def inspect_java_sources(files: dict[str, str]) -> JavaAstModel:
    """解析任务内 Java/XML 文件并提取分层业务事实。"""

    types: list[JavaType] = []
    identifiers: set[str] = set()
    literals: set[str] = set()
    xml_tags: set[str] = set()
    xml_operation_ids: dict[str, set[str]] = {}
    for path, text in files.items():
        source = text.encode("utf-8")
        language = "xml" if path.casefold().endswith(".xml") else "java"
        root = get_parser(language).parse(source).root_node
        if root.has_error:
            raise JavaAstError(f"{language.upper()} AST parse failed: {path}")
        if language == "xml":
            _collect_xml(root, source, xml_tags, xml_operation_ids, identifiers, literals)
            continue
        for node in _walk(root):
            if node.type in {"identifier", "type_identifier"}:
                identifiers.add(_text(node, source))
            if node.type in {"string_literal", "decimal_integer_literal", "true", "false", "null_literal"}:
                literals.add(_literal(node, source))
        types.extend(_java_types(root, source))
    return JavaAstModel(
        types=types,
        identifiers=identifiers,
        literals=literals,
        xml_tags=xml_tags,
        xml_operation_ids=xml_operation_ids,
    )


def _java_types(root: Any, source: bytes) -> list[JavaType]:
    """提取顶层与嵌套 Java 类型及其直接成员。"""

    result: list[JavaType] = []
    supported = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
    for node in _walk(root):
        if node.type not in supported:
            continue
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name is None or body is None:
            continue
        fields: list[JavaField] = []
        methods: list[JavaMethod] = []
        enum_values: set[str] = set()
        for member in body.named_children:
            if member.type in {"field_declaration", "constant_declaration"}:
                fields.extend(_fields(member, source))
            elif member.type in {"method_declaration", "constructor_declaration"}:
                methods.append(_method(member, source))
            elif member.type == "enum_constant":
                enum_name = member.child_by_field_name("name")
                if enum_name is not None:
                    enum_values.add(_text(enum_name, source))
        result.append(
            JavaType(
                kind=node.type.removesuffix("_declaration"),
                name=_text(name, source),
                annotations=_annotations(node, source),
                fields=fields,
                methods=methods,
                enum_values=enum_values,
            )
        )
    return result


def _fields(node: Any, source: bytes) -> list[JavaField]:
    """提取一个字段声明中的全部变量。"""

    type_node = node.child_by_field_name("type")
    type_name = _text(type_node, source) if type_node is not None else ""
    result: list[JavaField] = []
    for child in node.named_children:
        if child.type != "variable_declarator":
            continue
        name = child.child_by_field_name("name")
        if name is not None:
            result.append(JavaField(name=_text(name, source), type_name=type_name))
    return result


def _method(node: Any, source: bytes) -> JavaMethod:
    """提取方法签名、参数、调用和方法体事实。"""

    name = node.child_by_field_name("name")
    return_node = node.child_by_field_name("type")
    parameters_node = node.child_by_field_name("parameters")
    parameters: list[JavaParameter] = []
    if parameters_node is not None:
        for parameter in parameters_node.named_children:
            if parameter.type not in {"formal_parameter", "spread_parameter", "receiver_parameter"}:
                continue
            parameter_name = parameter.child_by_field_name("name")
            parameter_type = parameter.child_by_field_name("type")
            parameters.append(
                JavaParameter(
                    name=_text(parameter_name, source) if parameter_name is not None else "",
                    type_name=_text(parameter_type, source) if parameter_type is not None else "",
                    annotations=_annotations(parameter, source),
                )
            )
    calls: list[JavaCall] = []
    identifiers: set[str] = set()
    literals: set[str] = set()
    for child in _walk(node):
        if child.type in {"identifier", "type_identifier"}:
            identifiers.add(_text(child, source))
        if child.type in {"string_literal", "decimal_integer_literal", "true", "false", "null_literal"}:
            literals.add(_literal(child, source))
        if child.type == "method_invocation":
            object_node = child.child_by_field_name("object")
            method_node = child.child_by_field_name("name")
            arguments = child.child_by_field_name("arguments")
            calls.append(
                JavaCall(
                    object_name=_text(object_node, source) if object_node is not None else "",
                    method=_text(method_node, source) if method_node is not None else "",
                    arguments=_text(arguments, source) if arguments is not None else "",
                    strings=[_literal(item, source) for item in _walk(child) if item.type == "string_literal"],
                )
            )
    return JavaMethod(
        name=_text(name, source) if name is not None else "",
        return_type=_text(return_node, source) if return_node is not None else "",
        annotations=_annotations(node, source),
        parameters=parameters,
        calls=calls,
        identifiers=identifiers,
        literals=literals,
    )


def _annotations(node: Any, source: bytes) -> list[JavaAnnotation]:
    """读取声明 modifiers 中的直接注解。"""

    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    result: list[JavaAnnotation] = []
    if modifiers is None:
        return result
    for annotation in modifiers.named_children:
        if annotation.type not in {"annotation", "marker_annotation"}:
            continue
        name_node = annotation.child_by_field_name("name")
        if name_node is None:
            name_node = next(
                (child for child in annotation.named_children if child.type in {"identifier", "scoped_identifier"}),
                None,
            )
        result.append(
            JavaAnnotation(
                name=_text(name_node, source).rsplit(".", 1)[-1] if name_node is not None else "",
                strings=[_literal(item, source) for item in _walk(annotation) if item.type == "string_literal"],
                text=_text(annotation, source),
            )
        )
    return result


def _collect_xml(
    root: Any,
    source: bytes,
    tags: set[str],
    operation_ids: dict[str, set[str]],
    identifiers: set[str],
    literals: set[str],
) -> None:
    """从 XML AST 读取 Mapper 标签、id 属性和值字面量。"""

    for node in _walk(root):
        if node.type == "CharData":
            identifiers.update(re.findall(r"[A-Za-z_$][\w$]*", _text(node, source)))
        if node.type not in {"element", "self_closing_tag"}:
            continue
        start_tag = next(
            (child for child in node.named_children if child.type in {"STag", "EmptyElemTag"}),
            node if node.type == "self_closing_tag" else None,
        )
        if start_tag is None:
            continue
        name_node = next((child for child in start_tag.named_children if child.type == "Name"), None)
        if name_node is None:
            continue
        tag = _text(name_node, source).casefold()
        tags.add(tag)
        for attribute in (child for child in _walk(start_tag) if child.type == "Attribute"):
            parts = attribute.named_children
            if not parts:
                continue
            attribute_name = _text(parts[0], source).casefold()
            value = _literal(parts[-1], source)
            literals.add(value)
            if attribute_name == "id":
                operation_ids.setdefault(tag, set()).add(value)


def _walk(node: Any):
    """深度优先遍历具名 AST 节点。"""

    yield node
    for child in node.named_children:
        yield from _walk(child)


def _text(node: Any, source: bytes) -> str:
    """读取 AST 节点对应的 UTF-8 源码。"""

    return source[node.start_byte : node.end_byte].decode("utf-8")


def _literal(node: Any, source: bytes) -> str:
    """移除 Java/XML 简单字面量的外围引号。"""

    text = _text(node, source).strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"} else text
