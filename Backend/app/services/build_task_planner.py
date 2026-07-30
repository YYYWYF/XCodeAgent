from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any

from app.services.build_task_menu import (
    ensure_page_route_registration_task,
    reconcile_live_page_paths,
)
from app.services.build_unit_compiler import (
    annotate_unit_inputs,
    apply_unit_compilation,
)
from app.services.task_scheduler import annotate_task_execution, build_execution_batches


logger = logging.getLogger(__name__)


TASK_STATUSES = ("pending", "running", "completed", "failed", "already_satisfied")
_HIGH_RISK_DATABASE_OPERATIONS = {
    "drop_table",
    "drop_column",
    "delete_data",
    "truncate",
    "drop",
    "delete",
}
_CODE_PATH_SUFFIXES = (
    ".java",
    ".kt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".vue",
    ".less",
    ".css",
)


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


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表收敛为字典列表。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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


def _task_target_files(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的目标文件。"""

    return _string_list(task.get("target_files"))


def _task_dependencies(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的依赖列表。"""

    return _string_list(task.get("dependencies"))


def _task_can_run_in_parallel(task: dict[str, Any]) -> bool:
    """读取当前 DAG v3 任务的并行标记。"""

    return bool(task.get("can_run_in_parallel", True))


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
    """把模型返回的候选任务规整为 v3 叶子任务。"""

    if not isinstance(raw_tasks, list):
        return []
    tasks: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue
        base_id = _text(item.get("id"), f"task-{index:03d}")
        task_id = base_id
        suffix = 2
        while task_id in used_ids:
            task_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(task_id)

        owner = _text(item.get("owner"), "frontend")
        if owner not in {"frontend", "backend", "database"}:
            owner = (
                "database"
                if owner in {"data_source", "data-source", "data", "db"}
                else "backend"
                if owner in {"api", "server"}
                else "frontend"
            )
        default_task_type = (
            "database.change"
            if owner == "database"
            else "backend.code"
            if owner == "backend"
            else "frontend.code"
        )
        description = _text(item.get("description"), _text(item.get("title"), task_id))
        target_files = _string_list(item.get("target_files"))
        change_scope = _change_scope(item.get("change_scope"), target_files)
        if not target_files:
            target_files = [change["path"] for change in change_scope]
        dependencies = _dedupe_normalized_strings(
            _string_list(item.get("dependencies"))
        )
        acceptance = _dedupe_normalized_strings(
            _string_list(item.get("acceptance_criteria"))
        )
        if not acceptance:
            acceptance = [f"{description}完成并通过相关构建或测试验证。"]
        can_parallel = bool(item.get("can_run_in_parallel", True))
        database_scope = _dict_value(item.get("database_scope"))
        allowed_paths = (
            _dedupe_normalized_strings(
                _string_list(item.get("allowed_paths"))
            )
            or target_files
        )
        if (
            owner != "database"
            and not change_scope
            and not target_files
            and not allowed_paths
            and not database_scope
        ):
            logger.info(
                "build_task_plan_excluded_verification_task task_id=%s title=%s",
                task_id,
                _text(item.get("title"), description),
            )
            continue
        tasks.append(
            {
                "id": task_id,
                "owner": owner,
                "task_type": _text(
                    item.get("task_type"),
                    default_task_type,
                ),
                "title": _text(item.get("title"), description),
                "description": description,
                "dependencies": dependencies,
                "status": "pending",
                "unit_id": _text(item.get("unit_id"), "application:root"),
                "source_refs": _dict_value(item.get("source_refs")),
                "requires_capabilities": _string_list(
                    item.get("requires_capabilities")
                ),
                "provides_capabilities": _string_list(
                    item.get("provides_capabilities")
                ),
                "database_scope": database_scope,
                "risk": _text(item.get("risk"), "low"),
                "approval": _dict_value(item.get("approval")),
                "allowed_paths": allowed_paths,
                "target_files": _dedupe_normalized_strings(target_files),
                "change_scope": change_scope,
                "impact_scope": _impact_scope(
                    item.get("impact_scope"), description
                ),
                "can_run_in_parallel": can_parallel,
                "parallel_reason": _text(
                    item.get("parallel_reason"),
                    "依赖满足且目标文件不冲突时可并行。",
                ),
                "acceptance_criteria": acceptance,
                "verification_commands": _dedupe_normalized_strings(
                    _string_list(item.get("verification_commands"))
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


def _drop_unneeded_database_change_tasks(
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """在新版 schema gaps 为空时移除多余 database.change 候选任务。"""

    database_context = _dict_value(build_context.get("database_planning_context"))
    if database_context.get("status") != "completed":
        return tasks
    if _dict_items(database_context.get("gaps")):
        return tasks
    retained: list[dict[str, Any]] = []
    for task in tasks:
        if (
            task.get("owner") == "database"
            and str(task.get("task_type") or "") == "database.change"
            and not any(_is_code_path(path) for path in _task_declared_paths(task))
        ):
            logger.info(
                "build_task_plan_dropped_unneeded_database_change task_id=%s",
                task.get("id"),
            )
            continue
        retained.append(task)
    return retained


def _complete_database_task_scopes(
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """用唯一数据库 gap 意图补齐模型遗漏的 database_scope。"""

    database_context = _dict_value(build_context.get("database_planning_context"))
    if database_context.get("status") != "completed":
        return tasks
    intents = _dict_items(database_context.get("task_intents"))
    if not intents:
        return tasks
    available_intents = [
        intent
        for intent in intents
        if _dict_value(intent.get("database_scope"))
        and _string_list(intent.get("gap_ids"))
    ]
    result: list[dict[str, Any]] = []
    covered_gap_ids = _covered_database_gap_ids(tasks)
    for task in tasks:
        if (
            task.get("owner") == "database"
            and not _dict_value(task.get("database_scope"))
            and not any(_is_code_path(path) for path in _task_declared_paths(task))
        ):
            candidates = [
                intent
                for intent in available_intents
                if not set(_string_list(intent.get("gap_ids"))).issubset(covered_gap_ids)
            ]
            if len(candidates) == 1:
                intent = candidates[0]
                completed = {
                    **task,
                    "database_scope": _dict_value(intent.get("database_scope")),
                    "source_refs": {
                        **_dict_value(task.get("source_refs")),
                        "type": "database_context_gap",
                        "gap_ids": _string_list(intent.get("gap_ids")),
                    },
                    "risk": _text(task.get("risk"), _text(intent.get("risk"), "low")),
                    "task_type": _text(
                        task.get("task_type"),
                        _text(intent.get("task_type"), "database.change"),
                    ),
                }
                covered_gap_ids.update(_string_list(intent.get("gap_ids")))
                result.append(completed)
                continue
            logger.info(
                "build_task_plan_kept_unscoped_database_task task_id=%s reason=no_unique_gap",
                task.get("id"),
            )
        result.append(task)
    return result


def _ensure_database_intent_tasks(
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """把数据库上下文中的确定性任务意图补进模型候选任务列表。"""

    database_context = _dict_value(build_context.get("database_planning_context"))
    if database_context.get("status") != "completed":
        return tasks
    intents = _dict_items(database_context.get("task_intents"))
    if not intents:
        return tasks
    covered_gap_ids = _covered_database_gap_ids(tasks)
    result = list(tasks)
    for index, intent in enumerate(intents, start=1):
        gap_ids = _string_list(intent.get("gap_ids"))
        if gap_ids and all(gap_id in covered_gap_ids for gap_id in gap_ids):
            continue
        result.append(_database_task_from_intent(intent, index, build_context))
        covered_gap_ids.update(gap_ids)
    return result


def _covered_database_gap_ids(tasks: list[dict[str, Any]]) -> set[str]:
    """统计当前数据库任务已经覆盖的 gap id。"""

    return {
        str(gap_id)
        for task in tasks
        if task.get("owner") == "database"
        for gap_id in _string_list(_dict_value(task.get("database_scope")).get("gap_ids"))
    }


def _database_task_from_intent(
    intent: dict[str, Any],
    index: int,
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """把单个数据库任务意图转换成 DAG v3 叶子任务。"""

    task_id = str(intent.get("id") or f"database-gap-{index:03d}")
    description = _text(intent.get("description"), "补齐数据库结构以满足接口需求。")
    data_source_ids = _string_list(build_context.get("data_source_ids"))
    unit_id = f"database:{data_source_ids[0]}" if data_source_ids else "database:default"
    database_scope = _dict_value(intent.get("database_scope"))
    database_scope["gap_ids"] = _string_list(intent.get("gap_ids"))
    return {
        "id": task_id,
        "owner": "database",
        "task_type": _text(intent.get("task_type"), "database.change"),
        "title": description,
        "description": description,
        "dependencies": [],
        "status": "pending",
        "unit_id": unit_id,
        "source_refs": {
            "type": "database_context_gap",
            "gap_ids": _string_list(intent.get("gap_ids")),
        },
        "requires_capabilities": [],
        "provides_capabilities": [unit_id],
        "database_scope": database_scope,
        "risk": _text(intent.get("risk"), "low"),
        "approval": {"required": _text(intent.get("risk"), "low") == "high"},
        "allowed_paths": [],
        "target_files": [],
        "change_scope": [],
        "impact_scope": _impact_scope({}, description),
        "can_run_in_parallel": False,
        "parallel_reason": "数据库结构变更按同一连接串行执行。",
        "acceptance_criteria": [
            "数据库上下文复查时对应 schema gap 已消除。",
        ],
        "verification_commands": [],
    }


def _annotate_parallelism(
    tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """为任务补齐并行元信息和执行批次。"""

    requested_parallel = {
        task["id"]: _task_can_run_in_parallel(task) for task in tasks
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
        task["can_run_in_parallel"] = _task_can_run_in_parallel(task)
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
        task_id: set(_task_dependencies(task))
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
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据叶子任务构造可校验的任务 DAG。"""

    task_ids = [task["id"] for task in tasks]
    edges = [
        {"from": dependency, "to": task["id"], "type": "depends_on"}
        for task in tasks
        for dependency in _task_dependencies(task)
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
    semantic_errors = _task_semantic_errors(tasks, build_context or {})
    all_errors = _dedupe_strings(
        [*missing_dependency_errors, *validation_errors, *semantic_errors]
    )
    return {
        "schema_version": "build-task-graph.v3",
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


def _task_semantic_errors(
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[str]:
    """校验 DAG 拓扑之外的 owner、Unit、数据库职责和审批语义。"""

    errors: list[str] = []
    database_context = _dict_value(build_context.get("database_planning_context"))
    database_context_completed = database_context.get("status") == "completed"
    for task in tasks:
        task_id = str(task.get("id") or "")
        owner = str(task.get("owner") or "")
        unit_id = str(task.get("unit_id") or "")
        task_type = str(task.get("task_type") or "")
        paths = _task_declared_paths(task)
        if unit_id.startswith("database:") and owner != "database":
            errors.append(f"Task {task_id} is in database Unit {unit_id} but owner is {owner}.")
        if unit_id.startswith("backend:") and owner != "backend":
            errors.append(f"Task {task_id} is in backend Unit {unit_id} but owner is {owner}.")
        if unit_id.startswith(("page:", "frontend:")) and owner != "frontend":
            errors.append(f"Task {task_id} is in frontend/page Unit {unit_id} but owner is {owner}.")
        if owner == "database":
            errors.extend(
                _database_task_semantic_errors(
                    task,
                    paths=paths,
                    database_context_completed=database_context_completed,
                    database_context=database_context,
                )
            )
        elif task.get("database_scope"):
            errors.append(f"Task {task_id} is {owner} owner but declares database_scope.")
        if owner == "backend" and task_type.startswith("database."):
            errors.append(f"Task {task_id} is backend owner but declares database task_type {task_type}.")
    return errors


def _database_task_semantic_errors(
    task: dict[str, Any],
    *,
    paths: list[str],
    database_context_completed: bool,
    database_context: dict[str, Any],
) -> list[str]:
    """校验 database task 只能处理数据库，不能混入代码修改。"""

    task_id = str(task.get("id") or "")
    task_type = str(task.get("task_type") or "")
    errors: list[str] = []
    if task_type not in {"database.change", "database.seed", "database.verify"}:
        errors.append(f"Database task {task_id} has invalid task_type {task_type}.")
    if not isinstance(task.get("database_scope"), dict) or not task.get("database_scope"):
        match_count = len(
            [
                intent
                for intent in _dict_items(database_context.get("task_intents"))
                if _dict_value(intent.get("database_scope"))
            ]
        )
        errors.append(
            f"Database task {task_id} must declare non-empty database_scope; "
            f"matched_database_change_gap_count={match_count}."
        )
    code_paths = [path for path in paths if _is_code_path(path)]
    if code_paths:
        errors.append(
            f"Database task {task_id} must not modify code files: {', '.join(code_paths)}."
        )
    if not database_context_completed:
        errors.append(f"Database task {task_id} requires completed database-context.v1.")
    if _database_task_requires_approval(task) and not _approval_required(task):
        errors.append(f"High-risk database task {task_id} must require user approval.")
    return errors


def _task_declared_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务声明的所有文件路径，供职责校验使用。"""

    paths = [*_task_target_files(task)]
    paths.extend(_string_list(task.get("allowed_paths")))
    for change in task.get("change_scope") if isinstance(task.get("change_scope"), list) else []:
        if isinstance(change, dict) and change.get("path"):
            paths.append(str(change.get("path")))
    return _dedupe_normalized_strings(paths)


def _is_code_path(path: str) -> bool:
    """判断路径是否属于代码或前端样式文件，database task 不允许修改。"""

    normalized = path.lower()
    return normalized.endswith(_CODE_PATH_SUFFIXES)


def _database_task_requires_approval(task: dict[str, Any]) -> bool:
    """识别删除、截断等高危数据库操作是否需要人工审批。"""

    scope = _dict_value(task.get("database_scope"))
    raw_operations = scope.get("operations") or scope.get("operation") or []
    if isinstance(raw_operations, str):
        raw_operations = [raw_operations]
    operations = [str(item).strip().lower() for item in raw_operations if str(item).strip()]
    text = json.dumps(scope, ensure_ascii=False, default=str).lower()
    return any(operation in _HIGH_RISK_DATABASE_OPERATIONS for operation in operations) or any(
        keyword in text for keyword in _HIGH_RISK_DATABASE_OPERATIONS
    )


def _approval_required(task: dict[str, Any]) -> bool:
    """读取任务审批标记。"""

    approval = task.get("approval")
    return isinstance(approval, dict) and approval.get("required") is True


def _task_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """按叶子任务状态和执行所有者计算 v3 计划摘要。"""

    return {
        "total": len(tasks),
        "frontend": len([task for task in tasks if task.get("owner") == "frontend"]),
        "backend": len([task for task in tasks if task.get("owner") == "backend"]),
        "database": len([task for task in tasks if task.get("owner") == "database"]),
        "pending": len([task for task in tasks if task.get("status") == "pending"]),
        "running": len([task for task in tasks if task.get("status") == "running"]),
        "completed": len(
            [
                task
                for task in tasks
                if task.get("status") in {"completed", "already_satisfied"}
            ]
        ),
        "already_satisfied": len(
            [task for task in tasks if task.get("status") == "already_satisfied"]
        ),
        "failed": len([task for task in tasks if task.get("status") == "failed"]),
    }


def replace_build_task_plan_tasks(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用最新叶子任务重建 v2 注册表、任务图和执行批次。"""

    normalized_tasks = [_canonical_task(task) for task in tasks]
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
        "task_graph": _build_task_graph(
            annotated_tasks,
            execution_batches,
            build_context or {},
        ),
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


def _canonical_task(task: dict[str, Any]) -> dict[str, Any]:
    """把任务对象收敛为 DAG v3 的 snake_case 单一字段形态。"""

    canonical = dict(task)
    canonical["id"] = _text(task.get("id"), "task")
    canonical["unit_id"] = _text(task.get("unit_id"), "application:root")
    canonical["task_type"] = _text(
        task.get("task_type"),
        _default_task_type(str(task.get("owner") or "")),
    )
    canonical["dependencies"] = _dedupe_strings(_task_dependencies(task))
    canonical["target_files"] = _dedupe_normalized_strings(_task_target_files(task))
    canonical["can_run_in_parallel"] = _task_can_run_in_parallel(task)
    canonical["acceptance_criteria"] = _dedupe_normalized_strings(
        _string_list(task.get("acceptance_criteria"))
    )
    canonical["source_refs"] = (
        task.get("source_refs") if isinstance(task.get("source_refs"), dict) else {}
    )
    return canonical


def _default_task_type(owner: str) -> str:
    """根据 owner 生成默认任务类型。"""

    if owner == "database":
        return "database.change"
    if owner == "backend":
        return "backend.code"
    return "frontend.code"


def compile_build_task_plan_scope(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 Unit 依赖、来源引用和输入指纹编译进任务图。"""

    context = build_context if isinstance(build_context, dict) else {}
    scoped_tasks = apply_unit_compilation(
        build_task_plan,
        tasks,
        context,
    )
    compiled = replace_build_task_plan_tasks(build_task_plan, scoped_tasks, context)
    compiled["build_units"] = annotate_unit_inputs(
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
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """将模型候选任务归一化并合并到已有全局 Unit 骨架。"""

    raw_tasks = _raw_agent_tasks(agent_plan)
    proposed_tasks = _normalize_agent_tasks(raw_tasks)
    context = build_context or {}
    proposed_tasks = reconcile_live_page_paths(
        proposed_tasks,
        workspace_root=workspace_root,
        build_context=context,
    )
    proposed_tasks = ensure_page_route_registration_task(
        proposed_tasks,
        project_plan=project_plan,
        workspace_root=workspace_root,
        build_context=context,
    )
    proposed_tasks = _drop_unneeded_database_change_tasks(proposed_tasks, context)
    proposed_tasks = _complete_database_task_scopes(proposed_tasks, context)
    proposed_tasks = _ensure_database_intent_tasks(proposed_tasks, context)
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
    tasks = apply_unit_compilation(base_plan, proposed_tasks, context)
    tasks, execution_batches = _annotate_parallelism(tasks)
    task_graph = _build_task_graph(tasks, execution_batches, context)
    blocked_batches = [
        batch for batch in execution_batches if batch.get("mode") == "blocked"
    ]

    plan = {
        **base_plan,
        "version": "3.0.0",
        "schema_version": "build-dag.v3",
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
            "schema_version": "build-unit-graph.v3",
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
            "strategy": "Dispatch database tasks before backend tasks, then frontend tasks whose dependencies are completed.",
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
        "agent_note": _compact_agent_note(agent_note, agent_plan),
    }
    return compile_build_task_plan_scope(plan, tasks, build_context)


def _compact_agent_note(agent_note: str, agent_plan: dict[str, Any] | None) -> str:
    """保存短模型诊断，不把完整模型 JSON 重复写入 DAG。"""

    task_count = len(_raw_agent_tasks(agent_plan) or [])
    fingerprint = sha256(str(agent_note or "").encode("utf-8")).hexdigest()[:16]
    return f"task_model_response sha256={fingerprint} task_count={task_count}"


def _dict_value(value: Any) -> dict[str, Any]:
    """将不可信输入规整为字典，便于后续合并元数据。"""

    return dict(value) if isinstance(value, dict) else {}
