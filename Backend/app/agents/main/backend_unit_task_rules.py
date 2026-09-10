"""把 Backend Unit 的 Endpoint responsibilities 投影为固定 Task 规则。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext


_ENDPOINT_STAGE_BY_KIND = {
    "backend.objects": "objects",
    "backend.repository": "repository",
    "backend.upstream": "upstream",
    "backend.application_service": "service",
    "backend.endpoint_controller": "controller",
}
_REQUIREMENT_PREFIX_BY_KIND = {
    "backend.objects": "backend.endpoint.objects",
    "backend.repository": "backend.repository",
    "backend.upstream": "backend.upstream",
    "backend.application_service": "backend.application_service",
    "backend.endpoint_controller": "backend.endpoint_controller",
}
_STAGE_ORDER = {
    "objects": 0,
    "repository": 1,
    "upstream": 1,
    "service": 2,
    "controller": 3,
}
_PHYSICAL_SOURCE_TYPES = frozenset({"database", "external_api"})


def _stable_json(value: Any) -> str:
    """把后端固定职责清单序列化为稳定 JSON。"""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _text(value: Any) -> str:
    """只读取无需修剪的非空正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


def _source_types(refs: Mapping[str, Any]) -> tuple[str, ...] | None:
    """读取 Endpoint responsibility 携带的完整有序物理来源集合。"""

    values = refs.get("data_source_types")
    if not isinstance(values, (list, tuple)):
        return None
    result = tuple(value for value in values if _text(value))
    if (
        len(result) != len(values)
        or len(set(result)) != len(result)
        or tuple(sorted(result)) != result
        or set(result) - _PHYSICAL_SOURCE_TYPES
    ):
        return None
    return result


def _endpoint_task_id(unit_id: str, stage: str) -> str:
    """使用已含 Endpoint 身份的 Unit ID 生成唯一阶段 Task ID。"""

    return f"{unit_id}::{stage}"


def _bootstrap_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """为后端公共依赖与框架启用生成唯一 bootstrap Task 规则。"""

    source_types = sorted({
        _text(requirement.source_refs.get("data_source_type"))
        for requirement in context.generation_requirements
        if _text(requirement.source_refs.get("data_source_type"))
    })
    if (
        not source_types
        or set(source_types) - _PHYSICAL_SOURCE_TYPES
        or any(
            _text(requirement.source_refs.get("kind")) != "backend.bootstrap"
            for requirement in context.generation_requirements
        )
    ):
        raise ValueError(
            "backend:bootstrap 必须只包含 database/external_api 的 "
            "backend.bootstrap 增量职责。"
        )
    capabilities = []
    if "database" in source_types:
        capabilities.append("MyBatis-Plus/MySQL")
    if "external_api" in source_types:
        capabilities.append("Spring Cloud OpenFeign")
    return (
        "Emit exactly one root Task with id `backend:bootstrap::bootstrap` and dependencies "
        "`[]`. It must cover every current backend.bootstrap requirement in one Task and "
        "declare one exact backend.bootstrap deliverable per requirement. Its capability "
        f"scope is {', '.join(capabilities) or 'the listed bootstrap requirements only'}.",
        "Inspect and own the existing `backend/pom.xml`; add or modify only the minimum "
        "dependencies and common configuration required by the listed source types. For "
        "database, own only required datasource/MyBatis bootstrap configuration. For "
        "external_api, also inspect the real Spring Boot Application.java or existing "
        "Feign-enablement configuration and add the smallest authorized "
        "@EnableFeignClients activation only when missing. Include every exact affected "
        "path in target_files, allowed_paths, change_scope, and the relevant deliverable.",
        "This Unit must not generate endpoint Clients, transport DTOs, endpoint objects, "
        "repositories, services, Controllers, DDL, migrations, seed data, tests, builds, "
        "verification, or acceptance work.",
    )


def _backend_stage_manifest(
    context: UnitGenerationContext,
) -> tuple[dict[str, Any], ...]:
    """把 Endpoint responsibilities 编译为不含实体身份的固定阶段清单。"""

    result: list[dict[str, Any]] = []
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        kind = _text(refs.get("kind"))
        stage = _ENDPOINT_STAGE_BY_KIND.get(kind, "")
        api_contract_id = _text(refs.get("api_contract_id"))
        endpoint_id = _text(refs.get("endpoint_id"))
        target_id = _text(refs.get("target_id"))
        source_types = _source_types(refs)
        expected_unit_id = f"backend:endpoint:{api_contract_id}:{endpoint_id}"
        expected_requirement_id = (
            f"{_REQUIREMENT_PREFIX_BY_KIND.get(kind, '')}:{api_contract_id}:{endpoint_id}"
        )
        if (
            not stage
            or not api_contract_id
            or not endpoint_id
            or target_id != endpoint_id
            or source_types is None
            or expected_unit_id != context.unit_id
            or requirement.requirement_id != expected_requirement_id
            or refs.get("artifact") != "endpoint-api-design"
            or "entity_id" in refs
        ):
            raise ValueError(
                f"Backend Endpoint Unit {context.unit_id} 含无法绑定到当前 Endpoint 的职责。"
            )
        result.append({
            "requirement_id": requirement.requirement_id,
            "api_contract_id": api_contract_id,
            "endpoint_id": endpoint_id,
            "data_source_types": list(source_types),
            "stage": stage,
            "task_id": _endpoint_task_id(context.unit_id, stage),
            "deliverable_kind": kind,
        })
    stages = [item["stage"] for item in result]
    if len(stages) != len(set(stages)):
        raise ValueError(f"Backend Endpoint Unit {context.unit_id} 含重复 Endpoint responsibility。")
    retained_task_ids = {
        task_id
        for summary in context.dependency_context.get("retained_task_summaries", ())
        if isinstance(summary, Mapping)
        and (task_id := _text(summary.get("id")))
        and summary.get("unit_id") in {None, context.unit_id}
    }
    available_task_ids = {
        item["task_id"] for item in result
    } | retained_task_ids
    for item in result:
        source_types = set(item["data_source_types"])
        objects_id = _endpoint_task_id(context.unit_id, "objects")
        branch_ids = [
            _endpoint_task_id(context.unit_id, stage)
            for source_type, stage in (
                ("database", "repository"),
                ("external_api", "upstream"),
            )
            if source_type in source_types
            and _endpoint_task_id(context.unit_id, stage) in available_task_ids
        ]
        service_id = _endpoint_task_id(context.unit_id, "service")
        if item["stage"] == "objects":
            dependencies: list[str] = []
        elif item["stage"] in {"repository", "upstream"}:
            dependencies = [objects_id] if objects_id in available_task_ids else []
        elif item["stage"] == "service":
            dependencies = branch_ids or (
                [objects_id] if objects_id in available_task_ids else []
            )
        else:
            dependencies = (
                [service_id]
                if service_id in available_task_ids
                else branch_ids or ([objects_id] if objects_id in available_task_ids else [])
            )
        item["dependencies"] = dependencies
    return tuple(sorted(
        result,
        key=lambda item: (_STAGE_ORDER[item["stage"]], item["requirement_id"]),
    ))


def _backend_common_rules() -> tuple[str, ...]:
    """声明 Endpoint 级路径、命名、描述和权限边界。"""

    return (
        "All backend business source paths must be under `backend/src/main/java/` or "
        "`backend/src/main/resources/`. Resolve naming from the exact API Contract and "
        "Endpoint IDs plus the authorized Endpoint API Design. Reuse an existing package "
        "for that Endpoint/API Contract; otherwise derive basePackage from the existing "
        "Application.java or neighboring business classes. Never derive Task identity, "
        "stage count, datasource, or deliverable target_id from TechnicalPlan entity_ids.",
        "Every backend Task description must be a Simplified Chinese newline-separated "
        "ordered execution list using exact `1. ...\\n2. ...` form. Name each exact path "
        "or Endpoint responsibility. For an existing path, require only the minimum "
        "correction against the frozen contracts; for a missing path, require direct "
        "creation from those contracts.",
        "If the authorized contract slice contains operationResourceKeys for this endpoint, "
        "only the controller Task may add exactly one @RequireAnyResource annotation before "
        "business logic, using platform-generated AuthConstants symbols and preserving "
        "ANY-OF semantics. Never create or modify AuthConstants, authorization services, "
        "request-derived permission checks, or data rules.",
        "Never emit owner=database Tasks, database Units, DDL, schema/table changes, "
        "migrations, seed SQL, tests, builds, lint, verification, runtime checks, or "
        "acceptance responsibilities. Only backend:bootstrap may modify pom.xml or global "
        "datasource/MyBatis/OpenFeign activation.",
    )


def _endpoint_topology_rules(
    context: UnitGenerationContext,
    manifest: tuple[dict[str, Any], ...],
) -> tuple[str, ...]:
    """声明 Endpoint responsibility 的固定 Task 数量、ID 和候选内依赖图。"""

    return (
        "Emit exactly one Task per record in this endpoint-responsibility manifest and no "
        "other backend Task. Use each exact task_id, exact dependencies, and one matching "
        "deliverable per record. "
        "There is no entity loop and no standalone mapping Task or deliverable:\n"
        + _stable_json(manifest),
        "The manifest dependencies preserve the complete Endpoint topology across both this "
        "incremental Candidate and exact same-Unit retained stable stage IDs: objects is the "
        "root; repository/upstream branch from objects; service joins available physical "
        "branches or objects; controller follows service. Never reference another Unit; the "
        "platform compiles cross-Unit edges.",
        f"Every deliverable target_id must be the Endpoint ID represented by `{context.unit_id}`. "
        "Never use an entity ID, source ID, table name, or upstream operation ID as the "
        "Candidate target_id.",
    )


def _objects_rules() -> tuple[str, ...]:
    """定义 Endpoint objects 职责边界。"""

    return (
        "The objects Task owns the Endpoint request/response DTOs, internal value objects, "
        "and typed conversion boundaries shared by all source branches. Derive their shape "
        "from endpointContract and fieldMappings, not from a per-entity pipeline. It must "
        "not perform repository access, upstream calls, multi-source composition, business "
        "rules, or Controller routing.",
    )


def _repository_rules() -> tuple[str, ...]:
    """定义 database 分支的 Endpoint repository 职责边界。"""

    return (
        "When the manifest contains repository, implement only the database access branch "
        "required by fieldMappings: Mapper/repository queries, persistence records, and "
        "typed database results. Do not create a repository for an Endpoint whose physical "
        "source set excludes database, and do not perform endpoint response composition in "
        "this Task.",
    )


def _upstream_rules() -> tuple[str, ...]:
    """定义 external_api 分支的 Endpoint upstream 职责边界。"""

    return (
        "When the manifest contains upstream, implement the external Client, transport DTOs, "
        "and exact upstream request/response contract from sourceSnapshots and the frozen "
        "Endpoint API Design. Reuse one Client method and transport DTO set for the same "
        "upstream operation. Own only endpoint-local client configuration when necessary; "
        "global OpenFeign activation belongs to backend:bootstrap.",
        "The upstream Task must not transform the final Endpoint response, execute "
        "fieldMappings, compose multiple sources, or create a standalone mapping layer. It "
        "must not create Entity/PO, Mapper, Repository, datasource, migration, or seed work.",
    )


def _service_rules() -> tuple[str, ...]:
    """定义统一 service 对字段映射、组合和业务说明的职责边界。"""

    return (
        "The service Task is the sole owner of Endpoint fieldMappings, multi-source "
        "composition, businessDescriptions, request binding, business processing, and final "
        "endpoint response transformation. It may coordinate the repository and upstream "
        "branches present in the manifest, but must not create a separate mapping Task, "
        "expose upstream transport types through the internal API, or implement Controller "
        "routing.",
    )


def _controller_rules() -> tuple[str, ...]:
    """定义 Endpoint controller 的薄适配职责边界。"""

    return (
        "The controller Task implements only the internal endpointContract method/path, "
        "typed request binding, status/envelope contract, authorization annotation when "
        "required, and delegation to the service. It must never expose an upstream path, "
        "perform repository/client access, repeat fieldMappings, or hard-code sample values.",
    )


def resolve_backend_unit_task_rules(
    context: UnitGenerationContext,
) -> tuple[str, ...]:
    """为 Bootstrap 或 Endpoint Backend Unit 选择当前 Endpoint 责任规则。"""

    if context.unit_id == "backend:bootstrap":
        return _bootstrap_rules(context)
    if not context.unit_id.startswith("backend:endpoint:"):
        raise ValueError(f"Backend Unit {context.unit_id} 没有 Endpoint 任务规划规则。")
    manifest = _backend_stage_manifest(context)
    if not manifest:
        raise ValueError(f"Backend Endpoint Unit {context.unit_id} 缺少增量职责。")
    source_type_sets = {tuple(item["data_source_types"]) for item in manifest}
    if len(source_type_sets) != 1:
        raise ValueError(f"Backend Endpoint Unit {context.unit_id} 的物理来源集合不一致。")
    source_types = set(next(iter(source_type_sets)))
    stages = {item["stage"] for item in manifest}
    if "repository" in stages and "database" not in source_types:
        raise ValueError(f"Backend Endpoint Unit {context.unit_id} 的 repository 缺少 database 来源。")
    if "upstream" in stages and "external_api" not in source_types:
        raise ValueError(f"Backend Endpoint Unit {context.unit_id} 的 upstream 缺少 external_api 来源。")
    rules = [
        *_backend_common_rules(),
        *_endpoint_topology_rules(context, manifest),
    ]
    if "objects" in stages:
        rules.extend(_objects_rules())
    if "repository" in stages:
        rules.extend(_repository_rules())
    if "upstream" in stages:
        rules.extend(_upstream_rules())
    if "service" in stages:
        rules.extend(_service_rules())
    if "controller" in stages:
        rules.extend(_controller_rules())
    return tuple(rules)
