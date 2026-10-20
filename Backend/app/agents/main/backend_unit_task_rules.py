"""把 Backend Unit 的 Endpoint responsibilities 投影为固定 Task 规则。"""

from __future__ import annotations

import json
from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.unit_task_manifest import PHYSICAL_SOURCE_TYPES, backend_stage_manifest
from app.agents.field_value_prompt_rules import FIELD_VALUE_PLANNING_RULES


def _stable_json(value: Any) -> str:
    """把后端固定职责清单序列化为稳定 JSON。"""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _text(value: Any) -> str:
    """只读取无需修剪的非空正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


def _bootstrap_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """为后端公共依赖与框架启用生成唯一 bootstrap Task 规则。"""

    source_types = sorted({
        _text(requirement.source_refs.get("data_source_type"))
        for requirement in context.generation_requirements
        if _text(requirement.source_refs.get("data_source_type"))
    })
    if (
        not source_types
        or set(source_types) - PHYSICAL_SOURCE_TYPES
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

    prompt_manifest = tuple(
        {
            ("id" if key == "task_id" else key): value
            for key, value in item.items()
        }
        for item in manifest
    )
    return (
        "Emit exactly one Task per record in this endpoint-responsibility manifest and no "
        "other backend Task. Use each exact id, exact dependencies, and one matching "
        "deliverable per record. "
        "There is no entity loop and no standalone mapping Task or deliverable:\n"
        + _stable_json(prompt_manifest),
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
        "required by databaseWrites and databaseQuery, and by read-side database fieldMappings: Mapper/repository queries, persistence records, and "
        "typed database results. Do not create a repository for an Endpoint whose physical "
        "source set excludes database, and do not perform endpoint response composition in "
        "this Task.",
        "Always keep or create the Mapper interface extending `BaseMapper<PO>` and the "
        "namespace-only Mapper XML placeholder. For single-table insert, primary-key "
        "lookup/update/delete, conditional single/list queries, count, pagination, sorting, "
        "fuzzy matching, and IN predicates, use BaseMapper with "
        "LambdaQueryWrapper/LambdaUpdateWrapper and leave the placeholder XML unchanged. "
        "Plan a custom Mapper method and XML statement only when the confirmed Endpoint "
        "requires a join, aggregation/grouping, UNION, subquery, window function, "
        "database-specific SQL, or a documented performance-sensitive query that BaseMapper "
        "cannot express clearly; name that reason in the task description.",
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
        "The service Task is the sole owner of Endpoint fieldMappings, databaseQuery value rules, "
        "databaseWrites, externalApiBindings, business processing, and final response composition. "
        "Coordinate only repository/upstream branches present in the manifest and pass confirmed "
        "externalApiBindings values to external request targets. Follow field-value planning rules for "
        "typed boundaries and execution phases. Do not add a standalone mapping Task, expose upstream "
        "transport types through the internal API, or implement Controller routing.",
        "Whenever implementation of a confirmed business failure branch requires "
        "`throw new BizException(...)`, also plan the concrete module-level "
        "`domain/exception/<Module>ErrorCode.java` enum implementing `IBizErrorCode`. "
        "Decide from the confirmed API Contract error_codes and Endpoint semantics; only "
        "create ErrorCode work for business failure branches the implementation needs. "
        "Use operation=add when the exact file is absent from the workspace snapshot and "
        "operation=modify when it exists. Include the path in the responsible task's "
        "change_scope, allowed_paths, target_files, and deliverable so the plan never "
        "imports a missing ErrorCode type. Prefer service task ownership and raising the "
        "exception in ApplicationService; this is a planning recommendation, not an "
        "exclusive constraint. Preserve an explicit existing ownership convention.",
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
    manifest = backend_stage_manifest(context)
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
        *FIELD_VALUE_PLANNING_RULES,
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
