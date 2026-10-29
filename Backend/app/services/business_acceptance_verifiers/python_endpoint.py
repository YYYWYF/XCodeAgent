"""FastAPI handler 级验收：method/path 必须绑定同一真实路由和服务返回链。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import PythonProgram, PythonEvidenceUnavailable, UNKNOWN, result_for
from app.services.business_acceptance_verifiers.python_shapes import python_shape, shape_errors


def trusted_context_names(program, unit, function) -> set[str]:
    """仅信任模板 RuntimeContext 注解，不把请求中的 owner/principal 字段当成可信身份。"""

    return {arg.arg for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
            if program.receiver_type(unit, function, ast.Name(id=arg.arg)) == "app.agent.context.RuntimeContext"}


def call_receives_context(call: ast.Call, names: set[str]) -> bool:
    """确认本次业务调用实参实际传递已识别的可信上下文。"""

    return any(isinstance(node, ast.Name) and node.id in names for value in
               [*call.args, *(keyword.value for keyword in call.keywords)] for node in ast.walk(value))


def layer_call(program, unit, function, call: ast.Call, layer: str) -> bool:
    """使用导入或接收者类型识别分层调用，不能仅靠方法名恰好包含 service/repository。"""

    symbol = program.symbol(unit, call.func)
    if isinstance(call.func, ast.Attribute):
        receiver = program.receiver_type(unit, function, call.func.value)
        symbol = receiver or symbol
    markers = {"service": (".application.", ".services."), "repository": (".repositories.", ".infrastructure."),
               "upstream": (".upstream.", ".clients.")}[layer]
    return symbol.startswith("app.") and any(marker in symbol for marker in markers)


def service_identity(program, unit, function, call: ast.Call) -> str:
    """将函数式和类型注入式服务调用统一为实际模块/类型/方法身份。"""

    if isinstance(call.func, ast.Attribute):
        receiver = program.receiver_type(unit, function, call.func.value)
        if receiver:
            return receiver + "." + call.func.attr
    return program.symbol(unit, call.func)


def route_records(program: PythonProgram, targets: set[str]) -> list[tuple]:
    """从 FastAPI/APIRouter 构造和装饰器解析绑定路由，支持显式常量 prefix。"""

    records = []
    for unit in program.units:
        if unit.path not in targets:
            continue
        routers = {}
        for statement in unit.tree.body:
            if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Call):
                continue
            call = statement.value
            if program.symbol(unit, call.func) not in {"fastapi.FastAPI", "fastapi.APIRouter", "fastapi.routing.APIRouter"}:
                continue
            prefix_node = program.keyword(call, "prefix")
            prefix = program.value(unit, prefix_node) if prefix_node is not None else ""
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    routers[target.id] = prefix
        for owner, function in program.functions.values():
            if owner is not unit:
                continue
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id not in routers:
                    continue
                method = decorator.func.attr.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "API_ROUTE"}:
                    continue
                path_node = decorator.args[0] if decorator.args else program.keyword(decorator, "path")
                path = program.value(unit, path_node)
                prefix = routers[decorator.func.value.id]
                if not isinstance(path, str) or not isinstance(prefix, str):
                    raise PythonEvidenceUnavailable(f"{unit.path}:{function.lineno} 的 FastAPI path/prefix 不是静态文本。")
                methods = program.value(unit, program.keyword(decorator, "methods")) if method == "API_ROUTE" else [method]
                if not isinstance(methods, list):
                    raise PythonEvidenceUnavailable("FastAPI api_route.methods 不是静态列表。")
                records.extend((str(verb).upper(), prefix.rstrip("/") + path, unit, function, decorator) for verb in methods)
    return records


def _parameter_errors(program, unit, function, endpoint) -> list[str]:
    """核对每个正式 Path/Query/Header 参数的绑定、类型和必填性。"""

    parameters = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    positional = [*function.args.posonlyargs, *function.args.args]
    defaults = {arg.arg: value for arg, value in zip(positional[-len(function.args.defaults):], function.args.defaults)} if function.args.defaults else {}
    defaults.update({arg.arg: value for arg, value in zip(function.args.kwonlyargs, function.args.kw_defaults) if value is not None})
    actual = []
    for arg in parameters:
        annotation, default = arg.annotation, defaults.get(arg.arg)
        marker = default if isinstance(default, ast.Call) else None
        if isinstance(annotation, ast.Subscript) and program.symbol(unit, annotation.value).endswith(".Annotated"):
            parts = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
            annotation = parts[0]
            marker = next((part for part in parts[1:] if isinstance(part, ast.Call)), marker)
        marker_name = program.symbol(unit, marker.func) if marker is not None else ""
        location = marker_name.removeprefix("fastapi.").lower() if marker_name in {"fastapi.Path", "fastapi.Query", "fastapi.Header"} else (
            "path" if "{" + arg.arg + "}" in endpoint["path"] else "query")
        alias = program.value(unit, program.keyword(marker, "alias")) if marker is not None else UNKNOWN
        name = alias if isinstance(alias, str) else arg.arg.replace("_", "-") if location == "header" else arg.arg
        marker_default = program.keyword(marker, "default") if marker is not None else None
        if marker_default is None and marker is not None and marker.args:
            marker_default = marker.args[0]
        required = default is None if marker is None else marker_default is None or isinstance(marker_default, ast.Constant) and marker_default.value is Ellipsis
        actual.append((name, location, required, annotation))
    errors = []
    for parameter in endpoint.get("parameters") or []:
        matches = [item for item in actual if item[0] == parameter["name"] and item[1] == parameter["in"]]
        if len(matches) != 1:
            errors.append(f"{endpoint['endpoint_id']} 缺少唯一的 {parameter['in']} 参数 {parameter['name']}。")
            continue
        _, _, required, annotation = matches[0]
        if bool(parameter.get("required")) != required:
            errors.append(f"参数 {parameter['name']} 必填性不一致。")
        errors.extend(shape_errors(parameter.get("schema") or {}, python_shape(program, unit, annotation), parameter["name"]))
    return errors


def verify_python_endpoint(files: dict, expected: dict) -> dict:
    """验证绑定到同一 handler 的路径、DTO、状态码及可信上下文到服务的返回链。"""

    program = PythonProgram(files)
    records = route_records(program, set(expected["_target_paths"]))
    errors, blocked, facts = [], [], []
    for endpoint in expected.get("endpoints") or []:
        matches = [record for record in records if record[:2] == (endpoint["method"], endpoint["path"])]
        if len(matches) != 1:
            if any(path.endswith(endpoint["path"]) or endpoint["path"].endswith(path) for _, path, *_ in records):
                blocked.append(f"{endpoint['method']} {endpoint['path']} 的完整路由挂载/prefix 无法唯一确认。")
            else:
                errors.append(f"未找到同一 FastAPI handler 绑定的 {endpoint['method']} {endpoint['path']}。")
            continue
        _, _, unit, function, decorator = matches[0]
        try:
            from app.services.business_acceptance_verifiers.python_ast import active_nodes
            if sum(isinstance(node, ast.Return) for node in active_nodes(function)) != 1:
                raise PythonEvidenceUnavailable(f"{unit.path}:{function.lineno} handler 存在多返回分支，需逐分支返回链适配。")
            errors.extend(_parameter_errors(program, unit, function, endpoint))
            response_type = program.keyword(decorator, "response_model") or function.returns
            if endpoint.get("response_schema"):
                errors.extend(shape_errors(endpoint["response_schema"], python_shape(program, unit, response_type), "response"))
            if endpoint.get("request_schema"):
                shapes = []
                for arg in (*function.args.args, *function.args.kwonlyargs):
                    if program.symbol(unit, arg.annotation) in program.classes:
                        shapes.append(python_shape(program, unit, arg.annotation, mode="request"))
                if not any(not shape_errors(endpoint["request_schema"], shape) for shape in shapes):
                    errors.append(f"{endpoint['endpoint_id']} 没有绑定与请求 Schema 一致的 Body DTO。")
            operation = next((item for item in expected.get("operations") or [] if item["endpoint_id"] == endpoint["endpoint_id"]), {})
            status_node = program.keyword(decorator, "status_code")
            status = program.value(unit, status_node) if status_node is not None else 200
            if operation.get("success_status_code") and status != operation["success_status_code"]:
                errors.append(f"{endpoint['endpoint_id']} 成功状态码与正式设计不一致。")
            calls = [call for call in program.return_calls(unit, function) if layer_call(program, unit, function, call, "service")]
            contexts = trusted_context_names(program, unit, function)
            if not contexts:
                blocked.append(f"{unit.path}:{function.lineno} 无法确认模板提供的 RuntimeContext。")
            elif not any(call_receives_context(call, contexts) for call in calls):
                errors.append(f"{unit.path}:{function.lineno} 返回链未委托携带可信上下文的 ApplicationService。")
            facts.append({"api_contract_id": endpoint["api_contract_id"], "endpoint_id": endpoint["endpoint_id"],
                          "method": endpoint["method"], "path": endpoint["path"], "handler": unit.module + "." + function.name,
                          "file": unit.path, "line": function.lineno})
        except PythonEvidenceUnavailable as exc:
            blocked.append(str(exc))
    if not expected.get("endpoints"):
        blocked.append("Endpoint 验收缺少正式接口。")
    return result_for(errors, blocked, facts={"endpoints": facts})
