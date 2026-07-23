from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from typing import Any

from app.services.task_scheduler import annotate_task_execution, build_execution_batches


logger = logging.getLogger(__name__)


TASK_STATUSES = ("pending", "running", "completed", "failed")


def tasks_from_build_task_plan(build_task_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """按任务图读取完整任务注册表，无效图不得通过部分拓扑序静默丢任务。"""

    registry = build_task_plan.get("task_registry")
    task_graph = build_task_plan.get("task_graph")
    if not isinstance(registry, dict) or not isinstance(task_graph, dict):
        return []
    nodes = _string_list(task_graph.get("nodes"))
    topological_order = _string_list(task_graph.get("topological_order"))
    registry_ids = [str(task_id) for task_id in registry]
    complete_ids = _dedupe_strings([*nodes, *registry_ids])
    validation = task_graph.get("validation")
    is_valid = isinstance(validation, dict) and validation.get("is_valid") is True
    task_ids = (
        topological_order
        if is_valid and set(topological_order) == set(complete_ids)
        else complete_ids
    )
    return [
        dict(registry[task_id])
        for task_id in task_ids
        if isinstance(registry.get(task_id), dict)
    ]


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


def _dedupe_normalized_strings(values: list[str]) -> list[str]:
    """按规范化文本去重模型输出列表，避免同一句验收点或路径重复落库。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized_text_key(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _normalized_text_key(value: str) -> str:
    """生成文本去重键，忽略大小写、空白和常见中英文标点差异。"""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    punctuation = " \t\r\n。．.，,；;：:、!！?？（）()[]【】{}<>《》\"'`"
    return "".join(char for char in text if char not in punctuation)


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
    """把模型返回的候选任务规整为 v2 叶子任务。"""

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
        dependencies = _dedupe_normalized_strings(
            _string_list(item.get("dependencies") or item.get("dependsOn"))
        )
        acceptance = _dedupe_normalized_strings(
            _string_list(item.get("acceptance_criteria") or item.get("acceptanceCriteria"))
        )
        if not acceptance:
            acceptance = [f"{description}完成并通过相关构建或测试验证。"]
        can_parallel = bool(
            item.get("can_run_in_parallel", item.get("canRunInParallel", True))
        )
        allowed_paths = (
            _dedupe_normalized_strings(
                _string_list(item.get("allowed_paths") or item.get("allowedPaths"))
            )
            or target_files
        )
        if not change_scope and not target_files and not allowed_paths:
            logger.info(
                "build_task_plan_excluded_verification_task task_id=%s title=%s",
                task_id,
                _text(item.get("title"), description),
            )
            continue
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
                "unit_id": _text(item.get("unit_id") or item.get("unitId"), "application:root"),
                "source_refs": _dict_value(item.get("source_refs") or item.get("sourceRefs")),
                "allowed_paths": allowed_paths,
                "targetFiles": _dedupe_normalized_strings(target_files),
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
                "verification_commands": _dedupe_normalized_strings(
                    _string_list(
                        item.get("verification_commands")
                        or item.get("verificationCommands")
                    )
                ),
            }
        )
    return tasks


def _raw_agent_tasks(agent_plan: dict[str, Any] | None) -> Any:
    """兼容读取模型输出中的 tasks 或 dag.tasks 候选列表。"""

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
    """为任务补齐并行元信息和执行批次。"""

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
    """对任务依赖执行拓扑排序，并返回缺失依赖或环路错误。"""

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


def _build_task_graph(
    tasks: list[dict[str, Any]],
    execution_batches: list[dict[str, Any]],
) -> dict[str, Any]:
    """根据叶子任务构造可校验的任务 DAG。"""

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
        "schema_version": "build-task-graph.v2",
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


def _task_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """按叶子任务状态和执行所有者计算 v2 计划摘要。"""

    return {
        "total": len(tasks),
        "frontend": len([task for task in tasks if task.get("owner") == "frontend"]),
        "data_source": len([task for task in tasks if task.get("owner") == "data_source"]),
        "pending": len([task for task in tasks if task.get("status") == "pending"]),
        "running": len([task for task in tasks if task.get("status") == "running"]),
        "completed": len([task for task in tasks if task.get("status") == "completed"]),
        "failed": len([task for task in tasks if task.get("status") == "failed"]),
    }


def replace_build_task_plan_tasks(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """用最新叶子任务重建 v2 注册表、任务图和执行批次。"""

    normalized_tasks = [
        {
            **task,
            "unit_id": _text(task.get("unit_id"), "application:root"),
            "source_refs": (
                task.get("source_refs") if isinstance(task.get("source_refs"), dict) else {}
            ),
        }
        for task in tasks
    ]
    annotated_tasks, execution_batches = _annotate_parallelism(normalized_tasks)
    build_units = deepcopy(
        build_task_plan.get("build_units")
        if isinstance(build_task_plan.get("build_units"), dict)
        else {}
    )
    for unit_id, unit in build_units.items():
        if isinstance(unit, dict):
            unit["task_ids"] = [
                task["id"] for task in annotated_tasks if task.get("unit_id") == unit_id
            ]
    return {
        **build_task_plan,
        "build_units": build_units,
        "task_registry": {task["id"]: task for task in annotated_tasks},
        "task_graph": _build_task_graph(annotated_tasks, execution_batches),
        "summary": {
            **(
                build_task_plan.get("summary")
                if isinstance(build_task_plan.get("summary"), dict)
                else {}
            ),
            **_task_summary(annotated_tasks),
        },
        "execution": {
            **(
                build_task_plan.get("execution")
                if isinstance(build_task_plan.get("execution"), dict)
                else {}
            ),
            "batches": execution_batches,
            "blocked_batches": [
                batch for batch in execution_batches if batch.get("mode") == "blocked"
            ],
        },
    }


def compile_build_task_plan_scope(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 Unit 依赖、来源引用和输入指纹编译进任务图。"""

    context = build_context if isinstance(build_context, dict) else {}
    scoped_tasks = _apply_unit_compilation(
        build_task_plan,
        tasks,
        context,
    )
    compiled = replace_build_task_plan_tasks(build_task_plan, scoped_tasks)
    compiled["build_units"] = _annotate_unit_inputs(
        compiled.get("build_units"),
        context,
        compiled.get("task_registry"),
    )
    return compiled


def create_build_task_plan(
    project_plan: dict[str, Any],
    agent_note: str = "live main-agent build task preparation",
    agent_plan: dict[str, Any] | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    base_build_task_plan: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将模型候选任务归一化并合并到已有全局 Unit 骨架。"""

    raw_tasks = _raw_agent_tasks(agent_plan)
    proposed_tasks = _normalize_agent_tasks(raw_tasks)
    logger.info(
        "build_task_plan_normalization parsed_keys=%s raw_tasks_type=%s raw_tasks_count=%s "
        "valid_tasks_count=%s valid_task_ids=%s",
        sorted(str(key) for key in agent_plan) if isinstance(agent_plan, dict) else [],
        type(raw_tasks).__name__,
        len(raw_tasks) if isinstance(raw_tasks, list) else 0,
        len(proposed_tasks),
        [task["id"] for task in proposed_tasks],
    )
    if not proposed_tasks:
        logger.warning(
            "build_task_plan_no_valid_tasks parsed_keys=%s raw_tasks_type=%s raw_tasks_count=%s",
            sorted(str(key) for key in agent_plan) if isinstance(agent_plan, dict) else [],
            type(raw_tasks).__name__,
            len(raw_tasks) if isinstance(raw_tasks, list) else 0,
        )
        raise ValueError("Build task model output did not include any valid tasks.")
    base_plan = deepcopy(base_build_task_plan or {})
    tasks = _apply_unit_compilation(base_plan, proposed_tasks, build_context or {})
    tasks, execution_batches = _annotate_parallelism(tasks)
    task_graph = _build_task_graph(tasks, execution_batches)
    blocked_batches = [
        batch for batch in execution_batches if batch.get("mode") == "blocked"
    ]

    plan = {
        **base_plan,
        "version": "2.0.0",
        "schema_version": "build-dag.v2",
        "status": (
            "ready"
            if task_graph["validation"]["is_valid"] and not blocked_batches
            else "blocked"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "source_project_plan_version": project_plan["version"],
        "application": base_plan.get("application") or {"unit_id": "application:root", "status": "prepared"},
        "build_units": base_plan.get("build_units") or {
            "application:root": {
                "id": "application:root",
                "kind": "application",
                "status": "prepared",
                "task_ids": [task["id"] for task in tasks],
                "depends_on_unit_ids": [],
                "source_refs": {},
            }
        },
        "unit_graph": base_plan.get("unit_graph") or {
            "schema_version": "build-unit-graph.v2",
            "nodes": ["application:root"],
            "edges": [],
            "validation": {"is_valid": True, "errors": []},
        },
        "execution_history": base_plan.get("execution_history") or [],
        "workspace_analysis": (
            _workspace_analysis((agent_plan or {}).get("workspace_analysis"))
            if (agent_plan or {}).get("workspace_analysis")
            else _workspace_analysis_from_snapshot(workspace_snapshot)
        ),
        "workspace_snapshot_ref": {
            "workspace_revision": (workspace_snapshot or {}).get("workspace_revision"),
            "schema_version": (workspace_snapshot or {}).get("schema_version"),
        },
        "summary": _task_summary(tasks),
        "execution": {
            "owner": "main-agent",
            "strategy": "Dispatch data-source tasks first, then frontend tasks whose dependencies are completed.",
            "batches": execution_batches,
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
    return compile_build_task_plan_scope(plan, tasks, build_context)


def _apply_unit_compilation(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """补齐任务 Unit 来源，并把 Unit depends_on 边落成任务依赖边。"""

    units = build_task_plan.get("build_units")
    units = units if isinstance(units, dict) else {}
    with_sources = [
        _with_task_unit_metadata(task, units, build_context)
        for task in tasks
    ]
    return _apply_unit_task_dependencies(with_sources, units, build_task_plan.get("unit_graph"))


def _with_task_unit_metadata(
    task: dict[str, Any],
    units: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """为单个任务补齐稳定 Unit、source_refs 和 capability 字段。"""

    unit_id = _text(task.get("unit_id"), "application:root")
    unit = units.get(unit_id) if isinstance(units.get(unit_id), dict) else {}
    source_refs = _dict_value(task.get("source_refs"))
    if not source_refs:
        source_refs = _unit_source_refs(unit_id, unit, build_context)
    task_with_refs = {
        **task,
        "unit_id": unit_id,
        "source_refs": source_refs,
        "requires_capabilities": _dedupe_strings(
            _string_list(task.get("requires_capabilities") or task.get("requiresCapabilities"))
        ),
        "provides_capabilities": _dedupe_strings(
            _string_list(task.get("provides_capabilities") or task.get("providesCapabilities"))
            or [unit_id]
        ),
    }
    return task_with_refs


def _apply_unit_task_dependencies(
    tasks: list[dict[str, Any]],
    units: dict[str, Any],
    unit_graph: Any,
) -> list[dict[str, Any]]:
    """仅保留同 Unit 显式依赖，并以 Unit Graph 编译唯一的跨 Unit 依赖。"""

    tasks_by_unit: dict[str, list[str]] = {}
    task_ids = {str(task.get("id") or "") for task in tasks if task.get("id")}
    task_units = {
        str(task.get("id")): str(task.get("unit_id") or "application:root")
        for task in tasks
        if task.get("id")
    }
    for task in tasks:
        tasks_by_unit.setdefault(str(task.get("unit_id") or "application:root"), []).append(
            str(task.get("id"))
        )
    dependency_units = _unit_dependency_map(unit_graph)
    result: list[dict[str, Any]] = []
    for task in tasks:
        unit_id = str(task.get("unit_id") or "application:root")
        inherited_dependencies = [
            dependency_task_id
            for dependency_unit_id in dependency_units.get(unit_id, [])
            for dependency_task_id in tasks_by_unit.get(dependency_unit_id, [])
            if dependency_task_id and dependency_task_id != task.get("id")
        ]
        explicit_dependencies = _string_list(
            task.get("dependencies") or task.get("dependsOn")
        )
        removed_cross_unit_dependencies = [
            dependency
            for dependency in explicit_dependencies
            if dependency in task_units and task_units[dependency] != unit_id
        ]
        same_unit_or_unknown_dependencies = [
            dependency
            for dependency in explicit_dependencies
            if dependency not in task_units or task_units[dependency] == unit_id
        ]
        dependencies = _dedupe_strings(
            [*same_unit_or_unknown_dependencies, *inherited_dependencies]
        )
        existing_rewrites = [
            rewrite
            for rewrite in task.get("dependency_rewrites", [])
            if isinstance(rewrite, dict)
        ]
        result.append(
            {
                **task,
                "dependencies": dependencies,
                "dependsOn": dependencies,
                "unit_dependencies": dependency_units.get(unit_id, []),
                "dependency_rewrites": [
                    *existing_rewrites,
                    *[
                        {
                            "dependency": dependency,
                            "reason": "unit_graph_authoritative",
                            "from_unit_id": task_units.get(dependency),
                            "to_unit_id": unit_id,
                        }
                        for dependency in removed_cross_unit_dependencies
                    ],
                ],
                "missing_unit_dependencies": [
                    dependency_unit_id
                    for dependency_unit_id in dependency_units.get(unit_id, [])
                    if dependency_unit_id in units
                    and not tasks_by_unit.get(dependency_unit_id)
                ],
                "invalid_dependencies": [
                    dependency for dependency in dependencies if dependency not in task_ids
                ],
            }
        )
    return result


def _unit_dependency_map(unit_graph: Any) -> dict[str, list[str]]:
    """从 Unit Graph 中提取 depends_on 边的反向依赖表。"""

    graph = unit_graph if isinstance(unit_graph, dict) else {}
    result: dict[str, list[str]] = {}
    for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if not isinstance(edge, dict) or edge.get("type") != "depends_on":
            continue
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if source and target:
            result.setdefault(target, []).append(source)
    return {unit_id: _dedupe_strings(dependencies) for unit_id, dependencies in result.items()}


def _annotate_unit_inputs(
    build_units_value: Any,
    build_context: dict[str, Any],
    task_registry_value: Any,
) -> dict[str, dict[str, Any]]:
    """为本次范围内的 Unit 记录来源引用、输入指纹和准备状态。"""

    build_units = deepcopy(build_units_value if isinstance(build_units_value, dict) else {})
    task_registry = task_registry_value if isinstance(task_registry_value, dict) else {}
    required_unit_ids = set(_string_list(build_context.get("required_unit_ids")))
    for unit_id, unit in build_units.items():
        if not isinstance(unit, dict):
            continue
        task_ids = _string_list(unit.get("task_ids"))
        if unit_id in required_unit_ids:
            source_refs = _unit_source_refs(unit_id, unit, build_context)
            unit["source_refs"] = source_refs
            unit["input_fingerprint"] = _stable_fingerprint(
                _unit_fingerprint_payload(unit_id, source_refs, build_context)
            )
            unit["status"] = "prepared" if task_ids else "not_prepared"
        unit["task_ids"] = [
            task_id for task_id in task_ids if isinstance(task_registry.get(task_id), dict)
        ]
    return build_units


def _unit_source_refs(
    unit_id: str,
    unit: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """按 Unit 类型映射到 ProjectPlan、PageDetail 或 EndpointDetail 来源。"""

    existing = _dict_value(unit.get("source_refs"))
    target = _dict_value(build_context.get("target"))
    refs = _dict_value(build_context.get("source_refs"))
    if unit_id.startswith("page:"):
        return {
            **existing,
            "type": "page_detail",
            "target": target,
            "page_detail": _dict_value(refs.get("page_detail")),
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
        }
    if unit_id.startswith("data-source:"):
        return {
            **existing,
            "type": "endpoint_detail",
            "target": target,
            "endpoint_details": _matching_endpoint_refs(
                refs.get("endpoint_details"),
                _string_list(build_context.get("endpoint_ids")),
            ),
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
        }
    return {
        **existing,
        "type": "application_unit",
        "target": {"type": "application", "id": "application"},
    }


def _unit_fingerprint_payload(
    unit_id: str,
    source_refs: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """按 Unit 类型选择定向失效所需的最小输入集合。"""

    if unit_id.startswith("page:"):
        return {
            "unit_id": unit_id,
            "source_refs": source_refs,
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "data_source_ids": _string_list(build_context.get("data_source_ids")),
        }
    if unit_id.startswith("data-source:"):
        return {
            "unit_id": unit_id,
            "source_refs": source_refs,
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
        }
    return {
        "unit_id": unit_id,
        "source_refs": source_refs,
    }


def _matching_endpoint_refs(value: Any, endpoint_ids: list[str]) -> list[dict[str, Any]]:
    """在当前构建上下文中查找指定 endpoint 详情引用。"""

    if not isinstance(value, list):
        return []
    allowed_ids = set(endpoint_ids)
    return [
        dict(item)
        for item in value
        if isinstance(item, dict) and str(item.get("id") or "") in allowed_ids
    ]


def _dict_value(value: Any) -> dict[str, Any]:
    """将不可信输入规整为字典，便于后续合并元数据。"""

    return dict(value) if isinstance(value, dict) else {}


def _stable_fingerprint(value: Any) -> str:
    """为 Unit 局部输入生成稳定哈希，支持后续定向失效。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()
