"""有界检查上游 HTTP 实际调用，动态协议与任意转换不得凭字面量通过。"""

import ast
import re
import tomllib

from app.services.business_acceptance_verifiers.python_ast import PythonProgram, UNKNOWN, result_for


def verify_python_upstream(files: dict, expected: dict) -> dict:
    """按当前正式 Operation 检查 method/path、配置来源、参数及失败状态处理。"""

    program = PythonProgram(files)
    errors, blocked, facts = [], [], []
    for operation in expected.get("external_apis") or []:
        info = operation.get("api_info") or {}
        matches = []
        for unit, function in program.functions.values():
            if unit.path not in expected["_target_paths"]:
                continue
            for call in program.calls(function):
                if not isinstance(call.func, ast.Attribute):
                    continue
                method = call.func.attr.upper()
                offset = 0
                if method == "REQUEST":
                    method = program.value(unit, call.args[0], function) if call.args else program.value(unit, program.keyword(call, "method"), function)
                    offset = 1
                if method != info.get("method"):
                    continue
                url_node = call.args[offset] if len(call.args) > offset else program.keyword(call, "url")
                url = program.value(unit, url_node, function)
                if url == info.get("path"):
                    matches.append((unit, function, call))
        if len(matches) != 1:
            blocked.append(f"上游 {operation.get('operation_id')} 无法唯一绑定到实际 HTTP method/path，动态 URL/封装需适配。")
            continue
        unit, function, call = matches[0]
        receiver = program.receiver_type(unit, function, call.func.value)
        if receiver not in {"httpx.Client", "httpx.AsyncClient", "requests.Session"}:
            blocked.append(f"{unit.path}:{call.lineno} 无法确认 HTTP 接收者类型。")
        if info.get("base_url_config_key"):
            blocked.append(f"上游 {operation.get('operation_id')} 的 Client Base URL 配置键尚不能从构造依赖证明。")
        for parameter in info.get("parameters") or []:
            if parameter.get("in") == "path":
                blocked.append("上游 Path 参数替换需要独立 URL 数据流证明。")
            else:
                argument = program.keyword(call, "params" if parameter.get("in") == "query" else "headers")
                argument = program.expand(unit, argument, function)
                keys = [program.value(unit, key, function) for key in argument.keys] if isinstance(argument, ast.Dict) else []
                if parameter.get("name") not in keys:
                    errors.append(f"上游缺少正式参数 {parameter.get('name')}。")
        def same_response(node):
            """确认状态检查和返回数据实际消费同一次 HTTP 调用结果。"""
            value = program.expand(unit, node, function)
            if isinstance(value, ast.Await):
                value = value.value
            return value is call

        if not any(isinstance(item.func, ast.Attribute) and item.func.attr == "raise_for_status"
                   and same_response(item.func.value) for item in program.calls(function)):
            blocked.append(f"{unit.path}:{function.lineno} 无法证明 HTTP 失败状态被处理。")
        if not any(isinstance(item.func, ast.Attribute) and item.func.attr == "json"
                   and same_response(item.func.value) for item in program.return_calls(unit, function)):
            blocked.append(f"{unit.path}:{function.lineno} 返回链未证明消费当前 HTTP 响应。")
        if operation.get("field_mappings") or operation.get("response_handling"):
            blocked.append(f"上游 {operation.get('operation_id')} 的响应转换/字段映射需要进一步数据流适配。")
        facts.append({"operation_id": operation.get("operation_id"), "file": unit.path, "line": call.lineno})
    if not expected.get("external_apis"):
        blocked.append("上游验收缺少正式 Operation 事实。")
    return result_for(errors, blocked, facts={"operations": facts})


def verify_python_bootstrap(files: dict, expected: dict) -> dict:
    """拒绝业务初始化自行建表或硬编码凭据，并要求实际运行配置装配证据。"""

    python_files = {path: source for path, source in files.items() if path.endswith('.py')}
    errors, blocked = [], []
    dependencies = set()
    for path, source in files.items():
        if path != "agent-runtime/pyproject.toml":
            continue
        try:
            project = tomllib.loads(source).get("project") or {}
        except ValueError as exc:
            return result_for([f"Runtime pyproject.toml 格式无效：{type(exc).__name__}。"], [])
        if "dependencies" in (project.get("dynamic") or []):
            blocked.append("动态依赖列表不能证明 Bootstrap 声明完整。")
        dependencies.update(re.split(r"[\s<>=!~\[;]", value, maxsplit=1)[0].lower().replace('_', '-')
                            for value in project.get("dependencies") or [] if isinstance(value, str))
    required = set()
    for design in expected.get("endpoint_designs") or []:
        source_type = (design.get("sourceBinding") or {}).get("sourceType")
        if source_type == "database":
            required.add("pymysql")
        elif source_type == "external_api":
            required.add("httpx")
    if required and "agent-runtime/pyproject.toml" not in files:
        blocked.append("Bootstrap 缺少当前工程 pyproject.toml 只读证据。")
    errors.extend(f"Runtime 缺少正式来源所需的依赖声明 {name}。" for name in required - dependencies)
    program = PythonProgram(python_files) if python_files else None
    for unit, function in program.functions.values() if program is not None else []:
        for call in program.calls(function):
            symbol = program.symbol(unit, call.func)
            if symbol.endswith((".create_all", ".drop_all")):
                errors.append(f"{unit.path}:{call.lineno} 初始化不得自动建表/删表。")
            for keyword in call.keywords:
                if keyword.arg in {"password", "api_key", "token"}:
                    value = program.value(unit, keyword.value, function)
                    if isinstance(value, str) and value:
                        errors.append(f"{unit.path}:{call.lineno} 不得硬编码连接或模型凭据。")
    return result_for(errors, blocked, facts={"declared_dependencies": sorted(dependencies),
                       "scope": "依赖声明和初始化禁止项；真实连接/事务/TLS 由后续 Runtime 启动检查验收"})
