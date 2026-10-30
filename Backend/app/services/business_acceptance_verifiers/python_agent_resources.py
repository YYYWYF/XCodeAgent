"""验收实际挂载的技能及本地知识中间件，不放行任意动态检索器。"""

import ast
from pathlib import PurePosixPath
from yaml import safe_load, YAMLError

from app.services.runtime_agent_resources import agent_resource_files


def _filesystem_root(program, unit, function, node, module):
    """要求中间件独享的文件 backend 锚定工厂源码旁的模块资源目录。"""

    node = program.expand(unit, node, function)
    if not isinstance(node, ast.Call) or program.symbol(unit, node.func) != "deepagents.backends.filesystem.FilesystemBackend":
        return False
    if program.value(unit, program.keyword(node, "virtual_mode")) is not True or node.args:
        return False
    root = program.keyword(node, "root_dir")
    if not (isinstance(root, ast.BinOp) and isinstance(root.op, ast.Div)
            and program.value(unit, root.right) == module):
        return False
    parent = root.left
    if not (isinstance(parent, ast.Subscript) and isinstance(parent.value, ast.Attribute)
            and parent.value.attr == "parents" and program.value(unit, parent.slice) == 1):
        return False
    resolved = parent.value.value
    return (isinstance(resolved, ast.Call) and not resolved.args and not resolved.keywords
            and isinstance(resolved.func, ast.Attribute) and resolved.func.attr == "resolve"
            and isinstance(resolved.func.value, ast.Call)
            and program.symbol(unit, resolved.func.value.func) == "pathlib.Path"
            and len(resolved.func.value.args) == 1
            and isinstance(resolved.func.value.args[0], ast.Name) and resolved.func.value.args[0].id == "__file__")


def verify_agent_resources(program, files, unit, function, factory, module, config):
    """核对正式文件内容、精确中间件和真实 factory 参数；不支持语义仍返回 blocked。"""

    resources = agent_resource_files(module, config)
    errors, blocked = [], []
    middleware = program.expand(unit, program.keyword(factory, "middleware"), function)
    if middleware is None:
        calls = []
    elif isinstance(middleware, (ast.List, ast.Tuple)):
        calls = [program.expand(unit, item, function) for item in middleware.elts]
    else:
        return [], ["资源中间件必须使用显式列表，无法证明动态装配。"]
    symbol = "deepagents.middleware.skills.SkillsMiddleware" if module == "skills" else "deepagents.middleware.memory.MemoryMiddleware"
    matches = [call for call in calls if isinstance(call, ast.Call) and program.symbol(unit, call.func) == symbol]
    if any(not isinstance(call, ast.Call) or program.symbol(unit, call.func) not in {
        "deepagents.middleware.skills.SkillsMiddleware", "deepagents.middleware.memory.MemoryMiddleware",
    } for call in calls):
        blocked.append("资源模块存在尚未适配的中间件，无法证明其访问和装配范围。")
    # 禁止绕过独立 backend，避免默认工厂 skills/memory 去读取另一资源域。
    direct = program.keyword(factory, "skills" if module == "skills" else "memory")
    if direct is not None and program.value(unit, direct, function) not in (None, []):
        blocked.append(f"{module} 必须通过独立本地资源中间件装配。")
    if not resources:
        if matches:
            errors.append(f"{module}.enabled=false 但仍挂载资源中间件。")
        return errors, blocked
    if len(matches) != 1 or unit.module != "app.agent.factory":
        return errors, [*blocked, f"启用 {module} 必须在当前 Agent 工厂挂载唯一的 {symbol}。"]
    call = matches[0]
    if not _filesystem_root(program, unit, function, program.keyword(call, "backend"), module):
        blocked.append(f"{module} 文件 backend 未证明以工厂的 src/app/{module} 为独立 virtual_mode 根。")
    prefix = f"agent-runtime/src/app/{module}/"
    paths = [PurePosixPath(path.removeprefix(prefix)) for path in resources]
    sources = sorted({"/" + (path.parent.parent.as_posix() if module == "skills" else path.as_posix()) for path in paths})
    if program.value(unit, program.keyword(call, "sources"), function) != sources:
        errors.append(f"{module} 中间件 sources 与正式文件清单不一致。")
    if program.keyword(call, "system_prompt") is not None:
        blocked.append(f"{module} 不能覆盖中间件默认 system_prompt，避免资源被装载却不进入模型。")
    for path in resources:
        content = files.get(path)
        if not isinstance(content, str) or not content.strip():
            errors.append(f"正式资源未交付或为空：{path}。")
        elif module == "skills":
            try:
                header = safe_load(content.split("---", 2)[1]) if content.startswith("---\n") and content.count("---") >= 2 else None
            except YAMLError:
                header = None
            if not isinstance(header, dict) or any(not isinstance(header.get(key), str) or not header[key].strip()
                                                    for key in ("name", "description")):
                errors.append(f"技能 {path} 缺少可加载的 name/description frontmatter。")
    return errors, blocked
