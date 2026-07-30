from __future__ import annotations

from typing import Any, Dict, List, Set


SHARED_FILE_MARKERS = (
    "AGENTS.md",
    "docs/CODEBASE_INDEX.md",
    "package.json",
    "pnpm-lock.yaml",
    "requirements.txt",
    "pyproject.toml",
    "Backend/app/main.py",
    "Backend/app/protocols/ag_ui.py",
    "Frontend/src/main/index.ts",
    "Frontend/src/preload/",
    "Frontend/src/renderer/src/typings/",
    "frontend/src/constants/menus.ts",
)

PUBLIC_CONTRACT_MARKERS = (
    "ag-ui",
    "ipc",
    "preload",
    "storage",
    "session",
    "contract",
    "schema",
)


def annotate_task_execution(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    target_counts = _target_counts(tasks)
    return [_annotate_task(task, target_counts=target_counts) for task in tasks]


def build_execution_batches(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    remaining = {task["id"]: task for task in tasks}
    completed: Set[str] = set()
    batches: List[Dict[str, Any]] = []
    batch_index = 1

    while remaining:
        ready = [
            task
            for task in remaining.values()
            if set(_task_dependencies(task)).issubset(completed)
        ]
        if not ready:
            batches.append(
                {
                    "index": batch_index,
                    "mode": "blocked",
                    "tasks": sorted(remaining),
                    "reason": "任务依赖存在环或依赖了不存在的任务。",
                }
            )
            break

        batch = _select_ready_batch(ready)
        mode = "parallel" if len(batch) > 1 else "serial"
        batches.append(
            {
                "index": batch_index,
                "mode": mode,
                "tasks": [task["id"] for task in batch],
                "reason": _batch_reason(batch, mode),
            }
        )
        for task in batch:
            completed.add(task["id"])
            remaining.pop(task["id"], None)
        batch_index += 1

    return batches


def scheduler_capabilities() -> Dict[str, Any]:
    return {
        "executionModes": ["main-integrated", "subagent-plan-only", "subagent-direct-write"],
        "rules": [
            "inspect tasks run through a read-only scout.",
            "verify tasks stay main-integrated.",
            "Tasks without explicit target_files are plan-only.",
            "Every executable task with explicit target_files is dispatched to its bounded owner runner.",
            "Shared, public-contract, and overlapping target_files are direct-write but serialized.",
        ],
    }


def _annotate_task(task: Dict[str, Any], *, target_counts: Dict[str, int]) -> Dict[str, Any]:
    next_task = dict(task)
    task_type = str(next_task.get("task_type") or "feature")
    target_files = _task_target_files(next_task)
    has_conflict = any(target_counts.get(target, 0) > 1 for target in target_files)
    shared = _touches_shared_file(target_files)
    public_contract = _touches_public_contract(next_task)

    if task_type == "inspect":
        mode = "main-integrated"
        agent = "scout"
        reason = "工程侦察任务只读执行，结果由主 Agent 纳入上下文。"
        can_parallel = False
    elif task_type == "verify":
        mode = "main-integrated"
        agent = "verifier"
        reason = "最终验证必须在所有实现任务之后由主 Agent 统一判断。"
        can_parallel = False
    elif not target_files:
        mode = "subagent-plan-only"
        agent = _builder_for(task_type)
        reason = "任务缺少明确 target_files，不能进入代码执行器。"
        can_parallel = False
    elif task_type == "shared" or shared or public_contract:
        mode = "subagent-direct-write"
        agent = _builder_for(task_type)
        reason = "任务涉及共享文件或公共契约，由对应受限执行器串行写入。"
        can_parallel = False
    elif has_conflict:
        mode = "subagent-direct-write"
        agent = _builder_for(task_type)
        reason = "任务 target_files 与其他任务重叠，由同一受限执行器按依赖顺序串行写入。"
        can_parallel = False
    else:
        mode = "subagent-direct-write"
        agent = _builder_for(task_type)
        reason = "任务 target_files 明确且互斥，允许受限 subagent 直接写入。"
        can_parallel = _task_can_run_in_parallel(next_task)

    next_task["executionMode"] = mode
    next_task["assignedAgent"] = agent
    next_task["directWriteReason"] = reason
    next_task["can_run_in_parallel"] = can_parallel
    next_task.setdefault("status", "pending")
    return next_task


def _select_ready_batch(ready: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    serial = [
        task
        for task in ready
        if task.get("executionMode") != "subagent-direct-write"
        or not _task_can_run_in_parallel(task)
    ]
    if serial:
        serial.sort(key=_task_priority)
        return [serial[0]]

    batch: List[Dict[str, Any]] = []
    used_targets: Set[str] = set()
    for task in sorted(ready, key=lambda item: str(item.get("id"))):
        targets = {target for target in _task_target_files(task) if target}
        if targets and used_targets.intersection(targets):
            continue
        batch.append(task)
        used_targets.update(targets)
    return batch or [sorted(ready, key=lambda item: str(item.get("id")))[0]]


def _batch_reason(batch: List[Dict[str, Any]], mode: str) -> str:
    if mode == "parallel":
        return "这些任务依赖已满足，且 target_files 互斥，可由受限 subagent 并行推进。"
    task_type = (
        str(batch[0].get("task_type") or "task")
        if batch
        else "task"
    )
    if task_type == "inspect":
        return "工程侦察需要先完成，后续任务依赖它的结果。"
    if task_type == "verify":
        return "最终验证必须在实现任务之后执行。"
    return str(batch[0].get("directWriteReason") or "该任务需要串行执行。") if batch else "该批次需要串行执行。"


def _task_priority(task: Dict[str, Any]) -> int:
    order = {"inspect": 0, "shared": 1, "feature": 2, "frontend": 2, "backend": 2, "fullstack": 2, "test": 3, "verify": 4}
    return order.get(str(task.get("task_type")), 9)


def _target_counts(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for task in tasks:
        for target in _task_target_files(task):
            counts[target] = counts.get(target, 0) + 1
    return counts


def _touches_shared_file(target_files: List[str]) -> bool:
    return any(any(marker in target for marker in SHARED_FILE_MARKERS) for target in target_files)


def _touches_public_contract(task: Dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(task.get("title") or ""),
            str(task.get("task_type") or ""),
            " ".join(_string_list(task.get("acceptance_criteria"))),
            " ".join(_task_target_files(task)),
        ]
    ).lower()
    return any(marker in text for marker in PUBLIC_CONTRACT_MARKERS)


def _builder_for(task_type: str) -> str:
    if task_type == "frontend":
        return "frontend-builder"
    if task_type == "backend":
        return "backend-builder"
    if task_type == "fullstack":
        return "fullstack-builder"
    if task_type == "test":
        return "verifier"
    return "fullstack-builder"


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _task_target_files(task: Dict[str, Any]) -> List[str]:
    """读取当前 DAG v3 任务的目标文件。"""

    return _string_list(task.get("target_files"))


def _task_dependencies(task: Dict[str, Any]) -> List[str]:
    """读取当前 DAG v3 任务的依赖列表。"""

    return _string_list(task.get("dependencies"))


def _task_can_run_in_parallel(task: Dict[str, Any]) -> bool:
    """读取当前 DAG v3 任务的并行标记。"""

    return bool(task.get("can_run_in_parallel", True))
