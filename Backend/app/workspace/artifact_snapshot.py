"""平台内部产物的写入审计快照，区分正式资产与并行运行日志。"""

import hashlib
import os
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR


# 仅排除平台持有的精确路径，不放行整个 recovery/runtime 目录或任意日志。
_RUNTIME_PATHS = frozenset(
    f"recovery/execution-recovery.sqlite{suffix}"
    for suffix in ("", "-wal", "-shm", "-journal")
) | frozenset(
    f"runtime/{directory}/{name}.{stream}.log"
    for directory, names in (
        ("launch", ("install", "frontend", "backend-build", "backend-repackage", "backend")),
        ("launch-ui-design", ("install", "frontend")),
    )
    for name in names
    for stream in ("stdout", "stderr")
)


def internal_artifact_snapshot(workspace: str) -> dict[str, str]:
    """快照受保护内部资产，排除 checkpoint、恢复数据库及启动器并行日志。"""

    root = Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR
    snapshot: dict[str, str] = {}
    if not root.is_dir():
        return snapshot
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "checkpoints")
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.relative_to(root).as_posix() in _RUNTIME_PATHS:
                continue
            try:
                snapshot[path.relative_to(root.parent).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except OSError:
                continue
    return snapshot
