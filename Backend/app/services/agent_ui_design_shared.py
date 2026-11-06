"""共享 Agent 应用层的范围校验和源码保留。"""

from pathlib import Path

SHARED_AGENT_ROOT = 'src/agent-ui'


def require_shared_application(project: Path) -> None:
    """已有独立应用须由用户重新生成，禁止默默丢弃页面定制。"""
    variants = project / 'src/design/agent-pages'
    if variants.is_symlink() or (variants.exists() and any(variants.iterdir())):
        raise ValueError('当前设计包含独立页面应用，无法直接转为共享应用；请明确重新生成并确认设计。')


def read_shared_application(project: Path) -> dict[str, str]:
    """保留当前共享应用的文本源码，不复制依赖、初始化状态或工程外链接。"""
    require_shared_application(project)
    root = project / SHARED_AGENT_ROOT
    if root.is_symlink():
        raise ValueError('共享 Agent 应用层不能使用链接。')
    result: dict[str, str] = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('共享 Agent 应用层不能包含链接。')
        if path.is_file():
            result[path.relative_to(project).as_posix()] = path.read_text(encoding='utf-8')
    return result
