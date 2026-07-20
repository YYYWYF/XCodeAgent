from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.task_scheduler import annotate_task_execution, build_execution_batches


TASK_STATUSES = ("pending", "running", "completed", "failed")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
            "主 Agent 返回中缺少可解析的工作目录检查摘要，已使用项目计划兜底拆分任务。",
        ),
    }


def _workspace_analysis_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or not snapshot:
        return _workspace_analysis({})

    entry_files = [
        str(entry.get("path"))
        for entry in snapshot.get("entrypoints", [])
        if isinstance(entry, dict) and entry.get("path")
    ]
    inspected_directories = [
        str(root.get("path"))
        for root in snapshot.get("project_roots", [])
        if isinstance(root, dict) and root.get("path")
    ]
    conventions = [
        f"{command.get('kind')}: {command.get('command')}"
        for command in snapshot.get("build_commands", [])
        if isinstance(command, dict) and command.get("command")
    ]
    return {
        "inspection_status": "completed",
        "workspace_revision": snapshot.get("workspace_revision"),
        "snapshot_schema_version": snapshot.get("schema_version"),
        "stack": _string_list(snapshot.get("tech_stack")),
        "inspected_directories": inspected_directories,
        "entry_files": entry_files,
        "conventions": conventions,
        "summary": "WorkspaceSnapshot provided deterministic project roots, stack, entrypoints, commands, and contract hints before task planning.",
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


def _raw_agent_tasks(agent_plan: dict[str, Any] | None) -> Any:
    if not isinstance(agent_plan, dict):
        return None
    if isinstance(agent_plan.get("tasks"), list):
        return agent_plan["tasks"]
    dag = agent_plan.get("dag")
    if isinstance(dag, dict):
        for key in ("tasks", "nodes"):
            if isinstance(dag.get(key), list):
                return dag[key]
    return None


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


def _topological_order(tasks: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    by_id = {task["id"]: task for task in tasks}
    incoming = {
        task_id: set(_string_list(task.get("dependencies") or task.get("dependsOn")))
        for task_id, task in by_id.items()
    }
    errors = [
        f"Task {task_id} depends on missing task {dependency}."
        for task_id, dependencies in incoming.items()
        for dependency in sorted(dependencies)
        if dependency not in by_id
    ]
    for dependencies in incoming.values():
        dependencies.intersection_update(by_id)

    ready = sorted(task_id for task_id, dependencies in incoming.items() if not dependencies)
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for candidate_id, dependencies in incoming.items():
            if task_id not in dependencies:
                continue
            dependencies.remove(task_id)
            if not dependencies and candidate_id not in order and candidate_id not in ready:
                ready.append(candidate_id)
        ready.sort()

    if len(order) != len(tasks):
        blocked = sorted(set(by_id) - set(order))
        errors.append(f"Task dependency graph contains a cycle involving: {', '.join(blocked)}.")
    return order, errors


def _build_static_dag(
    tasks: list[dict[str, Any]],
    execution_batches: list[dict[str, Any]],
) -> dict[str, Any]:
    task_ids = [task["id"] for task in tasks]
    edges = [
        {"from": dependency, "to": task["id"], "type": "depends_on"}
        for task in tasks
        for dependency in _string_list(task.get("dependencies") or task.get("dependsOn"))
    ]
    incoming = {task_id: 0 for task_id in task_ids}
    outgoing = {task_id: 0 for task_id in task_ids}
    for edge in edges:
        if edge["to"] in incoming:
            incoming[edge["to"]] += 1
        if edge["from"] in outgoing:
            outgoing[edge["from"]] += 1

    topological_order, validation_errors = _topological_order(tasks)
    missing_dependency_errors = [
        f"Task {edge['to']} depends on missing task {edge['from']}."
        for edge in edges
        if edge["from"] not in incoming
    ]
    all_errors = _dedupe_strings([*missing_dependency_errors, *validation_errors])
    return {
        "schema_version": "build-dag.v1",
        "nodes": task_ids,
        "edges": edges,
        "roots": [task_id for task_id in task_ids if incoming[task_id] == 0],
        "leaves": [task_id for task_id in task_ids if outgoing[task_id] == 0],
        "topological_order": topological_order,
        "execution_layers": execution_batches,
        "validation": {
            "is_valid": not all_errors,
            "errors": all_errors,
        },
    }


def create_build_task_plan(
    project_plan: dict[str, Any],
    agent_note: str = "live main-agent build task preparation",
    agent_plan: dict[str, Any] | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize model-produced executable build tasks into a static Build DAG."""

    proposed_tasks = _normalize_agent_tasks(_raw_agent_tasks(agent_plan))
    if not proposed_tasks:
        raise ValueError("Build task model output did not include any valid tasks.")
    tasks = proposed_tasks
    tasks, execution_batches = _annotate_parallelism(tasks)
    dag = _build_static_dag(tasks, execution_batches)
    blocked_batches = [
        batch for batch in execution_batches if batch.get("mode") == "blocked"
    ]

    return {
        "version": "0.3.0",
        "status": (
            "ready"
            if dag["validation"]["is_valid"] and not blocked_batches
            else "blocked"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "source_project_plan_version": project_plan["version"],
        "task_statuses": list(TASK_STATUSES),
        "dag": dag,
        "workspace_analysis": (
            _workspace_analysis((agent_plan or {}).get("workspace_analysis"))
            if (agent_plan or {}).get("workspace_analysis")
            else _workspace_analysis_from_snapshot(workspace_snapshot)
        ),
        "workspace_snapshot_ref": {
            "workspace_revision": (workspace_snapshot or {}).get("workspace_revision"),
            "schema_version": (workspace_snapshot or {}).get("schema_version"),
        },
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
        "prepared_by": {
            "agent": "prepare-build-tasks",
            "mode": "model-normalized",
            "model": None,
            "source": "confirmed_project_plan_and_workspace_snapshot",
        },
        "preparation_source": "confirmed_project_plan_and_workspace_snapshot",
        "agent_note": agent_note,
    }
