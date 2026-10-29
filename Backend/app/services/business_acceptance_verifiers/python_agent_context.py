"""Agent 上下文和 checkpoint 调用证据：必须追踪真实 RuntimeContext，而非出现字段名。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import PythonEvidenceUnavailable, active_nodes
from app.services.business_acceptance_verifiers.python_endpoint import trusted_context_names
from app.services.business_acceptance_verifiers.python_agent_binding import factory_input, template_graph_argument
from app.services.business_acceptance_verifiers.python_runtime_identity import (
    frozen_pydantic_context, anonymous_context_evidence, template_thread_evidence,
)


def _attributes(program, unit, function, value, seen=()) -> set[tuple]:
    """有界追踪赋值及格式化键中实际读取的上下文属性，不执行表达式。"""

    if value is None or len(seen) > 16:
        return set()
    if isinstance(value, ast.Name) and value.id not in seen:
        assigned = program.assignment(unit, value.id, function)
        if assigned is not None:
            return _attributes(program, unit, function, assigned, (*seen, value.id))
    result = set()
    if isinstance(value, ast.Attribute):
        parts, root = [], value
        while isinstance(root, ast.Attribute):
            parts.insert(0, root.attr)
            root = root.value
        if isinstance(root, ast.Name):
            result.add((root.id, *parts))
    for child in ast.iter_child_nodes(value):
        result.update(_attributes(program, unit, function, child, seen))
    return result


def _dict_field(program, unit, function, value, name):
    """从实际调用参数追踪固定字典键，动态展开不能当作可信配置。"""

    value = program.expand(unit, value, function)
    if not isinstance(value, ast.Dict) or any(key is None for key in value.keys):
        return None
    return next((child for key, child in zip(value.keys, value.values()) if program.value(unit, key, function) == name), None)


def graph_invocations(program) -> list[tuple]:
    """只识别显式 CompiledStateGraph 或当前 Agent 工厂创建的图实例调用。"""

    result = []
    for unit, function in program.functions.values():
        if unit.path != "agent-runtime/src/app/interaction/service.py":
            continue
        for call in program.calls(function):
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in {"invoke", "ainvoke", "astream", "astream_events"}:
                continue
            receiver = program.receiver_type(unit, function, call.func.value)
            known = receiver in {"langgraph.graph.state.CompiledStateGraph", "langgraph.graph.CompiledStateGraph"}
            if not known:
                known = template_graph_argument(program, unit, function, call)
            if isinstance(call.func.value, ast.Name):
                assigned = program.expand(unit, call.func.value, function)
                if isinstance(assigned, ast.Call):
                    creator = program.functions.get(program.symbol(unit, assigned.func))
                    if creator:
                        known = any(program.symbol(creator[0], returned.func) in {
                            "deepagents.create_deep_agent", "deepagents.graph.create_deep_agent",
                        } for returned in program.return_calls(*creator))
            if known:
                result.append((unit, function, call))
    return result


def verify_context_wiring(program, config: dict) -> tuple[list, list]:
    """验证冻结上下文、Runtime 提供的 Principal 和图调用的实际 context 传参。"""

    errors, blocked = [], []
    record = program.classes.get("app.agent.context.RuntimeContext")
    if record is None:
        return [], ["模板没有可读取的 RuntimeContext 定义。"]
    owner, cls = record
    frozen = any(isinstance(decorator, ast.Call) and program.symbol(owner, decorator.func) == "dataclasses.dataclass"
                 and program.value(owner, program.keyword(decorator, "frozen")) is True for decorator in cls.decorator_list)
    frozen = frozen or frozen_pydantic_context(program, owner, cls)
    if not frozen:
        blocked.append("无法证明 RuntimeContext 为冻结且拒绝额外字段的上下文。")
    anonymous_verified = anonymous_context_evidence(program)
    constructors = []
    for unit, function in program.functions.values():
        if unit.path not in {"agent-runtime/src/app/interaction/service.py", "agent-runtime/src/app/agent/context.py"}:
            continue
        for call in program.calls(function):
            if program.symbol(unit, call.func) == "app.agent.context.RuntimeContext":
                principal = program.expand(unit, program.keyword(call, "principal"), function)
                attrs = _attributes(program, unit, function, principal)
                if not any(path[-2:] == ("state", "principal") for path in attrs):
                    blocked.append(f"{unit.path}:{call.lineno} Principal 未证明来自模板鉴权后的 request.state。")
                else:
                    constructors.append((unit, function, call))
    if not constructors and not anonymous_verified:
        blocked.append("未发现由模板请求状态创建的 RuntimeContext，不能接受客户端自报 Principal。")
    invocations = graph_invocations(program)
    if not invocations:
        blocked.append("未能在交互服务中定位实际 Agent 图调用。")
    for unit, function, call in invocations:
        context = program.keyword(call, "context")
        names = trusted_context_names(program, unit, function)
        valid = isinstance(context, ast.Name) and context.id in names
        expanded = program.expand(unit, context, function)
        if isinstance(expanded, ast.Call):
            valid = any(expanded is constructor for _, _, constructor in constructors)
        if not valid:
            blocked.append(f"{unit.path}:{call.lineno} 图调用未传入已证明可信的 RuntimeContext。")
    if config:
        blocked.append("context 包含额外正式配置，需逐项数据流适配。")
    return errors, blocked


def verify_memory_wiring(program, unit, function, factory, config: dict) -> tuple[list, list]:
    """核对持久化 checkpointer 工厂和实际 thread key 的 Principal/thread/namespace 隔离。"""

    errors, blocked = [], []
    unit, function, checkpointer = factory_input(program, unit, function, factory, "checkpointer")
    if checkpointer is None:
        return ["Agent memory 未传入模板 checkpointer。"], []
    if isinstance(checkpointer, ast.Await):
        checkpointer = checkpointer.value
    creator = program.symbol(unit, checkpointer.func) if isinstance(checkpointer, ast.Call) else ""
    record = program.functions.get(creator)
    if not creator.startswith("app.persistence.checkpointer.") or record is None:
        blocked.append("checkpointer 未绑定可追踪的模板持久化工厂。")
    else:
        owner, method = record
        calls = [program.symbol(owner, call.func) for call in program.calls(method)]
        if any(".MemorySaver" in symbol or ".InMemorySaver" in symbol for symbol in calls):
            errors.append("Memory 使用内存 Saver，不能满足持久化隔离合同。")
        if not any(symbol.startswith("langgraph.checkpoint.") and any(name in symbol for name in
                   ("SqliteSaver", "AsyncSqliteSaver", "PostgresSaver", "AsyncPostgresSaver")) for symbol in calls):
            blocked.append("无法证明 checkpointer 使用模板受支持的持久化 Saver。")
    invocations = graph_invocations(program)
    if not invocations:
        blocked.append("没有实际图调用，无法证明 checkpoint 隔离键被使用。")
    for owner, method, call in invocations:
        if template_thread_evidence(program, owner, method, call):
            continue
        run_config = program.keyword(call, "config")
        if run_config is None and len(call.args) > 1:
            run_config = call.args[1]
        configurable = _dict_field(program, owner, method, run_config, "configurable")
        thread = _dict_field(program, owner, method, configurable, "thread_id")
        attrs = _attributes(program, owner, method, thread)
        names = trusted_context_names(program, owner, method)
        if not any({(name, "namespace"), (name, "thread_id"), (name, "principal", "subject_id")} <= attrs for name in names):
            blocked.append(f"{owner.path}:{call.lineno} checkpoint.thread_id 未证明同时绑定可信 namespace、Principal 和 thread。")
    if config:
        blocked.append("memory 包含额外正式配置，需逐项验收持久化和生命周期策略。")
    return errors, blocked
