"""结束任务后按代码、配置和正式产物内容判断是否仍可安全继续。"""

import hashlib
import os
from pathlib import Path

# 这些目录只承载依赖、构建或运行证据；正式产物和 application.json 必须参与。
_IGNORED = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
            ".next", ".turbo", ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage"}
_RUNTIME = {"runtime", "recovery", "logs", "reports", "cache", "snapshots", "checkpoints"}


def workspace_fingerprint(workspace: str | Path) -> str:
    """流式哈希文件路径和内容；读取失败直接拒绝继续，不输出敏感文件内容。"""
    root = Path(workspace).resolve(strict=True)
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=_raise_scan_error):
        relative = Path(directory).relative_to(root)
        dirs[:] = sorted(name for name in dirs if name not in _IGNORED and not (
            relative == Path(".devagentstudio") and name in _RUNTIME
        ))
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if rel == ".devagentstudio/application-lifecycle.json" or name.endswith((".log", ".pyc")):
                continue
            digest.update(rel.encode())
            digest.update(b"\0")
            if path.is_symlink():
                # 不沿链接越出工作区；链接目标变化也会失效。
                digest.update(os.readlink(path).encode())
            else:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _raise_scan_error(error: OSError) -> None:
    """禁止扫描失败时以不完整目录结果误判未变化。"""
    raise error


def ended_workspace_unchanged(workspace: str, fingerprint: str) -> bool:
    """公开投影读取失败时收紧资格，最终恢复仍在入场锁内重新校验。"""
    try:
        return workspace_fingerprint(workspace) == fingerprint
    except OSError:
        return False
