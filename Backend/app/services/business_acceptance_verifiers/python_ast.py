"""有界 Python AST 读取和静态引用解析，不 import、eval 或执行目标项目。"""

import ast
from dataclasses import dataclass

UNKNOWN = object()
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


class PythonEvidenceUnavailable(ValueError):
    """当前声明范围或语法无法提供确定性证据，应阻断而不是假定成功。"""


@dataclass
class PythonUnit:
    """单个任务文件的模块身份、语法树与当前导入。"""

    path: str
    module: str
    tree: ast.Module
    imports: dict[str, str]


def active_nodes(node: ast.AST):
    """遍历当前执行体，排除文档、未调用嵌套定义和静态不可达分支。"""

    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.ClassDef, *FUNCTIONS)):
            continue
        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
            continue
        if isinstance(child, ast.If) and isinstance(child.test, ast.Constant):
            for statement in child.body if child.test.value else child.orelse:
                yield from active_nodes(statement)
        else:
            yield from active_nodes(child)
        if isinstance(child, (ast.Return, ast.Raise)):
            break


class PythonProgram:
    """只解析平台提供的精确业务文件；导入缺失和重名不向项目外追踪。"""

    def __init__(self, files: dict[str, str]):
        """构建当前任务的模块索引，并保留语法错误的文件和行号。"""

        self.units: list[PythonUnit] = []
        self.functions: dict[str, tuple[PythonUnit, ast.AST]] = {}
        self.classes: dict[str, tuple[PythonUnit, ast.ClassDef]] = {}
        if not files:
            raise PythonEvidenceUnavailable("未提供 Python 业务目标文件。")
        for path, source in files.items():
            if not path.startswith("agent-runtime/src/app/") or not path.endswith(".py"):
                raise PythonEvidenceUnavailable(f"验收只能读取声明的 Runtime 生产源码：{path}。")
            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError as exc:
                raise PythonEvidenceUnavailable(f"{path}:{exc.lineno}:{exc.offset}：{exc.msg}。") from exc
            module = path.removeprefix("agent-runtime/src/").removesuffix(".py").replace("/", ".").removesuffix(".__init__")
            unit = PythonUnit(path, module, tree, {})
            package = module if path.endswith("/__init__.py") else module.rsplit(".", 1)[0]
            for statement in tree.body:
                if isinstance(statement, ast.Import):
                    unit.imports.update({alias.asname or alias.name.split(".")[0]: alias.name if alias.asname else alias.name.split(".")[0]
                                         for alias in statement.names})
                elif isinstance(statement, ast.ImportFrom):
                    prefix = statement.module or ""
                    if statement.level:
                        prefix = ".".join(package.split(".")[:len(package.split(".")) - statement.level + 1]
                                          + ([prefix] if prefix else []))
                    unit.imports.update({alias.asname or alias.name: prefix + "." + alias.name for alias in statement.names if alias.name != "*"})
                elif isinstance(statement, FUNCTIONS):
                    self.functions[module + "." + statement.name] = (unit, statement)
                elif isinstance(statement, ast.ClassDef):
                    self.classes[module + "." + statement.name] = (unit, statement)
                    for method in statement.body:
                        if isinstance(method, FUNCTIONS):
                            self.functions[module + "." + statement.name + "." + method.name] = (unit, method)
            self.units.append(unit)

    def symbol(self, unit: PythonUnit, node: ast.AST | None) -> str:
        """解析导入别名和属性链；不把字符串或任意变量名认作已导入基础设施。"""

        if isinstance(node, ast.Name):
            return unit.imports.get(node.id, unit.module + "." + node.id)
        if isinstance(node, ast.Attribute):
            base = self.symbol(unit, node.value)
            return base + "." + node.attr if base else ""
        return ""

    def assignment(self, unit: PythonUnit, name: str, function: ast.AST | None = None) -> ast.AST | None:
        """只接受单次直接赋值，条件合流、重赋值和反射留给明确的 blocked 诊断。"""

        statements = function.body if function is not None else unit.tree.body
        values = []
        for statement in statements:
            if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
                values.append(statement.value)
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id == name:
                values.append(statement.value)
        return values[0] if len(values) == 1 else None

    def expand(self, unit: PythonUnit, node: ast.AST | None, function: ast.AST | None = None, seen: tuple = ()) -> ast.AST | None:
        """有界展开局部/模块常量，不解析调用结果或客户端运行值。"""

        if isinstance(node, ast.Name) and node.id not in seen and len(seen) < 12:
            value = self.assignment(unit, node.id, function) if function is not None else None
            value = value if value is not None else self.assignment(unit, node.id)
            if value is not None:
                return self.expand(unit, value, function, (*seen, node.id))
        return node

    def value(self, unit: PythonUnit, node: ast.AST | None, function: ast.AST | None = None, depth: int = 0):
        """读取常量容器与拼接；动态值返回专用 UNKNOWN，不与 None 混淆。"""

        if depth > 12:
            return UNKNOWN
        node = self.expand(unit, node, function)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values = [self.value(unit, item, function, depth + 1) for item in node.elts]
            return values if all(value is not UNKNOWN for value in values) else UNKNOWN
        if isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
            keys = [self.value(unit, key, function, depth + 1) for key in node.keys]
            values = [self.value(unit, value, function, depth + 1) for value in node.values]
            if all(isinstance(key, str) for key in keys) and all(value is not UNKNOWN for value in values):
                return dict(zip(keys, values))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.value(unit, node.left, function, depth + 1), self.value(unit, node.right, function, depth + 1)
            if isinstance(left, str) and isinstance(right, str):
                return left + right
        return UNKNOWN

    def keyword(self, call: ast.Call, name: str) -> ast.AST | None:
        """只读取显式关键字，拒绝 **kwargs 隐藏覆盖正式设置。"""

        if any(keyword.arg is None for keyword in call.keywords):
            raise PythonEvidenceUnavailable("动态 **kwargs 无法证明实际生效的业务/Agent 设置。")
        return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)

    def calls(self, function: ast.AST) -> list[ast.Call]:
        """读取当前执行体中的真实调用，注释、文档或未调用嵌套函数不能作为证据。"""

        return [node for node in active_nodes(function) if isinstance(node, ast.Call)]

    def return_calls(self, unit: PythonUnit, function: ast.AST) -> list[ast.Call]:
        """只追踪实际返回表达式的数据依赖，避免无用服务调用配固定响应冒充实现。"""

        result = []
        for returned in (node for node in active_nodes(function) if isinstance(node, ast.Return)):
            pending, seen = [returned.value], set()
            while pending:
                node = pending.pop()
                if node is None or id(node) in seen:
                    continue
                seen.add(id(node))
                expanded = self.expand(unit, node, function)
                if expanded is not node:
                    pending.append(expanded)
                if isinstance(node, ast.Call):
                    result.append(node)
                pending.extend(ast.iter_child_nodes(node))
        return result

    def receiver_type(self, unit: PythonUnit, function: ast.AST, node: ast.AST) -> str:
        """从显式形参注解识别服务/上下文；不信任恰好叫 service 或 principal 的变量。"""

        if isinstance(node, ast.Name):
            argument = next((arg for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
                             if arg.arg == node.id), None)
            if argument:
                annotation = argument.annotation
                if isinstance(annotation, ast.Subscript) and self.symbol(unit, annotation.value).endswith(".Annotated"):
                    annotation = annotation.slice.elts[0] if isinstance(annotation.slice, ast.Tuple) else annotation.slice
                return self.symbol(unit, annotation)
            value = self.assignment(unit, node.id, function)
            if isinstance(value, ast.Call):
                symbol = self.symbol(unit, value.func)
                if symbol in self.classes:
                    return symbol
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            for owner_unit, cls in self.classes.values():
                if owner_unit is not unit or function not in cls.body:
                    continue
                for member in cls.body:
                    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name) and member.target.id == node.attr:
                        return self.symbol(unit, member.annotation)
                    if isinstance(member, FUNCTIONS) and member.name == "__init__":
                        for assignment in member.body:
                            if isinstance(assignment, ast.Assign) and any(ast.dump(target) == ast.dump(node) for target in assignment.targets):
                                return self.receiver_type(unit, member, assignment.value)
        return ""


def result_for(errors: list[str], blocked: list[str], *, facts: dict | None = None) -> dict:
    """区分可修复的确定错误和证据不足，统一返回可显示的细节与原因码。"""

    from app.services.business_acceptance_verifiers.common import verification_result

    status = "failed" if errors else "blocked" if blocked else "passed"
    return verification_result(status, "；".join(errors + blocked) if errors or blocked else "声明范围内的 Python AST 业务检查通过。",
                               observations=errors + blocked, facts={**(facts or {}), "reason_code":
                               "implementation_mismatch" if errors else "python_evidence_unavailable" if blocked else "verified_static_contract"})
