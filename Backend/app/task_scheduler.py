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
    "Backend/app/ag_ui.py",
    "Frontend/src/main/index.ts",
    "Frontend/src/preload/",
    "Frontend/src/renderer/src/typings/",
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
            if set(task.get("dependsOn") or []).issubset(completed)
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
            "shared, verify, public API, Electron IPC, AG-UI payload, and storage-format tasks stay main-integrated.",
            "Tasks without explicit targetFiles are plan-only.",
            "Tasks with mutually exclusive targetFiles can run as subagent-direct-write.",
            "Tasks touching the same targetFiles are serialized.",
        ],
    }


def _annotate_task(task: Dict[str, Any], *, target_counts: Dict[str, int]) -> Dict[str, Any]:
    next_task = dict(task)
    task_type = str(next_task.get("type") or "feature")
    target_files = _string_list(next_task.get("targetFiles"))
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
    elif task_type == "shared" or shared or public_contract:
        mode = "main-integrated"
        agent = "main-agent"
        reason = "任务涉及共享文件或公共契约，保留主 Agent 集成所有权。"
        can_parallel = False
    elif not target_files:
        mode = "subagent-plan-only"
        agent = _builder_for(task_type)
        reason = "任务缺少明确 targetFiles，subagent 只返回方案。"
        can_parallel = False
    elif has_conflict:
        mode = "subagent-plan-only"
        agent = _builder_for(task_type)
        reason = "任务 targetFiles 与其他任务重叠，先返回方案再由主 Agent 集成。"
        can_parallel = False
    else:
        mode = "subagent-direct-write"
        agent = _builder_for(task_type)
        reason = "任务 targetFiles 明确且互斥，允许受限 subagent 直接写入。"
        can_parallel = bool(next_task.get("canRunInParallel", True))

    next_task["executionMode"] = mode
    next_task["assignedAgent"] = agent
    next_task["directWriteReason"] = reason
    next_task["canRunInParallel"] = can_parallel
    next_task.setdefault("status", "pending")
    return next_task


def _select_ready_batch(ready: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    serial = [
        task
        for task in ready
        if task.get("executionMode") != "subagent-direct-write" or not task.get("canRunInParallel")
    ]
    if serial:
        serial.sort(key=_task_priority)
        return [serial[0]]

    batch: List[Dict[str, Any]] = []
    used_targets: Set[str] = set()
    for task in sorted(ready, key=lambda item: str(item.get("id"))):
        targets = {target for target in _string_list(task.get("targetFiles")) if target}
        if targets and used_targets.intersection(targets):
            continue
        batch.append(task)
        used_targets.update(targets)
    return batch or [sorted(ready, key=lambda item: str(item.get("id")))[0]]


def _batch_reason(batch: List[Dict[str, Any]], mode: str) -> str:
    if mode == "parallel":
        return "这些任务依赖已满足，且 targetFiles 互斥，可由受限 subagent 并行推进。"
    task_type = str(batch[0].get("type") if batch else "task")
    if task_type == "inspect":
        return "工程侦察需要先完成，后续任务依赖它的结果。"
    if task_type == "verify":
        return "最终验证必须在实现任务之后执行。"
    return str(batch[0].get("directWriteReason") or "该任务需要串行执行。") if batch else "该批次需要串行执行。"


def _task_priority(task: Dict[str, Any]) -> int:
    order = {"inspect": 0, "shared": 1, "feature": 2, "frontend": 2, "backend": 2, "fullstack": 2, "test": 3, "verify": 4}
    return order.get(str(task.get("type")), 9)


def _target_counts(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for task in tasks:
        for target in _string_list(task.get("targetFiles")):
            counts[target] = counts.get(target, 0) + 1
    return counts


def _touches_shared_file(target_files: List[str]) -> bool:
    return any(any(marker in target for marker in SHARED_FILE_MARKERS) for target in target_files)


def _touches_public_contract(task: Dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(task.get("title") or ""),
            str(task.get("type") or ""),
            " ".join(_string_list(task.get("acceptanceCriteria"))),
            " ".join(_string_list(task.get("targetFiles"))),
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
