"""验证真实 DeepAgents 工厂七模块参数，不将合同副本或模块禁用声明当作实现。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import PythonProgram, PythonEvidenceUnavailable, UNKNOWN, active_nodes, result_for
from app.services.business_acceptance_verifiers.python_endpoint import trusted_context_names, call_receives_context, layer_call, route_records, service_identity
from app.services.business_acceptance_verifiers.python_agent_context import verify_memory_wiring, verify_context_wiring
from app.services.business_acceptance_verifiers.python_agent_binding import registered_factory, factory_input


def _factory(program: PythonProgram, expected: dict) -> tuple:
    """优先按当前静态注册表绑定正式 Agent；直接工厂仍要求唯一真实返回。"""

    registry_unit = next((unit for unit in program.units if unit.module == "app.agent.factory"), None)
    if registry_unit is not None and program.assignment(registry_unit, "_AGENT_BUILDERS") is not None:
        return registered_factory(program, expected)

    candidates = []
    for unit, function in program.functions.values():
        if unit.path != "agent-runtime/src/app/agent/factory.py":
            continue
        for call in program.return_calls(unit, function):
            if program.symbol(unit, call.func) in {"deepagents.create_deep_agent", "deepagents.graph.create_deep_agent"}:
                if sum(isinstance(node, ast.Return) for node in active_nodes(function)) != 1:
                    raise PythonEvidenceUnavailable("Agent 工厂有多个返回分支，无法唯一确认当前 Agent 的真实装配。")
                candidates.append((unit, function, call))
    if len(candidates) != 1 or expected["agent_count"] != 1:
        raise PythonEvidenceUnavailable("无法将当前 Agent 唯一绑定到实际返回的 DeepAgents 工厂；多 Agent 分派不能按字符串出现推断。")
    return candidates[0]


def _model(program, unit, function, factory, config, errors, blocked) -> None:
    """检查返回模型的工厂调用实际读取项目 Settings，不允许模型名字符串绕过注入。"""

    unit, function, node = factory_input(program, unit, function, factory, "model")
    if not isinstance(node, ast.Call):
        blocked.append("Agent 的 model 未绑定可解析的模型工厂调用。")
        return
    symbol = program.symbol(unit, node.func)
    record = program.functions.get(symbol)
    if record is None:
        blocked.append(f"Agent 模型工厂 {symbol} 不在声明的只读依赖范围。")
        return
    owner, creator = record
    constructors = [call for call in program.return_calls(owner, creator) if program.symbol(owner, call.func) in {
        "langchain_openai.ChatOpenAI", "langchain_anthropic.ChatAnthropic", "langchain.chat_models.init_chat_model",
        "app.models.anthropic_transport.RuntimeChatAnthropic",
    }]
    if not constructors:
        blocked.append("模型工厂没有实际返回受支持的模型构造，动态 provider 分派需专用适配。")
    for constructor in constructors:
        if program.symbol(owner, constructor.func) == "app.models.anthropic_transport.RuntimeChatAnthropic":
            adapter = program.classes.get("app.models.anthropic_transport.RuntimeChatAnthropic")
            if adapter is None or not any(program.symbol(adapter[0], base) == "langchain_anthropic.ChatAnthropic" for base in adapter[1].bases):
                blocked.append("Runtime Anthropic 适配器未继承实际 LangChain 模型。")
        for keyword in constructor.keywords:
            if keyword.arg in {"api_key", "anthropic_api_key", "base_url", "model", "model_name"}:
                value = program.value(owner, keyword.value, creator)
                if isinstance(value, str):
                    errors.append(f"{owner.path}:{constructor.lineno} 模型字段 {keyword.arg} 未继承当前项目配置。")
        settings_names = {arg.arg for arg in (*creator.args.args, *creator.args.kwonlyargs)
                          if program.symbol(owner, arg.annotation) == "app.settings.RuntimeSettings"}
        if not any(isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in settings_names
                   for node in active_nodes(creator)):
            blocked.append("无法证明模型构造使用模板 app.settings 的项目注入配置。")
    if set(config) != {"source"}:
        blocked.append("模型模块包含额外正式参数，需逐参数装配适配后才能通过。")


def _tools(program, unit, function, factory, expected, errors, blocked) -> None:
    """禁用时要求实际 tools=[]；启用时逐 Tool 检查执行函数和可信服务委托。"""

    config = expected["configuration"]
    expression = program.expand(unit, program.keyword(factory, "tools"), function)
    value = program.value(unit, expression, function) if expression is not None else None
    if not config["enabled"]:
        if value not in ([], None):
            if value is UNKNOWN:
                blocked.append("无法证明禁用模块实际装配为空 tools。")
            else:
                errors.append("tools.enabled=false 但工厂仍装配工具。")
        return
    if not isinstance(expression, (ast.List, ast.Tuple)):
        blocked.append("启用 Tools 需要可解析的显式工具列表；动态注册需适配。")
        return
    declared = {binding["toolId"] for binding in config["bindings"]}
    actual = {}
    for item in expression.elts:
        symbol = program.symbol(unit, item)
        record = program.functions.get(symbol)
        if record is None:
            blocked.append(f"工具 {symbol} 的实际函数不在声明依赖范围。")
            continue
        owner, tool = record
        tool_name = tool.name
        decorators = [decorator for decorator in tool.decorator_list if
                      program.symbol(owner, decorator.func if isinstance(decorator, ast.Call) else decorator)
                      in {"langchain_core.tools.tool", "langchain.tools.tool"}]
        if len(decorators) != 1:
            blocked.append(f"工具 {tool.name} 无法确认 @tool 适配。")
        elif isinstance(decorators[0], ast.Call) and decorators[0].args:
            explicit = program.value(owner, decorators[0].args[0])
            if isinstance(explicit, str):
                tool_name = explicit
        actual[tool_name] = (owner, tool)
    if set(actual) != declared:
        errors.append("实际工具列表与正式 Tool bindings 不一致，存在缺失或额外工具。")
    for binding in config["bindings"]:
        record = actual.get(binding["toolId"])
        if not record:
            continue
        owner, tool = record
        contexts = trusted_context_names(program, owner, tool)
        service_calls = [call for call in program.return_calls(owner, tool) if layer_call(program, owner, tool, call, "service")]
        if not any(call_receives_context(call, contexts) for call in service_calls):
            blocked.append(f"Tool {tool.name} 无法证明沿可信 RuntimeContext 调用正式 ApplicationService。")
        endpoint = next((endpoint for endpoint in expected.get("tool_endpoints") or []
                         if (endpoint["api_contract_id"], endpoint["endpoint_id"]) == (binding["apiContractId"], binding["endpointId"])), None)
        routes = route_records(program, {unit.path for unit in program.units})
        handlers = [record for record in routes if endpoint and record[:2] == (endpoint["method"], endpoint["path"])]
        if len(handlers) != 1:
            blocked.append(f"Tool {tool.name} 的正式 Endpoint 无法绑定到精确 Controller。")
            continue
        _, _, handler_unit, handler, _ = handlers[0]
        endpoint_services = {service_identity(program, handler_unit, handler, call)
                             for call in program.return_calls(handler_unit, handler)
                             if layer_call(program, handler_unit, handler, call, "service")}
        tool_services = {service_identity(program, owner, tool, call) for call in service_calls}
        if len(endpoint_services) != 1 or tool_services != endpoint_services:
            errors.append(f"Tool {tool.name} 未委托与正式 Endpoint 相同的唯一 ApplicationService 方法。")
        if endpoint["method"] not in {"GET", "HEAD", "OPTIONS"}:
            blocked.append(f"写 Tool {tool.name} 需证明显式确认后的授权控制流，不能以出现 interrupt 调用作为证明。")


def verify_agent_module(files: dict, expected: dict) -> dict:
    """逐模块检查真实装配，无法证明的动态 wiring/自然语言语义返回 blocked。"""

    program = PythonProgram({path: source for path, source in files.items() if path.endswith('.py')})
    unit, function, factory = _factory(program, expected)
    module, config = expected["module"], expected["configuration"]
    errors, blocked = [], []
    if module == "prompt":
        prompt = program.value(unit, program.keyword(factory, "system_prompt"), function)
        if prompt is UNKNOWN:
            blocked.append("system_prompt 为动态值，无法证明正式提示词实际传入工厂。")
        elif prompt != config["systemPrompt"]:
            errors.append("实际 system_prompt 与当前正式提示词不一致。")
        if set(config) != {"systemPrompt"}:
            blocked.append("提示词模块包含额外设置，尚无对应装配适配。")
    elif module == "model":
        _model(program, unit, function, factory, config, errors, blocked)
    elif module == "tools":
        _tools(program, unit, function, factory, expected, errors, blocked)
    elif module in {"skills", "knowledge"}:
        key = "skills" if module == "skills" else "middleware"
        node = program.keyword(factory, key)
        value = program.value(unit, node, function) if node is not None else None
        if config["enabled"]:
            blocked.append(f"启用 {module} 需要证明文件/检索器可达和实际调用，当前动态资源装配不能静态放行。")
        elif value not in (None, []):
            if value is UNKNOWN:
                blocked.append(f"无法证明禁用的 {module} 没有残留装配。")
            else:
                errors.append(f"{module}.enabled=false 但工厂仍有相关装配。")
        if module == "knowledge" and not config["enabled"] and value in (None, []):
            tools_expected = {**expected, "configuration": expected["tools_configuration"]}
            _tools(program, unit, function, factory, tools_expected, errors, blocked)
            # 所有实际工具必须属于正式业务 Tool 白名单；不能通过额外检索工具绕开禁用开关。
            for call in program.calls(function):
                if program.symbol(unit, call.func).startswith("app.knowledge."):
                    errors.append("knowledge.enabled=false 但工厂仍调用知识模块。")
        if set(config) != {"enabled"}:
            blocked.append(f"{module} 模块包含额外设置，需逐项验收。")
    elif module == "memory":
        errors, blocked = verify_memory_wiring(program, unit, function, factory, config)
    elif module == "context":
        context_schema = program.keyword(factory, "context_schema")
        if program.symbol(unit, context_schema) != "app.agent.context.RuntimeContext":
            blocked.append("工厂没有绑定可证明的模板 RuntimeContext。")
        context_errors, context_blocked = verify_context_wiring(program, config)
        errors.extend(context_errors)
        blocked.extend(context_blocked)
    else:
        blocked.append(f"未注册 Agent 模块：{module}。")
    return result_for(errors, blocked, facts={"agent_id": expected["agent_id"], "module": module,
                                            "factory": unit.module + "." + function.name, "file": unit.path, "line": factory.lineno})
