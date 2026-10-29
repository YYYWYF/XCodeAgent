"""按真实 Python 类型节点比较领域字段和 API Schema，不混用兄弟模型字段。"""

import ast
import re

from app.services.business_acceptance_verifiers.python_ast import PythonProgram, PythonEvidenceUnavailable, UNKNOWN, result_for


def python_shape(program: PythonProgram, unit, annotation, *, mode: str = "response", visited: tuple = ()) -> dict:
    """解析常用 Pydantic/dataclass 类型、嵌套数组、Literal、Enum 和别名；动态形态明确阻断。"""

    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        annotation = ast.parse(annotation.value, mode="eval").body
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        branches = [python_shape(program, unit, child, mode=mode, visited=visited) for child in (annotation.left, annotation.right)]
        non_null = [branch for branch in branches if branch.get("type") != "null"]
        if len(non_null) == 1:
            return {**non_null[0], "nullable": True}
        raise PythonEvidenceUnavailable("非空 Union 需要判别规则，不能静态选择其中一个响应形态。")
    name = program.symbol(unit, annotation)
    tail = name.rsplit(".", 1)[-1]
    primitives = {"str": "string", "int": "integer", "float": "number", "Decimal": "number", "bool": "boolean",
                  "datetime": "string", "date": "string", "UUID": "string"}
    if tail in primitives:
        return {"type": primitives[tail]}
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return {"type": "null"}
    if isinstance(annotation, ast.Subscript):
        outer = program.symbol(unit, annotation.value).rsplit(".", 1)[-1]
        args = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
        if outer == "Annotated":
            return python_shape(program, unit, args[0], mode=mode, visited=visited)
        if outer == "Optional":
            return {**python_shape(program, unit, args[0], mode=mode, visited=visited), "nullable": True}
        if outer in {"list", "List", "Sequence", "tuple", "Tuple"} and len(args) == 1:
            return {"type": "array", "items": python_shape(program, unit, args[0], mode=mode, visited=visited)}
        if outer == "Literal":
            values = [program.value(unit, value) for value in args]
            if all(value is not UNKNOWN for value in values):
                return {"type": "string" if all(isinstance(value, str) for value in values) else "integer", "enum": values}
        raise PythonEvidenceUnavailable(f"尚不能证明类型结构：{ast.unparse(annotation)}。")
    if name in visited or len(visited) >= 8:
        raise PythonEvidenceUnavailable("递归模型超出有界类型展开范围。")
    record = program.classes.get(name)
    if not record:
        raise PythonEvidenceUnavailable(f"声明依赖范围内缺少类型定义：{name or '<无注解>'}。")
    owner, cls = record
    bases = [program.symbol(owner, base) for base in cls.bases]
    if any(base in {"enum.Enum", "enum.StrEnum", "enum.IntEnum"} for base in bases):
        values = [program.value(owner, statement.value) for statement in cls.body if isinstance(statement, ast.Assign)]
        if not values or any(value is UNKNOWN for value in values):
            raise PythonEvidenceUnavailable(f"枚举 {cls.name} 的值不是显式常量。")
        return {"type": "string" if all(isinstance(value, str) for value in values) else "integer", "enum": values}
    properties = {}
    for base in cls.bases:
        symbol = program.symbol(owner, base)
        if symbol == "pydantic.BaseModel" or symbol.endswith(".BaseModel") and symbol.startswith("pydantic."):
            continue
        if symbol in program.classes:
            properties.update(python_shape(program, owner, base, mode=mode, visited=(*visited, name)).get("properties", {}))
        elif symbol not in {"builtins.object", owner.module + ".object"}:
            raise PythonEvidenceUnavailable(f"模型 {cls.name} 的基类无法在当前范围确认：{symbol}。")
    for statement in cls.body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "model_config" for target in statement.targets):
            if any(isinstance(node, ast.keyword) and node.arg == "alias_generator" for node in ast.walk(statement)):
                raise PythonEvidenceUnavailable(f"模型 {cls.name} 使用动态 alias_generator。")
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        if statement.target.id.startswith("_") or statement.target.id == "model_config":
            continue
        field_name = statement.target.id
        value = statement.value
        required = value is None or isinstance(value, ast.Constant) and value.value is Ellipsis
        if isinstance(value, ast.Call) and program.symbol(owner, value.func) == "pydantic.Field":
            alias_node = program.keyword(value, "serialization_alias" if mode == "response" else "validation_alias") or program.keyword(value, "alias")
            if alias_node is not None:
                alias = program.value(owner, alias_node)
                if not isinstance(alias, str):
                    raise PythonEvidenceUnavailable(f"{cls.name}.{field_name} 的字段别名不是固定文本。")
                field_name = alias
            default = program.keyword(value, "default")
            default = default if default is not None else value.args[0] if value.args else None
            required = ((default is None or isinstance(default, ast.Constant) and default.value is Ellipsis)
                        and program.keyword(value, "default_factory") is None)
        field = python_shape(program, owner, statement.annotation, mode=mode, visited=(*visited, name))
        properties[field_name] = {**field, "required": required}
    return {"type": "object", "properties": properties}


def shape_errors(expected: dict, actual: dict, path: str = "") -> list[str]:
    """逐层比较类型、字段、必填性和完整枚举，不能由其他模型补齐缺失字段。"""

    errors = []
    expected_type, actual_type = expected.get("type"), actual.get("type")
    if expected_type and expected_type != actual_type and not (expected_type == "number" and actual_type == "integer"):
        errors.append(f"{path or '对象'} 类型应为 {expected_type}，实际为 {actual_type}。")
    if "enum" in expected and set(expected["enum"]) != set(actual.get("enum") or []):
        errors.append(f"{path} 枚举值与正式设计不一致。")
    for key, descriptor in (expected.get("properties") or {}).items():
        name = f"{path}.{key}" if path else key
        field = (actual.get("properties") or {}).get(key)
        if field is None:
            errors.append(f"缺少字段 {name}。")
            continue
        if "required" in descriptor and descriptor["required"] != field.get("required"):
            errors.append(f"{name} 必填性不一致。")
        errors.extend(shape_errors(descriptor, field, name))
    if expected.get("items"):
        errors.extend(shape_errors(expected["items"], actual.get("items") or {}, path + "[]"))
    return errors


def verify_python_objects(files: dict, expected: dict) -> dict:
    """验证每个正式实体的独立类型；接口 DTO 在 handler 绑定处继续核对。"""

    program = PythonProgram({path: source for path, source in files.items() if path in expected["_target_paths"]})
    errors, blocked, models = [], [], {}
    for entity in expected.get("entities") or []:
        identity = re.sub(r"_", "", entity["entity_id"]).casefold()
        candidates = [(name, unit, cls) for name, (unit, cls) in program.classes.items()
                      if cls.name.replace("_", "").casefold() in {identity + suffix for suffix in ("", "entity", "model", "record")}]
        if len(candidates) != 1:
            blocked.append(f"实体 {entity['entity_id']} 无法唯一绑定到独立 Python 模型，不能合并多个类的字段。")
            continue
        name, unit, cls = candidates[0]
        fields = {}
        for field in entity.get("fields") or []:
            key = field.get("name") or field.get("path")
            type_name = {"text": "string", "longtext": "string", "datetime": "string", "date": "string",
                         "decimal": "number", "float": "number", "int": "integer", "enum": "string"}.get(field.get("type"), field.get("type"))
            fields[key] = {"type": type_name, "required": bool(field.get("required"))}
            if field.get("enum_values"):
                fields[key]["enum"] = field["enum_values"]
        shape = python_shape(program, unit, ast.Name(id=cls.name), mode="request")
        errors.extend(shape_errors({"type": "object", "properties": fields}, shape, entity["entity_id"]))
        models[name] = shape
    if not expected.get("entities") and not expected.get("endpoints"):
        blocked.append("Python 对象验收缺少正式实体或接口 Schema。")
    if not program.classes:
        errors.append("对象交付物没有独立的类型定义。")
    return result_for(errors, blocked, facts={"models": models})
