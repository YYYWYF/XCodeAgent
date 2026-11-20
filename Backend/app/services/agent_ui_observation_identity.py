"""受管预览的有界源码、配置和已安装包字节摘要，不复制内容。"""

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

_EXCLUDED = {'node_modules', '.git', 'dist', 'build', '.rsbuild', '.cache', 'coverage',
             '.venv', '__pycache__', '.pytest_cache', '.devagentstudio', 'logs',
             'checkpoints', '.mypy_cache', '.ruff_cache'}
_SUFFIXES = {'.log', '.pyc', '.sqlite', '.sqlite3', '.db', '.pem', '.key'}


def digest(value: Any) -> str:
    """以稳定编码生成摘要，结果不包含源码、路径内容或配置原值。"""
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@lru_cache(maxsize=8192)
def _file_digest(path: str, identity: tuple[int, ...]) -> str:
    """按 inode、大小和纳秒时间缓存未变字节，避免每次状态订阅重读全部包。"""
    target = Path(path)
    with target.open('rb') as stream:
        value = sha256()
        for block in iter(lambda: stream.read(128 * 1024), b''):
            value.update(block)
    if _stat_identity(target) != identity:
        raise ValueError('观察期间文件发生变化')
    return value.hexdigest()


def _stat_identity(path: Path) -> tuple[int, ...]:
    """缓存同时核对变更时间，原地修改并恢复 mtime 也不能复用旧摘要。"""
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def tree_digest(root: Path, *, package: bool = False) -> str:
    """哈希当前树内普通文件；拒绝链接和超限树，不遍历依赖或运行输出。"""
    entries: list[tuple[str, str]] = []
    total = 0
    count = 0

    def visit(directory: Path) -> None:
        """遍历有界目录并保留相对身份，不暴露机器绝对路径。"""
        nonlocal total, count
        for path in sorted(directory.iterdir()):
            if path.name in _EXCLUDED or (not package and (path.name.startswith('.env') or path.suffix in _SUFFIXES)):
                continue
            count += 1
            if count > 12000 or path.is_symlink():
                raise ValueError('观察树超限或包含链接')
            if path.is_dir():
                visit(path)
            elif path.is_file():
                identity = _stat_identity(path)
                total += identity[2]
                if identity[2] > 32 * 1024 * 1024 or total > 128 * 1024 * 1024:
                    raise ValueError('观察字节超限')
                entries.append((path.relative_to(root).as_posix(), _file_digest(str(path), identity)))
            else:
                raise ValueError('观察只接受普通文件')

    if root.is_symlink():
        raise ValueError('观察根不能为链接')
    if root.exists():
        visit(root)
    return digest(entries)


def workspace_revision(root: Path, packages: list[dict[str, str]]) -> dict[str, str]:
    """绑定当前前后端源码和正式配置；运行记录与凭据文件不作为源码。"""
    formal = root / '.devagentstudio'
    files = []
    if formal.is_symlink():
        raise ValueError('正式配置目录不能为链接')
    if formal.is_dir():
        for item in sorted(formal.iterdir()):
            if item.suffix not in {'.json', '.md'} or item.name in {'application-lifecycle.json'}:
                continue
            if item.is_symlink() or not item.is_file() or item.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('正式配置不可观察')
            files.append((item.name, _file_digest(str(item), _stat_identity(item))))
    facts = {'frontend': tree_digest(root / 'frontend'), 'runtime': tree_digest(root / 'agent-runtime'),
             'formal': digest(files), 'specs': tree_digest(formal / 'specs'),
             'plans': tree_digest(formal / 'plans'), 'packages': packages}
    return {'workspaceSha256': sha256(str(root).encode()).hexdigest(), 'revisionSha256': digest(facts)}
