from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from app.services.authorization_capability_dependency import (
    AUTH_GUARD_UNIT_ID,
    AuthCapabilityDependencyResolution,
    current_auth_resource_capability,
    resolve_auth_capability_dependency,
)
from app.services.authorization_overlay import unit_authorization_slice
from app.services.planning_issues import ValidationIssue


class BuildUnitCompilationError(ValueError):
    """携带 Unit dependency 编译产生的结构化 Global 问题。"""

    def __init__(self, issues: list[ValidationIssue] | tuple[ValidationIssue, ...]) -> None:
        """冻结完整问题集合，禁止调用方从错误文本反推问题类型。"""

        self.issues = tuple(ValidationIssue.model_validate(issue) for issue in issues)
        super().__init__("；".join(issue.message for issue in self.issues))


def apply_unit_compilation(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
    *,
    preserve_compiled_task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """补齐本轮任务 Unit 来源，并为全量任务重建 Unit 依赖边。"""

    units = build_task_plan.get("build_units")
    units = units if isinstance(units, dict) else {}
    preserved_ids = preserve_compiled_task_ids or set()
    with_sources = [
        deepcopy(task)
        if str(task.get("id") or "") in preserved_ids
        else _with_task_unit_metadata(task, units, build_context)
        for task in tasks
    ]
    return _apply_unit_task_dependencies(
        with_sources, units, build_task_plan.get("unit_graph"), build_context
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
            authorization = unit_authorization_slice(unit_id, build_context)
            if authorization is not None:
                source_refs["authorization"] = authorization
            else:
                source_refs.pop("authorization", None)
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
    canonical_source_refs = _unit_source_refs(unit_id, unit, build_context)
    provided_source_refs = _dict_value(task.get("source_refs"))
    provided_source_refs.pop("entity_ids", None)
    source_refs = {
        **canonical_source_refs,
        **provided_source_refs,
    }
    # 权限 Overlay 是平台拥有的只读事实，模型候选不得提交或覆盖同名来源字段。
    authorization = unit_authorization_slice(unit_id, build_context)
    if authorization is not None:
        source_refs["authorization"] = authorization
    else:
        source_refs.pop("authorization", None)
    # entity_designs 是来源隔离的确定性输入，不能被模型返回的未过滤引用覆盖；
    # endpoint 任务按固定任务 ID 推导实体子集，只暴露本任务真正实现的实体设计。
    if (
        unit_id.startswith("frontend:data:")
        or unit_id.startswith("backend:endpoint:")
        or unit_id == "backend:bootstrap"
    ):
        canonical_designs = _entity_design_items(
            canonical_source_refs.get("entity_designs")
        )
        canonical_entity_ids = [
            str(design.get("entity_id") or "")
            for design in canonical_designs
            if str(design.get("entity_id") or "")
        ]
        selected_entity_ids = _task_entity_ids(task, unit_id, canonical_entity_ids)
        source_refs["entity_designs"] = [
            design
            for design in canonical_designs
            if str(design.get("entity_id") or "") in set(selected_entity_ids)
        ]
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


def _task_entity_ids(
    task: dict[str, Any],
    unit_id: str,
    canonical_entity_ids: list[str],
) -> list[str]:
    """从固定任务 ID 推导后端任务的实体范围。"""

    if not unit_id.startswith("backend:endpoint:") or not canonical_entity_ids:
        return canonical_entity_ids
    task_id = _text(task.get("id"))
    prefix = f"{unit_id}::"
    if task_id.startswith(prefix):
        entity_id, separator, stage = task_id[len(prefix):].rpartition("::")
        if separator and entity_id in canonical_entity_ids and stage:
            return [entity_id]
    if len(canonical_entity_ids) == 1:
        return canonical_entity_ids
    raise ValueError(
        f"Backend endpoint task {task_id or '<unknown>'} must use fixed id "
        f"{unit_id}::<entityId>::<stage> so its entity scope is unambiguous."
    )


def _apply_unit_task_dependencies(
    tasks: list[dict[str, Any]],
    units: dict[str, Any],
    unit_graph: Any,
    build_context: dict[str, Any],
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
    auth_resolution = _auth_dependency_resolution(
        tasks,
        dependency_units,
        build_context,
    )
    if auth_resolution is not None and auth_resolution.issues:
        raise BuildUnitCompilationError(auth_resolution.issues)
    auth_consumer_units = {
        unit_id
        for unit_id, dependencies in dependency_units.items()
        if AUTH_GUARD_UNIT_ID in dependencies and unit_id.startswith("page:")
    }
    result: list[dict[str, Any]] = []
    for task in tasks:
        unit_id = str(task.get("unit_id") or "application:root")
        # 前端页面 Unit 只继承同 frontend 域的依赖（如 frontend:api-client），
        # 不继承 backend:endpoint:* / database:* 的任务依赖，使前端页面可与后端
        # 接口并行生成：前端通过 api-client（已封装 service.get + 契约 schema）
        # 调接口，无需等后端实现。前后端契约一致性由 app:integration 集成测试兜底。
        page_frontend_only = unit_id.startswith("page:")
        # shell 边仅表达模板架构前置；即使登记过历史任务，也不能变为执行依赖。
        inherited_dependencies: list[str] = []
        for dependency_unit_id in dependency_units.get(unit_id, []):
            if dependency_unit_id == "frontend:shell" or not (
                not page_frontend_only
                or dependency_unit_id.startswith("frontend:")
                or (
                    bool(tasks_by_unit.get(dependency_unit_id))
                    and all(
                        str(candidate.get("owner") or "") == "frontend"
                        for candidate in tasks
                        if str(candidate.get("unit_id") or "application:root")
                        == dependency_unit_id
                    )
                )
            ):
                continue
            dependency_task_ids = tasks_by_unit.get(dependency_unit_id, [])
            if (
                auth_resolution is not None
                and unit_id in auth_consumer_units
                and dependency_unit_id == AUTH_GUARD_UNIT_ID
            ):
                dependency_task_ids = list(auth_resolution.provider_task_ids)
            inherited_dependencies.extend(
                dependency_task_id
                for dependency_task_id in dependency_task_ids
                if dependency_task_id and dependency_task_id != task.get("id")
            )
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
                "requires_capabilities": _dedupe_strings([
                    *_string_list(task.get("requires_capabilities")),
                    *(
                        [auth_resolution.capability_id]
                        if auth_resolution is not None and unit_id in auth_consumer_units
                        else []
                    ),
                ]),
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
                    and dependency_unit_id != "frontend:shell"
                    and not (
                        auth_resolution is not None
                        and unit_id in auth_consumer_units
                        and dependency_unit_id == AUTH_GUARD_UNIT_ID
                    )
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


def _auth_dependency_resolution(
    tasks: list[dict[str, Any]],
    dependency_units: dict[str, list[str]],
    build_context: dict[str, Any],
) -> AuthCapabilityDependencyResolution | None:
    """在 Scope Assembly 明示启用时，为受控 Page 解析当前 R 的精确 provider。"""

    if build_context.get("_compile_auth_capability_dependencies") is not True:
        return None
    task_unit_ids = {
        str(task.get("unit_id") or "")
        for task in tasks
        if task.get("id")
    }
    consumer_units = sorted(
        unit_id
        for unit_id, dependencies in dependency_units.items()
        if unit_id in task_unit_ids
        and unit_id.startswith("page:")
        and AUTH_GUARD_UNIT_ID in dependencies
    )
    if not consumer_units:
        return None
    project_plan = build_context.get("project_plan")
    if not isinstance(project_plan, dict):
        return None
    capability_id = current_auth_resource_capability(project_plan)
    if capability_id is None:
        return None
    consumer_task_ids = [
        str(task.get("id"))
        for task in tasks
        if task.get("id") and str(task.get("unit_id") or "") in consumer_units
    ]
    external = build_context.get("external_capabilities")
    return resolve_auth_capability_dependency(
        capability_id=capability_id,
        tasks=tasks,
        external_capabilities=(external if isinstance(external, list) else []),
        consumer_unit_ids=consumer_units,
        consumer_task_ids=consumer_task_ids,
    )


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
    """按 Unit 类型映射到页面实现契约、TechnicalPlan Endpoint 或实体绑定来源。"""

    existing = _dict_value(unit.get("source_refs"))
    target = _dict_value(build_context.get("target"))
    refs = _dict_value(build_context.get("source_refs"))
    entity_designs = _entity_design_items(build_context.get("entity_designs"))
    if unit_id.startswith("page:"):
        return {
            **existing,
            "type": "page_implementation_contract",
            "target": target,
            "page_implementation_contract": _dict_value(
                refs.get("page_implementation_contract")
            ),
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "entity_designs": entity_designs,
        }
    if unit_id.startswith("frontend:data:"):
        return {
            **existing,
            "type": "frontend_mock_contract",
            "target": target,
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "entity_designs": _filter_entity_designs_by_source(entity_designs, {"static"}),
        }
    if unit_id.startswith("backend:endpoint:"):
        contract_id, endpoint_id = _backend_endpoint_identity(unit_id)
        endpoint_ids = [endpoint_id]
        endpoint_refs = _matching_endpoint_refs(
            refs.get("technical_plan_endpoints"),
            endpoint_ids,
        )
        return {
            **existing,
            "type": "technical_plan_endpoint",
            "target": {
                "type": "endpoint",
                "id": endpoint_id,
                "api_contract_id": contract_id,
            },
            "technical_plan_endpoint": (
                endpoint_refs[0]
                if endpoint_refs
                else _dict_value(refs.get("technical_plan_endpoint"))
            ),
            "technical_plan_endpoints": endpoint_refs,
            "endpoint_ids": endpoint_ids,
            "entity_designs": _scope_entity_designs_to_endpoint(
                _filter_entity_designs_by_source(
                    entity_designs,
                    {"database", "external_api"},
                ),
                contract_id=contract_id,
                endpoint_id=endpoint_id,
            ),
        }
    if unit_id == "backend:bootstrap":
        backend_designs = _filter_entity_designs_by_source(
            entity_designs,
            {"database", "external_api"},
        )
        return {
            **existing,
            "type": "backend_bootstrap",
            "target": target,
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "entity_designs": backend_designs,
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
            "entity_ids": _string_list(build_context.get("entity_ids")),
            "entity_designs": _entity_design_items(build_context.get("entity_designs")),
        }
    if unit_id.startswith("frontend:data:"):
        return {
            "unit_id": unit_id,
            "source_refs": source_refs,
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "entity_ids": _string_list(build_context.get("entity_ids")),
            "entity_designs": _filter_entity_designs_by_source(
                _entity_design_items(build_context.get("entity_designs")),
                {"static"},
            ),
        }
    if unit_id.startswith("backend:endpoint:"):
        return {
            "unit_id": unit_id,
            "source_refs": source_refs,
            "endpoint_ids": _string_list(build_context.get("endpoint_ids")),
            "entity_ids": _string_list(build_context.get("entity_ids")),
            "entity_designs": _filter_entity_designs_by_source(
                _entity_design_items(build_context.get("entity_designs")),
                {"database", "external_api"},
            ),
        }
    return {
        "unit_id": unit_id,
        "source_refs": source_refs,
    }


def _filter_entity_designs_by_source(
    entity_designs: list[dict[str, Any]],
    allowed_source_types: set[str],
) -> list[dict[str, Any]]:
    """按 Unit 所属实现边界过滤实体来源，避免前后端互相携带无关设计。"""

    return [
        design
        for design in entity_designs
        if str(design.get("data_source_type") or "").strip() in allowed_source_types
    ]


def _backend_endpoint_identity(unit_id: str) -> tuple[str, str]:
    """从当前 backend:endpoint Unit 标识提取稳定契约与 Endpoint 身份。"""

    parts = str(unit_id or "").split(":", 3)
    if len(parts) != 4 or parts[:2] != ["backend", "endpoint"]:
        return "", ""
    return parts[2].strip(), parts[3].strip()


def _scope_entity_designs_to_endpoint(
    entity_designs: list[dict[str, Any]],
    *,
    contract_id: str,
    endpoint_id: str,
) -> list[dict[str, Any]]:
    """按 Unit Endpoint 裁剪外部 API 操作，数据库实体设计保持原样。"""

    scoped: list[dict[str, Any]] = []
    target_ref = (contract_id, endpoint_id)
    for design in entity_designs:
        copied = deepcopy(design)
        if str(copied.get("data_source_type") or "") != "external_api":
            scoped.append(copied)
            continue
        external = _dict_value(copied.get("external_api_design"))
        operations = []
        for operation in _entity_design_items(external.get("operations")):
            refs = {
                (
                    str(ref.get("api_contract_id") or "").strip(),
                    str(ref.get("endpoint_id") or "").strip(),
                )
                for ref in _entity_design_items(operation.get("endpoint_refs"))
            }
            if target_ref in refs:
                operations.append(deepcopy(operation))
        copied["external_api_design"] = {
            **external,
            "operation_count": len(operations),
            "operations": operations,
        }
        scoped.append(copied)
    return scoped


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


def _entity_design_items(value: Any) -> list[dict[str, Any]]:
    """读取构建上下文中的实体设计摘要，只保留字典项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
