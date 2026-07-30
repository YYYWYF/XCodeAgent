from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


def apply_unit_compilation(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """补齐任务 Unit 来源，并把 Unit depends_on 边落成任务依赖边。"""

    units = build_task_plan.get("build_units")
    units = units if isinstance(units, dict) else {}
    with_sources = [
        _with_task_unit_metadata(task, units, build_context) for task in tasks
    ]
    return _apply_unit_task_dependencies(
        with_sources, units, build_task_plan.get("unit_graph")
    )


def annotate_unit_inputs(
    build_units_value: Any,
    build_context: dict[str, Any],
    task_registry_value: Any,
) -> dict[str, dict[str, Any]]:
    """为本次范围内的 Unit 记录来源引用、输入指纹和准备状态。"""

    build_units = deepcopy(
        build_units_value if isinstance(build_units_value, dict) else {}
    )
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
            task_id
            for task_id in task_ids
            if isinstance(task_registry.get(task_id), dict)
        ]
    return build_units


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
            _string_list(task.get("requires_capabilities"))
        ),
        "provides_capabilities": _dedupe_strings(
            _string_list(task.get("provides_capabilities")) or [unit_id]
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
        tasks_by_unit.setdefault(
            str(task.get("unit_id") or "application:root"), []
        ).append(str(task.get("id")))
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
        explicit_dependencies = _task_dependencies(task)
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
                    dependency
                    for dependency in dependencies
                    if dependency not in task_ids
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
    return {
        unit_id: _dedupe_strings(dependencies)
        for unit_id, dependencies in result.items()
    }


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
    if unit_id.startswith("database:"):
        database_context = _dict_value(build_context.get("database_planning_context"))
        return {
            **existing,
            "type": "database_context",
            "target": target,
            "database_context_status": database_context.get("status"),
            "database_context_hashes": [
                str(context.get("schema_hash") or "")
                for context in _dict_items(database_context.get("contexts"))
                if context.get("schema_hash")
            ],
            "endpoint_details": _matching_endpoint_refs(
                refs.get("endpoint_details"),
                _string_list(build_context.get("endpoint_ids")),
            ),
            "data_source_ids": _string_list(build_context.get("data_source_ids")),
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
        }
    if unit_id.startswith("backend:endpoint:"):
        return {
            **existing,
            "type": "endpoint_detail",
            "target": target,
            "endpoint_detail": _dict_value(refs.get("endpoint_detail")),
            "endpoint_details": _matching_endpoint_refs(
                refs.get("endpoint_details"),
                _string_list(build_context.get("endpoint_ids")),
            ),
            "api_contract_ids": _string_list(build_context.get("api_contract_ids")),
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
    if unit_id.startswith("database:"):
        return {
            "unit_id": unit_id,
            "source_refs": source_refs,
            "data_source_ids": _string_list(build_context.get("data_source_ids")),
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "database_planning_context": _dict_value(
                build_context.get("database_planning_context")
            ),
        }
    if unit_id.startswith("backend:endpoint:"):
        return {
            "unit_id": unit_id,
            "source_refs": source_refs,
            "api_contract_ids": _string_list(build_context.get("api_contract_ids")),
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
        }
    return {
        "unit_id": unit_id,
        "source_refs": source_refs,
    }


def _matching_endpoint_refs(
    value: Any, endpoint_ids: list[str]
) -> list[dict[str, Any]]:
    """在当前构建上下文中查找指定 endpoint 详情引用。"""

    if not isinstance(value, list):
        return []
    allowed_ids = set(endpoint_ids)
    return [
        dict(item)
        for item in value
        if isinstance(item, dict) and str(item.get("id") or "") in allowed_ids
    ]


def _task_dependencies(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的依赖列表。"""

    return _string_list(task.get("dependencies"))


def _string_list(value: Any) -> list[str]:
    """将列表输入规整为去空字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表收敛为字典列表。"""

    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _dedupe_strings(values: list[str]) -> list[str]:
    """按原始字符串去重并保留顺序。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _text(value: Any, default: str = "") -> str:
    """规整文本输入。"""

    text = str(value or "").strip()
    return text or default


def _dict_value(value: Any) -> dict[str, Any]:
    """将不可信输入规整为字典。"""

    return dict(value) if isinstance(value, dict) else {}


def _stable_fingerprint(value: Any) -> str:
    """为 Unit 局部输入生成稳定哈希，支持后续定向失效。"""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
