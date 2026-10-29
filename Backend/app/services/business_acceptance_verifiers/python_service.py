"""Python 服务返回值与正式映射的有界数据流检查，不执行模型或外部资源。"""

import ast

from app.services.api_design_mapping_rules import mapping_sources
from app.services.business_acceptance_verifiers.python_ast import PythonProgram, UNKNOWN, active_nodes, result_for
from app.services.business_acceptance_verifiers.python_endpoint import call_receives_context, layer_call, trusted_context_names


def expression_path(node: ast.AST | None) -> tuple[str, ...]:
    """提取可证明的属性/常量下标链，不把任意表达式中的同名标识符当成映射。"""

    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*expression_path(node.value), node.attr)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return (*expression_path(node.value), node.slice.value)
    return ()


def returned_fields(program, unit, function) -> dict[str, ast.AST] | None:
    """只展开单一真实返回对象的字典/模型构造，遇动态合并或条件分支明确不给通过。"""

    values = [node.value for node in active_nodes(function) if isinstance(node, ast.Return)]
    if len(values) != 1:
        return None
    node = program.expand(unit, values[0], function)
    if isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Dict):
        keys = [program.value(unit, key, function) for key in node.keys]
        if all(isinstance(key, str) for key in keys):
            return dict(zip(keys, node.values))
    if isinstance(node, ast.Call) and program.symbol(unit, node.func) in program.classes:
        if any(keyword.arg is None for keyword in node.keywords):
            return None
        return {keyword.arg: keyword.value for keyword in node.keywords}
    return None


def _field_expression(program, unit, function, fields: dict, path: str) -> ast.AST | None:
    """逐层解析响应对象中的实际赋值；数组和动态转换交给 blocked。"""

    if "[]" in path:
        return None
    parts = path.split('.')
    node = fields.get(parts[0])
    for part in parts[1:]:
        node = program.expand(unit, node, function)
        if isinstance(node, ast.Dict):
            node = next((value for key, value in zip(node.keys, node.values)
                         if program.value(unit, key, function) == part), None)
        elif isinstance(node, ast.Call) and program.symbol(unit, node.func) in program.classes:
            node = program.keyword(node, part)
        else:
            return None
    return program.expand(unit, node, function)


def _source_reaches_call(program, unit, function, expression: ast.AST, layer: str) -> bool:
    """沿返回字段根变量追溯真实 Repository/Upstream 调用，防止固定响应和无用调用混淆。"""

    chain = expression_path(expression)
    if not chain:
        return False
    value = program.expand(unit, ast.Name(id=chain[0]), function)
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call) and layer_call(program, unit, function, value, layer)


def _mapping_errors(program, unit, function, fields: dict, design: dict) -> tuple[list, list]:
    """核对每个响应字段的固定值、请求引用或数据库/上游返回字段来源。"""

    errors, blocked = [], []
    arguments = {arg.arg for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)} - {"self"}
    for mapping in design.get("fieldMappings") or []:
        target = mapping["endpointField"]
        if target["side"] != "response":
            continue
        path = target["path"]
        expression = _field_expression(program, unit, function, fields, path)
        if expression is None:
            blocked.append(f"响应字段 {path} 无法追踪到明确返回赋值。")
            continue
        rule = mapping.get("right") or {}
        if mapping.get("mappingType") == "business_description" or rule.get("kind") == "business":
            blocked.append(f"响应字段 {path} 含自然语言业务规则，静态 AST 不能证明其语义。")
        elif rule.get("kind") == "fixed":
            actual = program.value(unit, expression, function)
            if actual is UNKNOWN:
                blocked.append(f"{path} 的固定值实现不是可解析常量。")
            elif type(actual) is not type(rule.get("value")) or actual != rule.get("value"):
                errors.append(f"响应字段 {path} 未使用正式固定值。")
        elif rule.get("kind") == "endpoint":
            chain = expression_path(expression)
            requested = tuple(rule["endpointField"]["path"].split('.'))
            if not chain or chain[0] not in arguments or chain[-len(requested):] != requested:
                errors.append(f"响应字段 {path} 未取自正式请求字段 {'.'.join(requested)}。")
        elif mapping.get("mappingType") == "source_mapping":
            sources = mapping_sources(mapping)
            if len(sources) != 1:
                blocked.append(f"响应字段 {path} 的多来源组合需要语义适配。")
                continue
            source = sources[0]
            leaf = source.get("column") if source.get("sourceType") == "database" else str(source.get("path") or "").split('.')[-1]
            layer = "repository" if source.get("sourceType") == "database" else "upstream"
            chain = expression_path(expression)
            if not chain or chain[-1] != leaf:
                errors.append(f"响应字段 {path} 未从正式来源字段 {leaf} 赋值。")
            elif not _source_reaches_call(program, unit, function, expression, layer):
                blocked.append(f"响应字段 {path} 的来源变量无法回溯到本次 {layer} 调用。")
        else:
            blocked.append(f"响应字段 {path} 没有受支持的正式取值规则。")
    return errors, blocked


def verify_python_service(files: dict, expected: dict) -> dict:
    """核对唯一服务操作、可信上下文委托、事务作用域以及实际返回字段映射。"""

    program = PythonProgram(files)
    targets = set(expected["_target_paths"])
    methods = [(unit, function) for symbol, (unit, function) in program.functions.items()
               if unit.path in targets and (".application." in symbol or ".services." in symbol)
               and not function.name.startswith('_')]
    if len(methods) != 1:
        return result_for([], ["服务操作无法唯一绑定到当前 Endpoint；请声明精确服务文件或补充调用适配，不能按名称猜测。"])
    unit, function = methods[0]
    contexts = trusted_context_names(program, unit, function)
    errors, blocked = [], []
    if not contexts:
        blocked.append(f"{unit.path}:{function.lineno} 缺少可证明的 RuntimeContext 输入。")
    calls = program.calls(function)
    for operation in expected.get("operations") or []:
        repository_calls = [call for call in calls if layer_call(program, unit, function, call, "repository")]
        if operation.get("requires_repository") and not any(call_receives_context(call, contexts) for call in repository_calls):
            errors.append(f"{operation['endpoint_id']} 未向 Repository 传递可信上下文。")
        if operation.get("transaction_required"):
            transactions = [node for node in active_nodes(function) if isinstance(node, (ast.With, ast.AsyncWith))
                            and any(isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Attribute)
                                    and item.context_expr.func.attr in {"transaction", "begin"} for item in node.items)]
            if not any(call in list(ast.walk(transaction)) for transaction in transactions for call in repository_calls):
                blocked.append(f"{operation['endpoint_id']} 无法证明写入位于模板事务作用域内。")
    fields = returned_fields(program, unit, function)
    if fields is None:
        blocked.append(f"{unit.path}:{function.lineno} 返回链包含动态转换/多分支，需专用数据流适配。")
    else:
        for design in expected.get("endpoint_designs") or []:
            current_errors, current_blocked = _mapping_errors(program, unit, function, fields, design)
            errors.extend(current_errors)
            blocked.extend(current_blocked)
            if design.get("implementationDescription"):
                blocked.append(f"{design['endpointId']} 含整体自然语言实现规则，需要行为测试证据，不能以 AST 名称匹配代替。")
    if not expected.get("endpoint_designs"):
        blocked.append("服务验收缺少当前正式 Endpoint Design。")
    return result_for(errors, blocked, facts={"service": unit.module + "." + function.name, "file": unit.path, "line": function.lineno})
