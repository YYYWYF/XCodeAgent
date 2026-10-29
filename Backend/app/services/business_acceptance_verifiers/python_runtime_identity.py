"""当前匿名 Runtime 的 Pydantic 上下文和签名恢复证据。"""

import ast

from app.services.business_acceptance_verifiers.python_ast import active_nodes
from app.services.business_acceptance_verifiers.python_agent_binding import scoped_value


def frozen_pydantic_context(program, owner, cls):
    """证明真实 BaseModel 配置拒绝额外字段并冻结上下文。"""

    if not any(program.symbol(owner, base) == "pydantic.BaseModel" for base in cls.bases):
        return False
    config = next((node.value for node in cls.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "model_config" for target in node.targets)), None)
    return (isinstance(config, ast.Call) and program.symbol(owner, config.func) == "pydantic.ConfigDict"
            and program.value(owner, program.keyword(config, "frozen")) is True
            and program.value(owner, program.keyword(config, "extra")) == "forbid")


def anonymous_context_evidence(program):
    """要求真实签名校验、namespace 检查和服务器生成身份，不能接受客户端 claims。"""

    resolver = program.functions.get("app.security.service.resolve_public_principal")
    verifier = program.functions.get("app.security.tokens.verify_anonymous_session")
    if resolver is None or verifier is None:
        return False
    token_unit, verify = verifier
    guards = [node for node in active_nodes(verify) if isinstance(node, ast.If)
              and any(isinstance(child, ast.Raise) for child in node.body)]
    signature_guard = any(isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)
                          and isinstance(node.test.operand, ast.Call)
                          and program.symbol(token_unit, node.test.operand.func) == "hmac.compare_digest" for node in guards)
    namespace_guard = any(isinstance(child, ast.Attribute) and child.attr == "namespace"
                          for node in guards for child in ast.walk(node.test))
    if not signature_guard or not namespace_guard:
        return False
    unit, function = resolver
    constructors = [call for call in program.calls(function) if program.symbol(unit, call.func) == "app.agent.context.RuntimeContext"]
    if not constructors:
        return False
    for call in constructors:
        namespace = program.keyword(call, "namespace")
        user = program.keyword(call, "user_id")
        if not (isinstance(namespace, ast.Attribute) and namespace.attr == "namespace"
                and isinstance(namespace.value, ast.Name) and namespace.value.id == "settings"):
            return False
        user = scoped_value(program, unit, function, user)
        if isinstance(user, ast.Subscript) and isinstance(user.value, ast.Name) and program.value(unit, user.slice) == "sub":
            claims = scoped_value(program, unit, function, user.value)
            if not (isinstance(claims, ast.Call) and program.symbol(unit, claims.func) == "app.security.tokens.verify_anonymous_session"):
                return False
        elif not (isinstance(user, ast.Attribute) and user.attr == "hex" and isinstance(user.value, ast.Call)
                  and program.symbol(unit, user.value.func) == "uuid.uuid4"):
            return False
    return True


def template_thread_evidence(program, unit, function, call):
    """追踪实际 config -> checkpointer_config -> SHA256 隔离键及正式请求 thread。"""

    config = program.keyword(call, "config")
    if not isinstance(config, ast.Name):
        return False
    value = scoped_value(program, unit, function, config)
    if not isinstance(value, ast.Call) or program.symbol(unit, value.func) != "app.persistence.checkpointer.checkpointer_config" or len(value.args) != 1:
        return False
    key = value.args[0]
    if not isinstance(key, ast.Call) or len(key.args) != 3:
        return False
    record = program.functions.get(program.symbol(unit, key.func))
    if record is None:
        return False
    owner, key_function = record
    context, thread = key.args[1:]
    if not (isinstance(context, ast.Name) and context.id == "runtime_context"
            and isinstance(thread, ast.Attribute) and thread.attr == "thread_id"
            and isinstance(thread.value, ast.Name) and thread.value.id == "request"):
        return False
    from app.services.business_acceptance_verifiers.python_agent_context import _attributes
    from app.services.business_acceptance_verifiers.python_endpoint import trusted_context_names

    returned = [node.value for node in active_nodes(key_function) if isinstance(node, ast.Return)]
    if len(returned) != 1:
        return False
    hashed = returned[0]
    if not (isinstance(hashed, ast.Call) and isinstance(hashed.func, ast.Attribute) and hashed.func.attr == "hexdigest"
            and isinstance(hashed.func.value, ast.Call) and program.symbol(owner, hashed.func.value.func) == "hashlib.sha256"):
        return False
    attributes = _attributes(program, owner, key_function, returned[0])
    names = trusted_context_names(program, owner, key_function)
    if not any({(name, "namespace"), (name, "user_id"), (name, "authentication_mode")} <= attributes for name in names):
        return False
    # 线程形参必须实际出现在唯一键的返回数据依赖内，而不是无用读取。
    identity = program.assignment(owner, "identity", key_function)
    if identity is None or not any(isinstance(node, ast.Name) and node.id == "thread_id" for node in ast.walk(identity)):
        return False
    checkpoint = program.functions.get("app.persistence.checkpointer.checkpointer_config")
    if checkpoint is None:
        return False
    saved_unit, saved_function = checkpoint
    returned_config = [node.value for node in active_nodes(saved_function) if isinstance(node, ast.Return)]
    if len(returned_config) != 1:
        return False
    from app.services.business_acceptance_verifiers.python_agent_context import _dict_field

    configurable = _dict_field(program, saved_unit, saved_function, returned_config[0], "configurable")
    saved_thread = _dict_field(program, saved_unit, saved_function, configurable, "thread_id")
    if not isinstance(saved_thread, ast.Name) or saved_thread.id != "thread_key":
        return False
    # config 只允许附加 metadata，不允许在图调用前覆盖隔离键。
    for node in active_nodes(function):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id == config.id:
            if program.value(unit, node.slice) != "metadata":
                return False
    return True
