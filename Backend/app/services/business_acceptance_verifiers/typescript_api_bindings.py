"""根据 AST 实参绑定校验前端 API 的 Path、Query 和 Body 契约。"""

from typing import Any

from app.services.business_acceptance_verifiers.common import shape_matches


class UnsupportedBinding(ValueError):
    """表示表达式超出当前有界 AST 验证能力。"""


def bound_shape(value: dict, function: Any) -> dict | None:
    """将直接变量或对象字段引用解析到真实形参类型，并传播可选性。"""
    kind = value.get("kind")
    if kind == "missing":
        return None
    if kind == "reference":
        parts = value["parts"]
        parameter = next((item for item in function.parameters if item["name"] == parts[0]), None)
        if parameter is None:
            return None
        shape = {**(parameter["shape"] or {}), "required": parameter["required"]}
        for name in parts[1:]:
            field = shape.get("properties", {}).get(name)
            if not isinstance(field, dict):
                return None
            shape = {**field, "required": shape.get("required") is True and field.get("required") is True}
        return shape
    if kind == "object":
        properties = {}
        for name, field in value["fields"].items():
            shape = bound_shape(field, function)
            if shape is None:
                return None
            properties[name] = shape
        return {"type": "object", "properties": properties, "required": True}
    if kind == "literal":
        return {**value["shape"], "required": True}
    raise UnsupportedBinding("HTTP 实参包含无法追溯的表达式")


def property_binding(value: dict, name: str) -> dict:
    """从明确对象字段或配置形参的属性读取 params/data 绑定。"""
    if value.get("kind") == "object":
        return value["fields"].get(name, {"kind": "missing"})
    if value.get("kind") == "reference":
        return {"kind": "reference", "parts": [*value["parts"], name]}
    return value if value.get("kind") == "unsupported" else {"kind": "missing"}


def request_bindings(call: Any) -> tuple[dict, dict]:
    """按模板 service 的 Axios 风格签名确定请求体和查询配置的真实位置。"""
    args = call.argument_bindings
    missing = {"kind": "missing"}
    second = args[1] if len(args) > 1 else missing
    if call.method.casefold() in {"post", "put", "patch"}:
        config = args[2] if len(args) > 2 else missing
        return second, property_binding(config, "params")
    return property_binding(second, "data"), property_binding(second, "params")


def schema_matches(expected: dict, actual: dict | None) -> bool:
    """复用既有结构检查并补充 JSON Schema required 数组的必填约束。"""
    comparable = expected
    if expected.get("enum") and actual and actual.get("type") == "union":
        comparable = {key: value for key, value in expected.items() if key != "type"}
    if not actual or not shape_matches(comparable, actual):
        return False
    if isinstance(expected.get("enum"), list) and set(expected["enum"]) != set(actual.get("enum") or []):
        return False
    if expected.get("type") and not actual.get("type"):
        return False
    properties = actual.get("properties", {})
    required = expected.get("required")
    if isinstance(required, list) and any(properties.get(name, {}).get("required") is not True for name in required):
        return False
    for name, child in expected.get("properties", {}).items():
        if not schema_matches(child, properties.get(name)):
            return False
    if expected.get("items") and not schema_matches(expected["items"], actual.get("items")):
        return False
    return True


def parameter_contract_errors(endpoint: dict, function: Any, call: Any) -> list[str]:
    """对每项正式参数核对实际请求位置、来源、必填性和可推导类型。"""
    errors = []
    body, query = request_bindings(call)
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict) or not parameter.get("name"):
            continue
        name = parameter["name"]
        location = str(parameter.get("in") or "query").casefold()
        shape = None
        if location == "path":
            binding = next((item for item in call.url.get("bindings", []) if item["name"] == name), None)
            if binding:
                value = binding["expression"]
                shape = bound_shape(value.get("value", value), function)
        elif location in {"query", "body", "formdata"}:
            value = query if location == "query" else body
            shape = bound_shape(property_binding(value, name), function)
        else:
            raise UnsupportedBinding(f"不支持验证参数位置 {location}")
        if shape is None:
            errors.append(f"{function.export_symbol} 未将参数 {name}（{location}）绑定到实际 HTTP 请求。")
            continue
        if parameter.get("required") and shape.get("required") is not True:
            errors.append(f"{function.export_symbol} 将必填参数 {name} 声明为可选。")
        schema = parameter.get("schema") or {"type": parameter.get("type")}
        if not schema_matches(schema, shape):
            errors.append(f"{function.export_symbol} 的参数 {name}（{location}）类型不匹配。")
    return errors


def request_contract_errors(endpoint: dict, function: Any, call: Any) -> list[str]:
    """校验真实请求体类型；保留 GET 的既有查询对象 request_schema 语义。"""
    errors = parameter_contract_errors(endpoint, function, call)
    schema = endpoint.get("request_schema")
    if schema:
        body, query = request_bindings(call)
        actual = bound_shape(query if call.method.casefold() == "get" else body, function)
        if not schema_matches(schema, actual):
            errors.append(f"{function.export_symbol} 的请求类型不匹配 {endpoint.get('endpoint_id', '')}。")
    return errors
