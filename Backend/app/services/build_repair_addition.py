"""校验 Build 局部修复唯一可申请的新增源码权限。"""

from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Any

from app.services.build_task_planner import _is_auth_constants_path, _task_declared_paths


_JAVA_SOURCE_PATH = re.compile(r"(?:backend|Backend)/src/main/java/(?:[\w$]+/)*[\w$]+\.java")
_WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])(?:\.java)?", re.IGNORECASE)


def validate_repair_addition(
    path: str,
    workspace: Path | None,
    tasks: list[dict[str, Any]],
    *,
    repair_id: str = "",
) -> None:
    """确认新增文件是工作区内未被其他任务声明的真实空闲 Java 源码路径。"""
    if (
        not _JAVA_SOURCE_PATH.fullmatch(path)
        or any(_WINDOWS_DEVICE.fullmatch(part) for part in path.split("/"))
        or _is_auth_constants_path(path)
    ):
        raise ValueError("局部修复只允许申请一个精确的后端 Java 源码路径。")
    if workspace is None:
        raise ValueError("缺少当前工作区，无法核对修复新增路径。")

    root = workspace.resolve()
    target = root / path
    try:
        # 逐段拒绝符号链接、junction 和 Windows 路径别名，避免授权穿过实际目录边界。
        if not (root / "/".join(path.split("/")[:4])).is_dir() or target.resolve() != target:
            raise ValueError(f"新增路径不在真实 Java 源码目录内：{path}。")
        if target.exists() or target.is_symlink():
            raise ValueError(f"新增目标已存在：{path}。请检查文件后再继续。")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法核对新增目标：{path}。") from exc

    for task in tasks:
        if task.get("id") != repair_id and any(
            _scope_claims_path(scope, path) for scope in _task_declared_paths(task)
        ):
            raise ValueError(f"新增路径 {path} 已由任务 {task['id']} 声明。请调整任务归属或依赖。")


def _scope_claims_path(scope: str, path: str) -> bool:
    """保守识别精确路径、目录及目录 glob 对新增目标的占用。"""
    normalized = scope.casefold().rstrip("/")
    candidate = path.casefold()
    return fnmatchcase(candidate, normalized) or candidate.startswith(normalized + "/")
