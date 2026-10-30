"""只放行可证明的写工具确认控制流，不以 interrupt 关键字充当授权证据。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import active_nodes


def _call_value(node):
    """展开 await，保留实际被执行的调用节点身份。"""

    return node.value if isinstance(node, ast.Await) else node


def tool_context_expressions(program, unit, tool):
    """只信任 LangChain 注入的 ToolRuntime[RuntimeContext]，普通工具形参不能自报身份。"""

    result = set()
    for argument in (*tool.args.args, *tool.args.kwonlyargs):
        annotation = argument.annotation
        if isinstance(annotation, ast.Subscript) and program.symbol(unit, annotation.value) in {
            "langchain.tools.ToolRuntime", "langgraph.prebuilt.ToolRuntime",
        } and program.symbol(unit, annotation.slice) == "app.agent.context.RuntimeContext":
            result.add(ast.dump(ast.Attribute(value=ast.Name(id=argument.arg, ctx=ast.Load()), attr="context", ctx=ast.Load())))
    return result


def write_confirmation_errors(program, unit, tool, service_calls, tool_id):
    """要求中断、严格布尔确认、失败终止和唯一服务委托依次发生，拒绝旁路与参数漂移。"""

    body = [statement for statement in tool.body if not (
        isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str))]
    reason = f"写 Tool {tool_id} 必须使用 interrupt → if approved is not True: raise → 唯一服务返回的确认控制流。"
    # 有意只支持这个最小控制流；循环、try/finally 和动态 helper 不能被静态猜测为安全。
    if len(body) != 3 or not isinstance(body[0], ast.Assign) or len(body[0].targets) != 1:
        return [reason]
    assignment, guard, returned = body
    target, interrupted = assignment.targets[0], _call_value(assignment.value)
    if not isinstance(target, ast.Name) or not isinstance(interrupted, ast.Call) \
            or program.symbol(unit, interrupted.func) != "langgraph.types.interrupt" \
            or len(interrupted.args) != 1 or interrupted.keywords:
        return [reason]
    test = guard.test if isinstance(guard, ast.If) else None
    if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == target.id
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot) and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is True
            and len(guard.body) == 1 and isinstance(guard.body[0], ast.Raise) and not guard.orelse):
        return [reason]
    raised = guard.body[0].exc
    if not (isinstance(raised, ast.Call) and isinstance(raised.func, ast.Name) and raised.func.id == "ValueError"
            and len(raised.args) == 1 and isinstance(raised.args[0], ast.Constant)
            and isinstance(raised.args[0].value, str) and not raised.keywords):
        return [reason]
    service = _call_value(returned.value) if isinstance(returned, ast.Return) else None
    if len(service_calls) != 1 or service is not service_calls[0]:
        return [reason]
    payload = program.expand(unit, interrupted.args[0], tool)
    if not isinstance(payload, ast.Dict) or any(key is None for key in payload.keys):
        return [f"写 Tool {tool_id} 的确认载荷必须是明确的 PendingInteraction。"]
    fields = {program.value(unit, key): value for key, value in zip(payload.keys, payload.values)}
    if set(fields) != {"schemaVersion", "interactionId", "kind", "message", "payload"} \
            or program.value(unit, fields["schemaVersion"]) != 1 \
            or program.value(unit, fields["kind"]) != "confirmation" \
            or not isinstance(program.value(unit, fields["message"]), str) \
            or not program.value(unit, fields["message"]).strip():
        return [f"写 Tool {tool_id} 的确认载荷不符合当前 PendingInteraction 合同。"]
    interaction_id = fields["interactionId"]
    if not (isinstance(interaction_id, ast.Attribute) and interaction_id.attr == "hex"
            and isinstance(interaction_id.value, ast.Call)
            and program.symbol(unit, interaction_id.value.func) == "uuid.uuid4"):
        return [f"写 Tool {tool_id} 必须为本次确认创建独立 interactionId。"]
    detail = fields["payload"]
    if not isinstance(detail, ast.Dict) or any(key is None for key in detail.keys):
        return [f"写 Tool {tool_id} 缺少可审阅的实际操作参数。"]
    detail_fields = {program.value(unit, key): value for key, value in zip(detail.keys, detail.values)}
    if set(detail_fields) != {"toolId", "arguments"} or program.value(unit, detail_fields["toolId"]) != tool_id:
        return [f"写 Tool {tool_id} 确认载荷没有绑定当前工具身份。"]
    # 排除可信上下文后，确认框必须逐项展示即将提交的原始实参，不能展示一份再执行另一份。
    contexts = tool_context_expressions(program, unit, tool)
    actual = [value for value in [*service.args, *(keyword.value for keyword in service.keywords)]
              if ast.dump(value) not in contexts]
    arguments = detail_fields["arguments"]
    if not isinstance(arguments, ast.Dict) or any(key is None for key in arguments.keys) \
            or len(arguments.values) != len(actual) \
            or sorted(ast.dump(value) for value in arguments.values) != sorted(ast.dump(value) for value in actual):
        return [f"写 Tool {tool_id} 确认展示的参数与实际服务调用不一致。"]
    # 动态参数求值可能含副作用；唯一写调用只能位于确认后的 return 内。
    inputs = {argument.arg for argument in (*tool.args.args, *tool.args.kwonlyargs)}
    for value in actual:
        if not isinstance(value, ast.Name) or value.id not in inputs or value.id == target.id:
            return [f"写 Tool {tool_id} 需直接传递已绑定的参数，确认后不能重新计算操作内容。"]
    if any(isinstance(node, (ast.Call, ast.Await, ast.NamedExpr)) for node in active_nodes(guard.test)):
        return [reason]
    return []
