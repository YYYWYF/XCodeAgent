"""基于 tree-sitter 的 TypeScript/TSX 有界结构提取。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tree_sitter_language_pack import get_parser

from app.services.business_acceptance_verifiers.typescript_ast_types import (
    annotation_value as _annotation_value,
    type_shape as _type_shape,
)


class TypeScriptAstError(ValueError):
    """表示源码无法被当前平台 AST 安全解析。"""


@dataclass
class TypeScriptCall:
    """记录函数体内一个可定位的调用表达式。"""

    callee: str
    object_name: str
    method: str
    arguments: list[str]
    path: str


@dataclass
class TypeScriptFunction:
    """记录导出函数的符号、签名、调用和类型结构。"""

    local_name: str
    export_symbol: str
    parameter_name: str = ""
    request_type: str = ""
    response_type: str = ""
    request_shape: dict[str, Any] | None = None
    response_shape: dict[str, Any] | None = None
    calls: list[TypeScriptCall] = field(default_factory=list)


@dataclass
class TypeScriptImport:
    """记录具名或命名空间 import 的本地绑定。"""

    module: str
    imported: str
    local: str
    namespace: bool = False


@dataclass
class TypeScriptAstModel:
    """汇总业务 verifier 所需的 TypeScript AST 事实。"""

    functions: list[TypeScriptFunction]
    imports_by_path: dict[str, list[TypeScriptImport]]
    calls_by_path: dict[str, list[str]]
    string_literals_by_path: dict[str, list[str]]
    identifiers: set[str]
    object_keys: set[str]
    literal_values: set[str]


@dataclass
class _ParsedSource:
    """保存单文件 AST 与原始字节，保证节点文本可稳定读取。"""

    path: str
    source: bytes
    root: Any


def inspect_typescript_sources(files: dict[str, str]) -> TypeScriptAstModel:
    """解析一组任务内 TypeScript 文件并提取可验证事实。"""

    parsed = [_parse_source(path, source) for path, source in files.items()]
    declarations = _type_declarations(parsed)
    string_constants = _string_constants(parsed)
    local_functions: dict[tuple[str, str], TypeScriptFunction] = {}
    export_aliases: dict[tuple[str, str], str] = {}
    imports_by_path: dict[str, list[TypeScriptImport]] = {}
    calls_by_path: dict[str, list[str]] = {}
    strings_by_path: dict[str, list[str]] = {}
    identifiers: set[str] = set()
    object_keys: set[str] = set()
    literal_values: set[str] = set()

    for item in parsed:
        imports_by_path[item.path] = _imports(item)
        calls_by_path[item.path] = []
        strings_by_path[item.path] = []
        for node in _walk(item.root):
            if node.type in {"identifier", "property_identifier", "type_identifier"}:
                identifiers.add(_node_text(node, item.source))
            if node.type in {"pair", "property_signature"}:
                key = node.child_by_field_name("key")
                if key is None:
                    key = node.child_by_field_name("name")
                if key is not None:
                    object_keys.add(_strip_literal(_node_text(key, item.source)))
            if node.type in {"string", "template_string", "number", "true", "false", "null"}:
                value = _literal_value(node, item.source)
                literal_values.add(value)
                if node.type in {"string", "template_string"}:
                    strings_by_path[item.path].append(value)
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                if function is not None:
                    calls_by_path[item.path].append(_node_text(function, item.source))
        for name, function in _local_functions(item, declarations, string_constants).items():
            local_functions[(item.path, name)] = function
        export_aliases.update({(item.path, local): alias for local, alias in _exports(item).items()})

    functions: list[TypeScriptFunction] = []
    for key, function in local_functions.items():
        export_symbol = export_aliases.get(key)
        if not export_symbol:
            continue
        function.export_symbol = export_symbol
        functions.append(function)
    return TypeScriptAstModel(
        functions=functions,
        imports_by_path=imports_by_path,
        calls_by_path=calls_by_path,
        string_literals_by_path=strings_by_path,
        identifiers=identifiers,
        object_keys=object_keys,
        literal_values=literal_values,
    )


def _parse_source(path: str, source: str) -> _ParsedSource:
    """按扩展名选择 TypeScript 或 TSX grammar，并拒绝错误语法树。"""

    encoded = source.encode("utf-8")
    language = "tsx" if path.casefold().endswith(".tsx") else "typescript"
    root = get_parser(language).parse(encoded).root_node
    if root.has_error:
        raise TypeScriptAstError(f"TypeScript AST parse failed: {path}")
    return _ParsedSource(path=path, source=encoded, root=root)


def _walk(node: Any):
    """深度优先遍历具名 AST 节点。"""

    yield node
    for child in node.named_children:
        yield from _walk(child)


def _node_text(node: Any, source: bytes) -> str:
    """读取节点对应的 UTF-8 源码。"""

    return source[node.start_byte : node.end_byte].decode("utf-8")


def _strip_literal(value: str) -> str:
    """移除简单字符串或模板字面量的外层引号。"""

    text = str(value or "").strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"} else text


def _literal_value(node: Any, source: bytes) -> str:
    """把 AST 字面量转换为稳定文本。"""

    return _strip_literal(_node_text(node, source))


def _type_declarations(parsed: list[_ParsedSource]) -> dict[str, tuple[Any, bytes]]:
    """收集任务文件内可解析的 interface 和 type 声明。"""

    result: dict[str, tuple[Any, bytes]] = {}
    for item in parsed:
        for node in _walk(item.root):
            if node.type not in {"interface_declaration", "type_alias_declaration"}:
                continue
            name = node.child_by_field_name("name")
            if name is not None:
                result[_node_text(name, item.source)] = (node, item.source)
    return result


def _string_constants(parsed: list[_ParsedSource]) -> dict[str, str]:
    """收集简单字符串常量，支持 API path 通过常量引用。"""

    result: dict[str, str] = {}
    for item in parsed:
        for node in _walk(item.root):
            if node.type != "variable_declarator":
                continue
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is None or value is None or value.type not in {"string", "template_string"}:
                continue
            result[_node_text(name, item.source)] = _literal_value(value, item.source)
    return result


def _exports(item: _ParsedSource) -> dict[str, str]:
    """提取本地符号到实际导出符号的映射。"""

    result: dict[str, str] = {}
    for statement in item.root.named_children:
        if statement.type != "export_statement":
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is not None:
            if declaration.type == "function_declaration":
                name = declaration.child_by_field_name("name")
                if name is not None:
                    local = _node_text(name, item.source)
                    result[local] = local
            elif declaration.type in {"lexical_declaration", "variable_declaration"}:
                for child in declaration.named_children:
                    if child.type == "variable_declarator":
                        name = child.child_by_field_name("name")
                        if name is not None:
                            local = _node_text(name, item.source)
                            result[local] = local
        for specifier in (node for node in _walk(statement) if node.type == "export_specifier"):
            name = specifier.child_by_field_name("name")
            alias = specifier.child_by_field_name("alias")
            if name is not None:
                local = _node_text(name, item.source)
                result[local] = _node_text(alias, item.source) if alias is not None else local
    return result


def _local_functions(
    item: _ParsedSource,
    declarations: dict[str, tuple[Any, bytes]],
    string_constants: dict[str, str],
) -> dict[str, TypeScriptFunction]:
    """提取函数声明和函数型变量，并关联签名与调用。"""

    result: dict[str, TypeScriptFunction] = {}
    for node in _walk(item.root):
        function_node = None
        name_node = None
        if node.type == "function_declaration":
            function_node = node
            name_node = node.child_by_field_name("name")
        elif node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in {"arrow_function", "function_expression"}:
                function_node = value
                name_node = node.child_by_field_name("name")
        if function_node is None or name_node is None:
            continue
        name = _node_text(name_node, item.source)
        parameter_name, request_type, request_shape = _function_parameter(
            function_node, item.source, declarations
        )
        response_type, response_shape = _function_response(
            function_node, item.source, declarations
        )
        result[name] = TypeScriptFunction(
            local_name=name,
            export_symbol=name,
            parameter_name=parameter_name,
            request_type=request_type,
            response_type=response_type,
            request_shape=request_shape,
            response_shape=response_shape,
            calls=_function_calls(function_node, item.source, string_constants),
        )
    return result


def _function_parameter(
    node: Any,
    source: bytes,
    declarations: dict[str, tuple[Any, bytes]],
) -> tuple[str, str, dict[str, Any] | None]:
    """读取函数第一个参数的名称、类型名和结构。"""

    parameters = node.child_by_field_name("parameters")
    if parameters is None or not parameters.named_children:
        return "", "", None
    parameter = parameters.named_children[0]
    pattern = parameter.child_by_field_name("pattern")
    type_node = _annotation_value(parameter.child_by_field_name("type"))
    name = _node_text(pattern, source) if pattern is not None else ""
    type_name = _node_text(type_node, source) if type_node is not None else ""
    return name, type_name, _type_shape(type_node, source, declarations, set())


def _function_response(
    node: Any,
    source: bytes,
    declarations: dict[str, tuple[Any, bytes]],
) -> tuple[str, dict[str, Any] | None]:
    """读取函数返回类型，并展开 Promise 的结果类型。"""

    type_node = _annotation_value(node.child_by_field_name("return_type"))
    if type_node is not None and type_node.type == "generic_type":
        name = type_node.child_by_field_name("name")
        if name is not None and _node_text(name, source) == "Promise":
            arguments = type_node.child_by_field_name("type_arguments")
            type_node = arguments.named_children[0] if arguments is not None and arguments.named_children else None
    type_name = _node_text(type_node, source) if type_node is not None else ""
    return type_name, _type_shape(type_node, source, declarations, set())


def _function_calls(
    node: Any,
    source: bytes,
    string_constants: dict[str, str],
) -> list[TypeScriptCall]:
    """提取函数体内的调用对象、method、参数和静态路径。"""

    calls: list[TypeScriptCall] = []
    for call in (item for item in _walk(node) if item.type == "call_expression"):
        function = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if function is None or arguments is None:
            continue
        object_name = method = ""
        if function.type == "member_expression":
            object_node = function.child_by_field_name("object")
            property_node = function.child_by_field_name("property")
            object_name = _node_text(object_node, source) if object_node is not None else ""
            method = _node_text(property_node, source) if property_node is not None else ""
        argument_nodes = list(arguments.named_children)
        argument_texts = [_node_text(argument, source) for argument in argument_nodes]
        path = ""
        if argument_nodes:
            first = argument_nodes[0]
            if first.type in {"string", "template_string"}:
                path = _literal_value(first, source)
            elif first.type == "identifier":
                path = string_constants.get(_node_text(first, source), "")
        calls.append(
            TypeScriptCall(
                callee=_node_text(function, source),
                object_name=object_name,
                method=method,
                arguments=argument_texts,
                path=path,
            )
        )
    return calls


def _imports(item: _ParsedSource) -> list[TypeScriptImport]:
    """提取具名、别名和命名空间 import。"""

    result: list[TypeScriptImport] = []
    for statement in item.root.named_children:
        if statement.type != "import_statement":
            continue
        source_node = statement.child_by_field_name("source")
        module = _literal_value(source_node, item.source) if source_node is not None else ""
        for specifier in _walk(statement):
            if specifier.type == "import_specifier":
                name = specifier.child_by_field_name("name")
                alias = specifier.child_by_field_name("alias")
                if name is not None:
                    imported = _node_text(name, item.source)
                    result.append(
                        TypeScriptImport(
                            module=module,
                            imported=imported,
                            local=_node_text(alias, item.source) if alias is not None else imported,
                        )
                    )
            elif specifier.type == "namespace_import":
                identifier = next((child for child in specifier.named_children if child.type == "identifier"), None)
                if identifier is not None:
                    local = _node_text(identifier, item.source)
                    result.append(TypeScriptImport(module=module, imported="*", local=local, namespace=True))
    return result
