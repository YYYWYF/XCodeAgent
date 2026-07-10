from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.task_scheduler import annotate_task_execution, build_execution_batches


TASK_STATUSES = ("pending", "running", "completed", "failed")


def _task_status(task: dict[str, Any]) -> str:
    return task.get("status", "pending")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _change_scope(value: Any, target_files: list[str]) -> list[dict[str, str]]:
    operations = {"add", "modify", "delete"}
    result: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                path = item.strip()
                if path:
                    result.append(
                        {
                            "operation": "modify",
                            "path": path,
                            "description": "按任务要求调整该文件。",
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            path = _text(item.get("path") or item.get("file"))
            if not path:
                continue
            operation = _text(item.get("operation"), "modify").lower()
            if operation not in operations:
                operation = "modify"
            result.append(
                {
                    "operation": operation,
                    "path": path,
                    "description": _text(item.get("description"), "按任务要求调整该文件。"),
                }
            )
    if result:
        return result
    return [
        {"operation": "modify", "path": path, "description": "按任务要求调整该文件。"}
        for path in target_files
    ]


def _impact_scope(value: Any, description: str) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "summary": _text(source.get("summary"), description),
        "affected_modules": _string_list(
            source.get("affected_modules") or source.get("affectedModules")
        ),
        "public_contracts": _string_list(
            source.get("public_contracts") or source.get("publicContracts")
        ),
        "risks": _string_list(source.get("risks")),
    }


def _workspace_analysis(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "inspection_status": "completed" if source else "incomplete",
        "stack": _string_list(source.get("stack")),
        "inspected_directories": _string_list(
            source.get("inspected_directories") or source.get("inspectedDirectories")
        ),
        "entry_files": _string_list(
            source.get("entry_files") or source.get("entryFiles")
        ),
        "conventions": _string_list(source.get("conventions")),
        "summary": _text(
            source.get("summary"),
            "Main Agent 返回中缺少可解析的工作目录检查摘要，已使用 ProjectPlan 兜底拆分任务。",
        ),
    }


def _data_source_task(data_source: dict[str, Any]) -> dict[str, Any]:
    task_id = f"data_source:{data_source['id']}"
    description = f"生成数据源 {data_source['name']}、API 契约和示例数据。"
    target_files = ["app/backend/**", "app/shared/api/**", "tests/backend/**"]
    acceptance_criteria = [
        f"数据源 {data_source['id']} 的实体模型被创建。",
        "相关 API 路径与 ProjectPlan 中的 API 契约一致。",
        "提供可用于页面联调的示例数据。",
    ]
    return {
        "id": task_id,
        "task_id": task_id,
        "owner": "data_source",
        "title": f"实现数据源 {data_source['name']}",
        "description": description,
        "dependencies": [],
        "dependsOn": [],
        "status": "pending",
        "source_ref": {
            "type": "data_source",
            "id": data_source["id"],
        },
        "allowed_paths": [
            "app/backend/**",
            "app/shared/api/**",
            "tests/backend/**",
        ],
        "targetFiles": target_files,
        "change_scope": _change_scope([], target_files),
        "impact_scope": _impact_scope({}, description),
        "canRunInParallel": True,
        "can_run_in_parallel": True,
        "parallel_reason": "不与其他任务修改相同文件且依赖满足时可并行。",
        "verification_commands": [],
        "acceptance_criteria": acceptance_criteria,
        "acceptanceCriteria": acceptance_criteria,
    }


def _frontend_task(page_detail_plan: dict[str, Any]) -> dict[str, Any]:
    data_source_dependencies = [
        f"data_source:{source['id']}" for source in page_detail_plan.get("data_sources", [])
    ]
    task_id = f"page:{page_detail_plan['page_id']}"
    description = f"生成页面 {page_detail_plan['page_name']}（{page_detail_plan['path']}）。"
    target_files = ["app/frontend/**", "app/shared/api/**", "tests/frontend/**"]
    acceptance_criteria = page_detail_plan["acceptance_criteria"]
    return {
        "id": task_id,
        "task_id": task_id,
        "owner": "frontend",
        "title": f"实现页面 {page_detail_plan['page_name']}",
        "description": description,
        "dependencies": data_source_dependencies,
        "dependsOn": data_source_dependencies,
        "status": "pending",
        "source_ref": {
            "type": "page_detail_plan",
            "id": page_detail_plan["id"],
            "page_id": page_detail_plan["page_id"],
        },
        "allowed_paths": [
            "app/frontend/**",
            "app/shared/api/**",
            "tests/frontend/**",
        ],
        "targetFiles": target_files,
        "change_scope": _change_scope([], target_files),
        "impact_scope": _impact_scope({}, description),
        "canRunInParallel": True,
        "can_run_in_parallel": True,
        "parallel_reason": "不与其他任务修改相同文件且依赖满足时可并行。",
        "verification_commands": [],
        "acceptance_criteria": acceptance_criteria,
        "acceptanceCriteria": acceptance_criteria,
    }


def _normalize_agent_tasks(raw_tasks: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tasks, list):
        return []
    tasks: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue
        base_id = _text(item.get("id") or item.get("task_id"), f"task-{index:03d}")
        task_id = base_id
        suffix = 2
        while task_id in used_ids:
            task_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(task_id)

        owner = _text(item.get("owner"), "frontend")
        if owner not in {"frontend", "data_source"}:
            owner = "data_source" if owner in {"backend", "data-source", "data"} else "frontend"
        description = _text(item.get("description"), _text(item.get("title"), task_id))
        target_files = _string_list(item.get("targetFiles") or item.get("target_files"))
        change_scope = _change_scope(
            item.get("change_scope") or item.get("changeScope"), target_files
        )
        if not target_files:
            target_files = [change["path"] for change in change_scope]
        dependencies = _string_list(item.get("dependencies") or item.get("dependsOn"))
        acceptance = _string_list(
            item.get("acceptance_criteria") or item.get("acceptanceCriteria")
        )
        if not acceptance:
            acceptance = [f"{description}完成并通过相关构建或测试验证。"]
        can_parallel = bool(
            item.get("can_run_in_parallel", item.get("canRunInParallel", True))
        )
        allowed_paths = (
            _string_list(item.get("allowed_paths") or item.get("allowedPaths"))
            or target_files
        )
        tasks.append(
            {
                "id": task_id,
                "task_id": task_id,
                "owner": owner,
                "type": "backend" if owner == "data_source" else "frontend",
                "title": _text(item.get("title"), description),
                "description": description,
                "dependencies": dependencies,
                "dependsOn": dependencies,
                "status": "pending",
                "source_ref": (
                    item.get("source_ref")
                    if isinstance(item.get("source_ref"), dict)
                    else {}
                ),
                "allowed_paths": allowed_paths,
                "targetFiles": target_files,
                "change_scope": change_scope,
                "impact_scope": _impact_scope(
                    item.get("impact_scope") or item.get("impactScope"), description
                ),
                "canRunInParallel": can_parallel,
                "can_run_in_parallel": can_parallel,
                "parallel_reason": _text(
                    item.get("parallel_reason") or item.get("parallelReason"),
                    "依赖满足且目标文件不冲突时可并行。",
                ),
                "acceptance_criteria": acceptance,
                "acceptanceCriteria": acceptance,
                "verification_commands": _string_list(
                    item.get("verification_commands")
                    or item.get("verificationCommands")
                ),
            }
        )
    return tasks


def _annotate_parallelism(
    tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requested_parallel = {
        task["id"]: bool(task.get("canRunInParallel")) for task in tasks
    }
    annotated = annotate_task_execution(tasks)
    batches = build_execution_batches(annotated)
    parallel_by_task: dict[str, list[str]] = {}
    for batch in batches:
        task_ids = _string_list(batch.get("tasks"))
        if batch.get("mode") != "parallel":
            continue
        for task_id in task_ids:
            parallel_by_task[task_id] = [candidate for candidate in task_ids if candidate != task_id]
    for task in annotated:
        task["can_run_in_parallel"] = bool(task.get("canRunInParallel"))
        task["parallel_with"] = parallel_by_task.get(task["id"], [])
        if not task["can_run_in_parallel"] and requested_parallel.get(task["id"]):
            task["parallel_reason"] = str(
                task.get("directWriteReason") or "调度器检测到文件或契约冲突，必须串行。"
            )
    return annotated, batches


def _deduplicate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[str, dict[str, Any]] = {}
    for task in tasks:
        existing = deduplicated.get(task["id"])
        if existing and _task_status(existing) == "completed":
            continue
        deduplicated[task["id"]] = task
    return list(deduplicated.values())


def create_build_task_plan(
    project_plan: dict[str, Any],
    agent_note: str = "live main-agent build task preparation",
    agent_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create executable build tasks from the Main Agent's ProjectPlan.

    Only confirmed page detail plans are converted into frontend generation
    tasks. Their data-source dependencies are converted into data-source tasks.
    """

    page_detail_plans = project_plan.get("page_detail_plans", [])
    required_data_source_ids = {
        source["id"]
        for detail_plan in page_detail_plans
        for source in detail_plan.get("data_sources", [])
    }
    data_sources = [
        source
        for source in project_plan.get("data_sources", [])
        if source["id"] in required_data_source_ids
    ]

    proposed_tasks = _normalize_agent_tasks((agent_plan or {}).get("tasks"))
    tasks = proposed_tasks or _deduplicate_tasks(
        [
            *[_data_source_task(source) for source in data_sources],
            *[_frontend_task(detail_plan) for detail_plan in page_detail_plans],
        ]
    )
    tasks, execution_batches = _annotate_parallelism(tasks)
    blocked_batches = [
        batch for batch in execution_batches if batch.get("mode") == "blocked"
    ]

    return {
        "version": "0.2.0",
        "status": "blocked" if blocked_batches else "ready",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_project_plan_version": project_plan["version"],
        "task_statuses": list(TASK_STATUSES),
        "workspace_analysis": _workspace_analysis((agent_plan or {}).get("workspace_analysis")),
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "frontend": len([task for task in tasks if task["owner"] == "frontend"]),
            "data_source": len([task for task in tasks if task["owner"] == "data_source"]),
            "pending": len(tasks),
            "running": 0,
            "completed": 0,
            "failed": 0,
        },
        "coordination": {
            "owner": "main-agent",
            "strategy": "Dispatch data-source tasks first, then frontend tasks whose dependencies are completed.",
            "execution_batches": execution_batches,
            "blocked_batches": blocked_batches,
        },
        "agent_note": agent_note,
    }
