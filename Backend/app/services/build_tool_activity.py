from __future__ import annotations

import fnmatch
from typing import Any


def task_ids_for_tool_activity(
    activity: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> list[str]:
    """按任务授权路径归属工具活动，无法精确命中时回退当前执行批次。"""

    path = _normalized_path(activity.get("path"))
    matched = [
        str(task.get("id") or "")
        for task in tasks
        if task.get("id") and path and any(_paths_overlap(path, scope) for scope in _task_scopes(task))
    ]
    if matched:
        return matched
    return [str(task.get("id") or "") for task in tasks if task.get("id")]


def _task_scopes(task: dict[str, Any]) -> list[str]:
    """汇总当前 DAG v3 任务中的目标文件和授权路径。"""

    values: list[Any] = []
    for key in ("target_files", "allowed_paths"):
        raw = task.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    change_scope = task.get("change_scope")
    if isinstance(change_scope, list):
        values.extend(
            item.get("path") for item in change_scope if isinstance(item, dict) and item.get("path")
        )
    return [normalized for value in values if (normalized := _normalized_path(value))]


def _paths_overlap(activity_path: str, task_scope: str) -> bool:
    """判断具体文件、目录或 glob 范围是否与任务授权范围相交。"""

    if fnmatch.fnmatchcase(activity_path, task_scope) or fnmatch.fnmatchcase(task_scope, activity_path):
        return True
    activity_prefix = _static_prefix(activity_path)
    scope_prefix = _static_prefix(task_scope)
    if not activity_prefix or not scope_prefix:
        return False
    return (
        activity_prefix == scope_prefix
        or activity_prefix.startswith(f"{scope_prefix}/")
        or scope_prefix.startswith(f"{activity_prefix}/")
    )


def _static_prefix(path: str) -> str:
    """提取 glob 之前的稳定路径前缀，用于目录级活动归属。"""

    parts: list[str] = []
    for part in path.split("/"):
        if any(token in part for token in ("*", "?", "[")):
            break
        if part:
            parts.append(part)
    return "/".join(parts)


def _normalized_path(value: Any) -> str:
    """统一虚拟路径分隔符和根前缀，便于跨平台匹配。"""

    text = str(value or "").strip().replace("\\", "/")
    return text.lstrip("/").rstrip("/")
