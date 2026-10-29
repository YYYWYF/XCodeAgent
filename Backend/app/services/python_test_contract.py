"""Runtime 单测路径和源码判定，生成权限与执行目标共用同一规则。"""

import ast
from pathlib import PurePosixPath


def is_runtime_test_path(path: str) -> bool:
    """只允许 Runtime tests 根下平铺 test_*.py，禁止隐藏路径、辅助脚本和路径穿越。"""

    if not isinstance(path, str) or "\\" in path:
        return False
    relative = path[1:] if path.startswith("/") else path
    parts = PurePosixPath(relative).parts
    return (len(parts) == 3 and parts[:2] == ("agent-runtime", "tests")
            and parts[2].startswith("test_") and parts[2].endswith(".py")
            and str(PurePosixPath(relative)) == relative
            and not any(char in relative for char in "*?[]\x00"))


def is_runtime_source_path(path: str) -> bool:
    """识别 Runtime 业务及 Agent Python 源码，不把测试、环境或缓存当成开发变更。"""

    parts = PurePosixPath(path).parts
    return (path.startswith("agent-runtime/src/app/") and path.endswith(".py")
            and ".." not in parts and not any(part.startswith(".") or part == "__pycache__" for part in parts)
            and PurePosixPath(path).name != "__init__.py")


def python_test_content_error(content: str) -> str | None:
    """验证 pytest 可收集的真实用例并拒绝静态 skip/xfail，不调用应用代码。"""

    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return f"Python 测试语法错误：第 {exc.lineno} 行 {exc.msg}。"
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith("test_")]
    if not functions:
        return "Python 测试没有 test_* 用例。"
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"skip", "skipif", "xfail"}:
            return "Python 测试不允许 skip/skipif/xfail。"
    # with 本身不是断言；仅接受 assert 或显式 pytest.raises，避免空上下文冒充行为验证。
    if not any(
        isinstance(node, ast.Assert) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pytest" and node.func.attr == "raises"
        )
        for function in functions for node in ast.walk(function)
    ):
        return "Python 测试必须包含结果断言或异常断言。"
    return None
