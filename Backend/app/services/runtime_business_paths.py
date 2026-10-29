"""规划与执行共用 Python 业务写范围，不向业务任务开放模板基础设施。"""

from pathlib import PurePosixPath


def runtime_business_path_error(path: str, *, bootstrap: bool = False) -> str | None:
    """验证精确业务路径，返回含目标位置的可读拒绝原因。"""

    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return "Runtime 任务必须声明精确、安全的文件路径。"
    parts = PurePosixPath(path).parts
    if str(PurePosixPath(path)) != path or any(
        part.startswith(".") or any(char in part for char in "*?[]") for part in parts
    ):
        return f"Runtime 任务路径不合法：{path}。"
    if path == "agent-runtime/pyproject.toml":
        return None if bootstrap else "只有 backend:bootstrap 可以修改 pyproject.toml。"
    if not path.endswith(".py") or not path.startswith(("agent-runtime/src/app/", "agent-runtime/tests/")):
        return f"Runtime 业务任务只允许精确 Python 源码：{path}。"
    root = "agent-runtime/src/app/"
    protected_roots = ("models/", "persistence/", "auth/", "security/", "agent/", "server/", "interaction/")
    protected_files = {
        "settings.py", "main.py", "__init__.py", "api/router.py", "api/security.py",
        "application/context.py", "domain/base.py", "infrastructure/database.py",
        "infrastructure/database_settings.py",
    }
    relative = path.removeprefix(root)
    if path.startswith(root) and (relative in protected_files or relative.startswith(protected_roots)):
        return (f"Python 业务任务不得修改模板基础设施：{path}。"
                "请复用模板数据库、路由及配置入口；重新规划业务任务，不能通过重试扩大权限。")
    return None
