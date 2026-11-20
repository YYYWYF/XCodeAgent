"""仅提取 API 验收所需的有界表达式、形参与 URL 绑定事实。"""

from typing import Any

from app.services.business_acceptance_verifiers.typescript_ast_types import annotation_value, type_shape


def declared_names(node: Any, source: bytes) -> set[str]:
    """保守收集值声明，避免局部同名变量或自定义编码函数冒充已知绑定。"""
    names = set()
    if node.type in {"required_parameter", "optional_parameter"}:
        binding = node.child_by_field_name("pattern")
    elif node.type in {"function_declaration", "variable_declarator", "class_declaration"}:
        binding = node.child_by_field_name("name")
    elif node.type == "arrow_function":
        binding = node.child_by_field_name("parameter")
    else:
        binding = None
    if binding is not None:
        if binding.type == "identifier":
            names.add(text(binding, source))
        else:
            names.update(text(child, source) for child in binding.named_children
                         if child.type in {"identifier", "shorthand_property_identifier_pattern"})
    for child in node.named_children:
        names.update(declared_names(child, source))
    return names


def static_url_constants(root: Any, function: Any, source: bytes) -> dict[str, str]:
    """仅绑定当前文件或当前函数直接声明的静态 const，排除跨作用域同名值。"""
    result = {}
    body = function.child_by_field_name("body")
    shadowed = declared_names(function, source)
    for container in (root, body):
        if container is None:
            continue
        for statement in container.named_children:
            declaration = statement.child_by_field_name("declaration") if statement.type == "export_statement" else statement
            if declaration is None or declaration.type != "lexical_declaration":
                continue
            if not any(child.type == "const" for child in declaration.children):
                continue
            for variable in declaration.named_children:
                name = variable.child_by_field_name("name")
                value = variable.child_by_field_name("value")
                if name is None or name.type != "identifier":
                    continue
                symbol = text(name, source)
                if container == root and symbol in shadowed:
                    continue
                binding = url_binding(value, source, {})
                if binding.get("supported") and not binding.get("bindings"):
                    result[symbol] = binding["template"]
    return result


def text(node: Any, source: bytes) -> str:
    """读取表达式节点对应的原文。"""
    return source[node.start_byte:node.end_byte].decode("utf-8")


def expression(node: Any, source: bytes) -> dict[str, Any]:
    """提取变量、静态成员、对象字段与编码调用，其他语法明确保留未知标记。"""
    if node is None:
        return {"kind": "missing"}
    if node.type in {"identifier", "shorthand_property_identifier"}:
        return {"kind": "reference", "parts": [text(node, source)]}
    if node.type == "member_expression":
        base = expression(node.child_by_field_name("object"), source)
        prop = node.child_by_field_name("property")
        if base["kind"] == "reference" and prop is not None and prop.type == "property_identifier":
            return {"kind": "reference", "parts": [*base["parts"], text(prop, source)]}
    if node.type == "object":
        fields = {}
        for child in node.named_children:
            if child.type == "comment":
                continue
            if child.type == "shorthand_property_identifier":
                fields[text(child, source)] = expression(child, source)
            elif child.type == "pair":
                key = child.child_by_field_name("key")
                if key is None or key.type not in {"property_identifier", "string"}:
                    return {"kind": "unsupported"}
                fields[text(key, source).strip("\"'")] = expression(child.child_by_field_name("value"), source)
            else:
                return {"kind": "unsupported"}
        return {"kind": "object", "fields": fields}
    if node.type == "call_expression":
        callee = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        values = [child for child in args.named_children if child.type != "comment"] if args else []
        if callee is not None and callee.type == "identifier" and text(callee, source) == "encodeURIComponent" and len(values) == 1:
            value = expression(values[0], source)
            if value["kind"] == "reference":
                return {"kind": "encoded", "value": value}
    if node.type in {"string", "number", "true", "false", "null"}:
        kind = {"true": "boolean", "false": "boolean"}.get(node.type, node.type)
        return {"kind": "literal", "shape": {"type": kind}}
    return {"kind": "unsupported"}


def function_parameters(node: Any, source: bytes, declarations: dict) -> list[dict[str, Any]]:
    """保留全部形参的位置、名称、类型和可选性，旧首参数投影由调用方保留。"""
    parameters = node.child_by_field_name("parameters")
    single = node.child_by_field_name("parameter")
    nodes = parameters.named_children if parameters else ([single] if single else [])
    result = []
    for parameter in nodes:
        if parameter.type == "comment":
            continue
        pattern = parameter if parameter.type == "identifier" else parameter.child_by_field_name("pattern")
        type_node = annotation_value(parameter.child_by_field_name("type"))
        result.append({
            "position": len(result), "name": text(pattern, source) if pattern else "",
            "type": text(type_node, source) if type_node else "",
            "shape": type_shape(type_node, source, declarations, set()),
            "required": parameter.type != "optional_parameter"
                and not any(child.type in {"?", "="} for child in parameter.children),
        })
    return result


def url_binding(node: Any, source: bytes, constants: dict[str, str]) -> dict[str, Any]:
    """从模板节点逐段构造路径，动态段仅接受明确变量或其内置编码调用。"""
    if node is None:
        return {"supported": False}
    if node.type == "identifier":
        value = constants.get(text(node, source))
        return {"supported": value is not None, "template": value, "bindings": []}
    if node.type == "string":
        # 转义路径不在当前静态表达式解释范围，避免把原文当成运行值。
        value = text(node, source)[1:-1]
        return {"supported": "\\" not in value, "template": value, "bindings": []}
    if node.type != "template_string":
        return {"supported": False}
    parts, bindings = [], []
    for child in node.named_children:
        if child.type == "string_fragment":
            parts.append(text(child, source))
        elif child.type == "template_substitution":
            values = [value for value in child.named_children if value.type != "comment"]
            value = expression(values[0], source) if len(values) == 1 else {"kind": "unsupported"}
            reference = value.get("value", value)
            if reference["kind"] != "reference":
                return {"supported": False}
            name = reference["parts"][-1]
            parts.append("{" + name + "}")
            bindings.append({"name": name, "expression": value})
        else:
            return {"supported": False}
    return {"supported": True, "template": "".join(parts), "bindings": bindings}
