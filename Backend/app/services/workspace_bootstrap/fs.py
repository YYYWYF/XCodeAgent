"""Windows 安全的 Bootstrap 受管路径删除。"""

from __future__ import annotations

import shutil
import stat
import time
from pathlib import Path
from typing import Any


def _clear_readonly(path: Path) -> None:
    """清除 Windows 只读属性，否则 Git pack 等文件无法删除。"""

    try:
        path.chmod(path.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        return


def _rmtree_onexc(function: Any, path: str, exc: BaseException) -> None:
    """在 shutil.rmtree 遇到权限错误时去掉只读位并重试当前条目。"""

    if not isinstance(exc, OSError):
        raise exc
    target = Path(path)
    _clear_readonly(target)
    function(path)


def remove_managed_path(path: Path) -> None:
    """删除已解析到受管路径的文件、目录或符号链接；Windows 上先放开只读位。"""

    if path.is_symlink():
        _clear_readonly(path)
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    last_error: OSError | None = None
    for attempt in range(6):
        try:
            if path.is_file():
                _clear_readonly(path)
                path.unlink()
                return
            for child in path.rglob("*"):
                _clear_readonly(child)
            _clear_readonly(path)
            shutil.rmtree(path, onexc=_rmtree_onexc)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05 * (2 ** attempt))
    if last_error is not None:
        raise last_error
