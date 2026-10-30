"""七模块本地资源的当前合同，只接受正式规划列出的精确文档文件。"""

from pathlib import PurePosixPath


def agent_resource_files(module: str, config: dict) -> list[str]:
    """启用时必须声明本地文件，禁用时不能遗留资源列表或外部检索配置。"""

    if config.get("enabled") is False:
        if set(config) != {"enabled"}:
            raise ValueError(f"禁用的 {module} 只能声明 enabled=false。")
        return []
    if config.get("enabled") is not True or set(config) != {"enabled", "files"}:
        raise ValueError(f"启用 {module} 必须声明 enabled=true 和精确 files；当前仅支持项目内文档资源。")
    files = config["files"]
    prefix = f"agent-runtime/src/app/{module}/"
    if not isinstance(files, list) or not files or len(files) > 16 or any(not isinstance(path, str) for path in files):
        raise ValueError(f"{module}.files 必须包含 1–16 个精确文件。")
    if len(set(files)) != len(files):
        raise ValueError(f"{module}.files 不能重复。")
    for path in files:
        parts = PurePosixPath(path).parts
        if not path.startswith(prefix) or str(PurePosixPath(path)) != path \
                or any(part.startswith(".") or any(char in part for char in "*?[]\\") for part in parts) \
                or not path.endswith(".md"):
            raise ValueError(f"{module} 资源必须是自身目录中的普通 Markdown：{path}。")
        if module == "skills" and (not path.endswith("/SKILL.md") or len(parts) < 7):
            raise ValueError("技能资源必须采用 skills/<集合>/<技能>/SKILL.md 结构。")
    return files
