"""解析当前模板的静态 Agent 注册表和实际 AG-UI 装配调用，不执行工程代码。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import PythonEvidenceUnavailable, active_nodes, FUNCTIONS


DEEP_AGENT_FACTORIES = {"deepagents.create_deep_agent", "deepagents.graph.create_deep_agent"}


def scoped_value(program, unit, function, expression):
    """有界展开实际函数执行体中的唯一赋值，支持 try 内装配但拒绝重赋值。"""

    seen = set()
    while isinstance(expression, ast.Name) and expression.id not in seen:
        seen.add(expression.id)
        assignments = [node.value for node in active_nodes(function)
                       if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                           isinstance(target, ast.Name) and target.id == expression.id
                           for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
        if len(assignments) != 1:
            return program.expand(unit, expression, function)
        expression = assignments[0]
    return expression


def registered_factory(program, expected):
    """将正式 Agent 精确绑定到当前静态注册表中的唯一 DeepAgents Builder。"""

    unit = next((unit for unit in program.units if unit.module == "app.agent.factory"), None)
    if unit is None:
        raise PythonEvidenceUnavailable("模板 Agent 工厂文件不可读。")
    registry = program.assignment(unit, "_AGENT_BUILDERS")
    if not isinstance(registry, ast.Dict) or any(key is None for key in registry.keys):
        raise PythonEvidenceUnavailable("Agent 注册表必须是可解析的静态字典。")
    keys = [program.value(unit, key) for key in registry.keys]
    if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
        raise PythonEvidenceUnavailable("Agent 注册表有动态或重复身份。")
    if len(keys) != expected["agent_count"] or expected["agent_id"] not in keys:
        raise PythonEvidenceUnavailable("实际 Agent 注册表与当前正式 Agent 集合不一致。")
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id == "_AGENT_BUILDERS":
            raise PythonEvidenceUnavailable("Agent 注册表存在动态写入，无法静态绑定。")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "_AGENT_BUILDERS":
            raise PythonEvidenceUnavailable("Agent 注册表存在动态方法调用，需专用适配。")
    dispatcher = program.functions.get("app.agent.factory.create_agent")
    if dispatcher is None:
        raise PythonEvidenceUnavailable("缺少实际 Agent 分派函数。")
    returns = [node for node in active_nodes(dispatcher[1]) if isinstance(node, ast.Return)]
    call = returns[0].value if len(returns) == 1 else None
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Subscript)
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "_AGENT_BUILDERS"
            and isinstance(call.func.slice, ast.Name) and call.func.slice.id == "agent_id"):
        raise PythonEvidenceUnavailable("Agent 分派没有返回正式注册表对应的 Builder。")
    for name in ("model", "runtime_context", "checkpointer"):
        value = program.keyword(call, name)
        if not isinstance(value, ast.Name) or value.id != name:
            raise PythonEvidenceUnavailable(f"Agent 分派未原样传递 {name}。")
    record = program.functions.get(program.symbol(unit, registry.values[keys.index(expected["agent_id"])]))
    if record is None:
        raise PythonEvidenceUnavailable("Agent Builder 不在已声明源码范围。")
    owner, function = record
    returns = [node for node in active_nodes(function) if isinstance(node, ast.Return)]
    call = returns[0].value if len(returns) == 1 else None
    if not isinstance(call, ast.Call) or program.symbol(owner, call.func) not in DEEP_AGENT_FACTORIES:
        raise PythonEvidenceUnavailable("Agent Builder 没有唯一返回真实 DeepAgents 实例。")
    return owner, function, call


def agui_assembly(program):
    """定位实际返回的 AG-UI 内层 stream，不把未使用的嵌套示例当成装配证据。"""

    record = program.functions.get("app.server.agui.build_agent_runtime_stream")
    if record is None:
        raise PythonEvidenceUnavailable("缺少当前 AG-UI 装配入口源码。")
    unit, outer = record
    returned = [node.value for node in outer.body if isinstance(node, ast.Return)]
    if len(returned) != 1 or not isinstance(returned[0], ast.Call) or not isinstance(returned[0].func, ast.Name):
        raise PythonEvidenceUnavailable("无法绑定 AG-UI 实际返回的 stream。")
    inner = [node for node in outer.body if isinstance(node, FUNCTIONS) and node.name == returned[0].func.id]
    if len(inner) != 1:
        raise PythonEvidenceUnavailable("AG-UI stream 身份不唯一。")
    function = inner[0]
    creators = [node for node in program.calls(function) if program.symbol(unit, node.func) == "app.agent.factory.create_agent"]
    if len(creators) != 1:
        raise PythonEvidenceUnavailable("AG-UI 必须唯一装配当前 Agent。")
    return unit, function, creators[0]


def factory_input(program, unit, function, factory, name):
    """追踪 Builder 的转发参数到实际 AG-UI 模型/checkpointer 创建表达式。"""

    value = program.expand(unit, program.keyword(factory, name), function)
    if isinstance(value, ast.Name) and value.id == name:
        owner, caller, creator = agui_assembly(program)
        return owner, caller, scoped_value(program, owner, caller, program.keyword(creator, name))
    return unit, function, value


def template_graph_argument(program, unit, function, call):
    """证明交互函数中的 agent 形参来自同一个 AG-UI create_agent 结果和可信上下文。"""

    if unit.module != "app.interaction.service" or function.name != "stream_agent_interaction":
        return False
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != "agent":
        return False
    owner, caller, creator = agui_assembly(program)
    invocations = [node for node in program.calls(caller) if program.symbol(owner, node.func) == "app.interaction.service.stream_agent_interaction"]
    if len(invocations) != 1:
        return False
    invoked = invocations[0]
    graph = scoped_value(program, owner, caller, program.keyword(invoked, "agent"))
    context = program.keyword(invoked, "runtime_context")
    created_context = program.keyword(creator, "runtime_context")
    return (graph is creator and isinstance(context, ast.Name) and context.id == "runtime_context"
            and isinstance(created_context, ast.Name) and ast.dump(context) == ast.dump(created_context))
