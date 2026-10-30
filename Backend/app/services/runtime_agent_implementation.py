"""有界解析实际 Agent Builder 及静态依赖符号，避免整体 factory 摘要误伤其他 Agent。"""
from __future__ import annotations
import ast
from hashlib import sha256
import json
from pathlib import Path


def agent_implementation_facts(root: Path, formal_ids: set[str]) -> dict[str, dict]:
    """只读实际注册入口和它静态引用的应用源码，不执行目标代码或扫描目录。"""
    runtime = root / "agent-runtime"
    cache, facts = {}, {}

    def module_path(module: str):
        """精确定位普通模块、包或命名空间目录，不扫描目录、不执行导入。"""
        if not module.startswith("app.") or any(not part.isidentifier() for part in module.split(".")):
            raise ValueError(f"Agent 应用模块身份无效：{module}。")
        base = runtime / "src" / module.replace(".", "/")
        for candidate in (base.with_suffix(".py"), base / "__init__.py", base):
            current = runtime
            for part in candidate.relative_to(runtime).parts:
                current /= part
                if current.is_symlink():
                    raise ValueError("Agent 实现依赖不能经过符号链接。")
            if candidate.is_file() or candidate == base and candidate.is_dir():
                return candidate
        raise ValueError(f"Agent 应用模块不存在：{module}。")

    def module_unit(module: str):
        """按精确模块路径读取一个普通源码文件，拒绝链接和无界依赖。"""
        if module in cache:
            return cache[module]
        if len(cache) >= 48:
            raise ValueError("Agent 静态依赖超过 48 个模块。")
        path = module_path(module)
        if path.is_file() and path.stat().st_size > 256 * 1024:
            raise ValueError(f"Agent 静态源码超过 256 KiB：{module}。")
        # 命名空间包本身没有源码；其子模块在使用时按精确导入路径继续解析。
        tree = ast.parse(path.read_text() if path.is_file() else "", filename=str(path))
        symbols, imports = {}, {}
        package = module if path.is_dir() or path.name == "__init__.py" else module.rsplit(".", 1)[0]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols[target.id] = node
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols[node.target.id] = node
            elif isinstance(node, ast.ImportFrom):
                source = node.module or ""
                if node.level:
                    parts = package.split(".")
                    source = ".".join(parts[:len(parts) - node.level + 1] + ([source] if source else []))
                if not source.startswith("app."):
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        raise ValueError("Agent 静态依赖不支持应用模块星号导入。")
                    imports[alias.asname or alias.name] = (source, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        imports[alias.asname or alias.name.split(".")[0]] = (alias.name, "")
        symbols[""] = tree
        cache[module] = (symbols, imports)
        return cache[module]

    symbols, _ = module_unit("app.agent.factory")
    assignment = symbols.get("_AGENT_BUILDERS")
    registry = assignment.value if isinstance(assignment, (ast.Assign, ast.AnnAssign)) else None
    if not isinstance(registry, ast.Dict):
        raise ValueError("Agent 必须使用静态 _AGENT_BUILDERS 注册表。")
    bindings = {}
    for key, value in zip(registry.keys, registry.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or not isinstance(value, ast.Name) or key.value in bindings:
            raise ValueError("Agent 注册表包含动态或重复身份。")
        bindings[key.value] = value.id
    if set(bindings) - formal_ids:
        raise ValueError("实际注册表包含正式合同以外的 Agent 或模板 chat。")
    for agent_id, builder in bindings.items():
        visited, records = set(), {}

        def visit(module: str, name: str):
            """递归投影 Builder 真正引用的静态符号和公共装配入口，策略表不参与自身摘要。"""
            key = (module, name)
            if key in visited or module == "app.agent.execution_policy":
                return
            if len(visited) >= 256:
                raise ValueError("Agent 符号依赖超过 256 项。")
            visited.add(key)
            definitions, imports = module_unit(module)
            if name in imports:
                visit(*imports[name])
                return
            node = definitions.get(name)
            if node is None:
                # from package import service 可绑定子模块，不要求 package 定义同名变量。
                visit(module + "." + name, "")
                return
            records[module + "." + name] = ast.dump(node, include_attributes=False)
            for reference in ast.walk(node):
                if isinstance(reference, ast.Name) and isinstance(reference.ctx, ast.Load):
                    if reference.id in imports or reference.id in definitions and reference.id != "_AGENT_BUILDERS":
                        visit(module, reference.id)

        visit("app.agent.factory", builder)
        visit("app.agent.factory", "create_agent")
        digest = sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        facts[agent_id] = {"implementationSha256": "sha256:" + digest, "symbols": sorted(records)}
    return facts
