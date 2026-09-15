"""提供不绑定 Reconcile 协议版本的原子 JSON 持久化工具。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """以临时文件、文件 fsync、rename 与平台支持的目录 fsync 原子落盘 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        _fsync_parent_directory(path.parent)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _fsync_parent_directory(directory_path: Path) -> None:
    """在支持目录文件描述符的平台同步父目录，Windows 保留原子替换结果。"""

    # Windows 无法通过 os.open 打开目录；文件 fsync 与 os.replace 已提供当前平台可用的持久化保证。
    if os.name == "nt":
        return
    descriptor = os.open(directory_path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
