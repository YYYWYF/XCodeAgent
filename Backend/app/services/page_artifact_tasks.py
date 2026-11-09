"""只读投影正式页面开发任务与真实存在的任务关联文件。"""

from pathlib import Path
from typing import Any

from app.services.build_scheduler import resolve_execution_slice
from app.workspace.task_documents import load_confirmed_build_task_plan, build_task_plan_json_path


def page_tasks(workspace: Path, page_id: str) -> dict[str, Any]:
    """复用调度器范围裁剪，拒绝把其他页面任务计入所选页面进度。"""
    plan = load_confirmed_build_task_plan(workspace)
    if plan is None:
        if build_task_plan_json_path({"workspace": str(workspace)}).exists():
            raise ValueError("当前开发计划尚未确认或正式计划校验未通过。")
        return {"tasks": [], "files": [], "taskPlanAvailable": False}
    registry = plan.get("task_registry")
    if not isinstance(registry, dict):
        raise ValueError("正式开发计划缺少有效任务注册表。")
    tasks = list(registry.values())
    if any(not isinstance(task, dict) or not task.get("id") for task in tasks):
        raise ValueError("正式开发任务记录不完整。")
    selected = resolve_execution_slice(
        build_task_plan=plan, tasks=tasks,
        build_execution_scope={"type": "page", "targetId": page_id},
    )["tasks"]
    task_ids = {task["id"] for task in selected}
    names = {task["id"]: str(task.get("title") or task["id"]) for task in tasks}
    projected = []
    files: dict[str, list[str]] = {}
    for task in selected:
        dependencies = task.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError(f"任务 {task['id']} 的依赖记录无效。")
        projected.append({
            "id": task["id"], "title": str(task.get("title") or task["id"]),
            "description": str(task.get("description") or ""),
            "owner": str(task.get("owner") or ""),
            "unitId": str(task.get("unit_id") or ""),
            "scope": "page" if task.get("unit_id") == f"page:{page_id}" else "dependency",
            "status": str(task.get("status") or "unknown"),
            "failureReason": str(task.get("failure_reason") or ""),
            "updatedAt": str(task.get("updated_at") or ""),
            "dependencies": [{"id": key, "title": names.get(key, key), "inScope": key in task_ids}
                             for key in dependencies if isinstance(key, str)],
        })
        # target_files 只说明计划关联；必须真实存在且位于工作区内，才展示为可浏览文件。
        for target in task.get("target_files", []) if isinstance(task.get("target_files"), list) else []:
            if not isinstance(target, str) or Path(target).is_absolute():
                continue
            path = (workspace / target).resolve()
            if path.is_relative_to(workspace) and path.is_file():
                relative = path.relative_to(workspace).as_posix()
                files.setdefault(relative, []).append(task["id"])
    return {
        "tasks": projected, "taskPlanAvailable": True,
        "files": [{"path": path, "taskIds": ids} for path, ids in sorted(files.items())],
        "taskPlanConfirmedAt": str(plan.get("confirmed_at") or ""),
    }
