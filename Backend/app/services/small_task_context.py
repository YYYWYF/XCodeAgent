"""从编译诊断提取受限修复的源码候选，保留规划器已确认的修复方向。"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.small_task_scope import _path_matches_task

_COMPILER_PATH = re.compile(
    r"(?:^|\n)\s*(?:\[ERROR\]\s*)?([^\n]+?\.(?:tsx?|jsx?|java|py))"
    r"(?:\(\d+,\d+\)|:\[?\d+(?:[:,]\d+)?\]?)"
)


def repair_candidate_files(task: dict[str, Any], workspace: str | None) -> list[str]:
    """优先返回真实编译报错源码，过滤不存在、越界和非授权的诊断路径。"""

    evidence = task.get("failure_evidence") or task.get("failureEvidence") or {}
    evidence = evidence if isinstance(evidence, dict) else {}
    attempt = evidence.get("failed_attempt") or {}
    execution = attempt.get("execution") if isinstance(attempt, dict) else {}
    execution = execution if isinstance(execution, dict) else {}
    cwd = str(execution.get("cwd") or "").replace("\\", "/")
    root = Path(workspace).resolve() if workspace else None
    text = "\n".join(str(value or "") for value in (
        execution.get("stdout_tail"), execution.get("stderr_tail"), evidence.get("evidence"),
    ))
    candidates: list[str] = []
    for match in _COMPILER_PATH.finditer(text[:24_000]):
        reported = match.group(1).strip().replace("\\", "/")
        # 诊断相对路径按真实命令 cwd 解析；绝对宿主路径必须仍位于工作区。
        paths = [reported, str(PurePosixPath(cwd) / reported)]
        for value in paths:
            path = PurePosixPath(value)
            if ".." in path.parts or root is None:
                continue
            resolved = (root / value).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            relative = resolved.relative_to(root).as_posix()
            if not _path_matches_task(relative, task):
                continue
            candidates.append(relative)
            break
    targets = task.get("target_files")
    targets = targets if isinstance(targets, list) else []
    return list(dict.fromkeys([*candidates, *(str(path) for path in targets if path)]))[:100]


def repair_strategy(state: dict[str, Any]) -> str:
    """向 SmallTask 传递规划器策略，避免已定位的问题重新从配置开始探索。"""

    plan = state.get("repair_task_plan")
    return str(plan.get("strategy") or "")[:4_000] if isinstance(plan, dict) else ""
