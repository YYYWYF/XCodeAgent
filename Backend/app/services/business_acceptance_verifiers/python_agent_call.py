"""验证业务服务真正委托正式 Agent 调用，不以配置文本出现替代实际返回链。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import active_nodes, result_for


def verify_agent_stream_handler(program, unit, function, endpoint: dict) -> list[str]:
    """证明 AG-UI 控制器解析标准输入并校验业务 DTO，响应 Schema 由服务映射校验。"""
    from app.services.business_acceptance_verifiers.python_shapes import python_shape, shape_errors
    calls = program.calls(function)
    parsers = [call for call in calls if program.symbol(unit, call.func) == "app.interaction.schemas.parse_run_request"]
    errors = []
    if len(parsers) != 1:
        errors.append("流式业务接口必须唯一使用模板 parse_run_request 解析官方 AG-UI 输入。")
    schemas = []
    for call in calls:
        if isinstance(call.func, ast.Attribute) and call.func.attr == "model_validate" and program.symbol(unit, call.func.value) in program.classes:
            if any(isinstance(node, ast.Attribute) and node.attr == "input" for arg in call.args for node in ast.walk(arg)):
                schemas.append(python_shape(program, unit, call.func.value, mode="request"))
    if endpoint.get("request_schema") and not any(not shape_errors(endpoint["request_schema"], schema) for schema in schemas):
        errors.append("流式业务接口未将标准请求 input 校验为正式业务 DTO。")
    return errors


def verify_agent_service_call(program, unit, function, contexts: set, expected: dict) -> dict:
    """只接受可信 ApplicationServiceContext 上的唯一直接返回及精确冻结映射。"""
    bindings = expected.get("agent_calls") or []
    if len(bindings) != 1:
        return result_for([], ["当前 Agent 调用服务必须唯一对应一份正式调用合同。"])
    binding = bindings[0]
    returns = [node.value for node in active_nodes(function) if isinstance(node, ast.Return)]
    expression = returns[0] if len(returns) == 1 else None
    if isinstance(expression, ast.Await):
        expression = expression.value
    method = "invoke_agent" if binding["binding"]["responseMode"] == "json" else "stream_agent"
    if not (isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute)
            and expression.func.attr == method and isinstance(expression.func.value, ast.Attribute)
            and expression.func.value.attr == "agents" and isinstance(expression.func.value.value, ast.Name)
            and expression.func.value.value.id in contexts):
        return result_for(["业务服务必须直接返回可信 context.agents 的正式调用结果，不能重建模型或伪造检测响应。"], [])
    errors = []
    for keyword, value in (("binding", binding["binding"]), ("response_schema", binding["responseSchema"])):
        if program.value(unit, program.keyword(expression, keyword), function) != value:
            errors.append(f"Agent 调用 {keyword} 与正式接口/能力合同不一致。")
    payload = program.expand(unit, program.keyword(expression, "payload"), function)
    arguments = {item.arg for item in (*function.args.args, *function.args.kwonlyargs)} - contexts - {"self"}
    if payload is None or not any(isinstance(node, ast.Name) and node.id in arguments for node in ast.walk(payload)):
        errors.append("Agent 调用 payload 未从本次业务请求参数取得。")
    return result_for(errors, [], facts={"service": unit.module + "." + function.name, "agentId": binding["binding"]["agentId"]})
