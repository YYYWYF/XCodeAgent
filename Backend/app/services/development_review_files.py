"""从当前 Build 计划的已完成模块固定 Diff 审查文件清单。"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.version_control import InspectAllVersionControlRequest, inspect_all_version_control
from app.workspace.workspace import _is_sensitive_path


def reviewable_file_path(value: Any) -> str:
    """仅接受 Diff 计划中的安全前后端项目相对路径。"""

    from app.agents.code_analyze.scope import is_code_review_diff_path

    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != path:
        return ""
    if not is_code_review_diff_path(path):
        return ""
    return path


def development_review_files(workspace: str | Path) -> list[str]:
    """按顶部已完成模块的同一计划与 Git 口径收集全部目标文件。"""

    return development_review_selection(workspace)[0]


def development_review_selection(workspace: str | Path) -> tuple[list[str], list[str]]:
    """返回可读目标及被跳过的安全路径，供报告说明实际扫描范围。"""

    root = Path(workspace).resolve()
    plan_path = root / WORKSPACE_ARTIFACT_DIR / "plans/build-task-plan.json"
    if plan_path.is_symlink() or not plan_path.resolve().is_relative_to(root):
        return [], []
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        snapshot = inspect_all_version_control(
            InspectAllVersionControlRequest(action="inspect_all", workspaceRoot=str(root))
        )
    except (OSError, UnicodeError, ValueError):
        return [], []
    if not isinstance(plan, dict):
        return [], []
    units = plan.get("build_units")
    registry = plan.get("task_registry")
    if not isinstance(units, dict) or not isinstance(registry, dict):
        return [], []
    pending = set(snapshot.eligible_paths)
    candidate_targets: set[str] = set()
    for unit in units.values():
        if not isinstance(unit, dict) or not isinstance(unit.get("task_ids"), list):
            continue
        task_ids = unit["task_ids"]
        if not task_ids:
            continue
        tasks = [registry.get(str(task_id)) for task_id in task_ids]
        if not all(isinstance(task, dict) and task.get("status") == "completed" for task in tasks):
            continue
        # 与顶部列表一致：模块只需一个文件未提交，便纳入它声明的全部目标文件。
        targets = {
            reviewable_file_path(path)
            for task in tasks
            for path in _task_target_files(task)
        }
        targets.discard("")
        if targets.intersection(pending):
            candidate_targets.update(targets)
    files = sorted(path for path in candidate_targets if current_review_file(root, path))
    skipped = sorted(candidate_targets.difference(files))
    return files, skipped


def _task_target_files(task: dict[str, Any]) -> list[Any]:
    """读取与前端模块列表一致的任务目标文件字段。"""

    declared = task.get("target_files", task.get("targetFiles"))
    return declared if isinstance(declared, list) else []


def current_review_file(workspace: str | Path, value: Any) -> bool:
    """审查启动时再次校验文件仍处于工作区且可安全读取。"""

    path = reviewable_file_path(value)
    if not path:
        return False
    root = Path(workspace).resolve()
    candidate = root / path
    if any(
        (root.joinpath(*PurePosixPath(path).parts[:index])).is_symlink()
        for index in range(1, len(PurePosixPath(path).parts) + 1)
    ):
        return False
    return (
        candidate.is_file()
        and not candidate.is_symlink()
        and candidate.resolve().is_relative_to(root)
        and not _is_sensitive_path(candidate)
        and _is_text_file(candidate)
    )


def _is_text_file(path: Path) -> bool:
    """拒绝二进制和非 UTF-8 文件，避免把二进制内容交给审查 Agent。"""

    try:
        with path.open("r", encoding="utf-8") as source:
            for chunk in iter(lambda: source.read(8192), ""):
                if "\x00" in chunk:
                    return False
    except (OSError, UnicodeError):
        return False
    return True
