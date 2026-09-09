"""恢复旧 Scope Prompt 中 Bootstrap 与 Backend Endpoint 的单 Unit 规则。"""

from __future__ import annotations

import json
from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext


_DATABASE_STAGE_BY_KIND = {
    "backend.domain_mapping": "objects",
    "backend.repository": "repository",
    "backend.application_service": "service",
    "backend.endpoint_controller": "controller",
}
_EXTERNAL_API_STAGE_BY_KIND = {
    "backend.external_api_client": "upstream",
    "backend.external_api_mapping": "mapping",
    "backend.application_service": "service",
    "backend.endpoint_controller": "controller",
}
_STAGE_ORDER = {
    "objects": 0,
    "upstream": 0,
    "repository": 1,
    "mapping": 1,
    "service": 2,
    "controller": 3,
}


def _stable_json(value: Any) -> str:
    """把后端固定阶段清单序列化为稳定 JSON。"""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _text(value: Any) -> str:
    """只读取无需修剪的非空正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


def _bootstrap_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """恢复旧版后端公共依赖与框架启用的单根任务规则。"""

    source_types = sorted({
        _text(requirement.source_refs.get("data_source_type"))
        for requirement in context.generation_requirements
        if _text(requirement.source_refs.get("data_source_type"))
    })
    if (
        not source_types
        or set(source_types) - {"database", "external_api"}
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
        "This Unit must not generate endpoint Clients, transport DTOs, domain objects, "
        "repositories, services, Controllers, DDL, migrations, seed data, tests, builds, "
        "verification, or acceptance work.",
    )


def _backend_stage_manifest(
    context: UnitGenerationContext,
) -> tuple[dict[str, str], ...]:
    """按实体和旧版阶段身份编译本次 Endpoint Candidate 的固定 Task 清单。"""

    result: list[dict[str, str]] = []
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        source_type = _text(refs.get("data_source_type"))
        entity_id = _text(refs.get("entity_id"))
        kind = _text(refs.get("kind"))
        stage_by_kind = (
            _DATABASE_STAGE_BY_KIND
            if source_type == "database"
            else _EXTERNAL_API_STAGE_BY_KIND
            if source_type == "external_api"
            else {}
        )
        stage = stage_by_kind.get(kind, "")
        result.append({
            "requirement_id": requirement.requirement_id,
            "entity_id": entity_id,
            "data_source_type": source_type,
            "stage": stage,
            "task_id": f"{context.unit_id}::{entity_id}::{stage}",
            "deliverable_kind": kind,
        })
    return tuple(sorted(
        result,
        key=lambda item: (
            item["data_source_type"],
            item["entity_id"],
            _STAGE_ORDER.get(item["stage"], 99),
            item["requirement_id"],
        ),
    ))


def _backend_common_rules() -> tuple[str, ...]:
    """恢复旧版后端路径、命名、描述和权限边界。"""

    return (
        "All backend business source paths must be under `backend/src/main/java/` or "
        "`backend/src/main/resources/`. Resolve naming in this priority: reuse an existing "
        "package/module for the same entity or API Contract; otherwise derive basePackage "
        "from the parent of the existing Application.java or business classes; otherwise "
        "do not create a second package root. Derive names mechanically from exact formal "
        "IDs and never invent semantic modules such as catalog, commerce, or management.",
        "Every backend Task description must be a Simplified Chinese newline-separated "
        "ordered execution list using exact `1. ...\\n2. ...` form. Name each exact path "
        "or responsibility. For an existing path, instruct comparison with the confirmed "
        "contract and only the minimum correction; for a missing path, directly instruct "
        "creation from the confirmed contract.",
        "If the authorized contract slice contains operationResourceKeys for this endpoint, "
        "only the Controller Task may add exactly one @RequireAnyResource annotation before "
        "business logic, using platform-generated AuthConstants symbols and preserving "
        "ANY-OF semantics. Never create or modify AuthConstants, authorization services, "
        "repositories, request-derived permission checks, or data rules.",
        "Never emit owner=database Tasks, database Units, DDL, schema/table changes, "
        "migrations, seed SQL, tests, builds, lint, verification, runtime checks, or "
        "acceptance responsibilities. Only backend:bootstrap may modify pom.xml, global "
        "datasource/MyBatis configuration, or global OpenFeign activation.",
    )


def _database_endpoint_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """恢复旧版数据库实体四阶段分层和固定同 Unit 依赖链。"""

    return (
        "For each database entity, the full logical chain is objects -> repository -> "
        "service -> controller. This incremental attempt emits exactly the stages present "
        "in the fixed manifest below and no retained stage. Use each exact task_id and one "
        "matching deliverable per record:\n" + _stable_json([
            item for item in _backend_stage_manifest(context)
            if item["data_source_type"] == "database"
        ]),
        "The objects stage owns domain/PO/DTO conversion files; repository owns Mapper/XML "
        "and repository files; service owns the application service; controller owns the "
        "internal endpoint adapter. Existing files do not remove a requested stage: use "
        "modify for existing paths and add for missing paths.",
        "Within each entity, a generated stage depends only on the nearest earlier generated "
        "stage in objects -> repository -> service -> controller order. If no earlier stage "
        "is part of this incremental Candidate, use dependencies `[]`; do not copy retained "
        "Task IDs. Different entities do not depend on one another.",
    )


def _external_api_endpoint_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """恢复旧版外部 API 四阶段、配置和字段映射规则。"""

    return (
        "For each external_api entity, the full logical chain is upstream -> mapping -> "
        "service -> controller. This incremental attempt emits exactly the stages present "
        "in the fixed manifest below and no retained stage. Use each exact task_id and one "
        "matching deliverable per record:\n" + _stable_json([
            item for item in _backend_stage_manifest(context)
            if item["data_source_type"] == "external_api"
        ]),
        "Read the authorized API Contract and entity binding. Match the exact "
        "api_contract_id + endpoint_id against operations[].endpoint_refs and require one "
        "operation. Reuse one Client method and transport DTO set for the same operation_id; "
        "prefer a typed @FeignClient for a new Client while preserving an existing complete "
        "HTTP abstraction. Never add persistence stages.",
        "Upstream must name operation_id, base_url_config_key, timeout, upstream method/path, "
        "typed Path/Query parameters, headers, request_shape, response_shape, and success "
        "status codes. It also owns the existing Spring Boot application.yml/yaml/properties "
        "or creates application.yml under the existing module resources when absent. Write "
        "effective_connection.base_url directly as the plain property value; never invent "
        "an environment placeholder or put the URL in Java code.",
        "Mapping must name mapped_entity_path, every source_field -> entity_field rule, and "
        "decimal/datetime/enum types. Service binds internal request fields to upstream fields "
        "by exact name, calls the Client, extracts mapped_entity_path, and translates errors. "
        "Controller implements only the internal API Contract and delegates to the service; "
        "it must never expose the upstream path or hard-code sample scalar values. When all "
        "field mappings share an array prefix such as list[], treat that prefix as the entity "
        "collection mapped_entity_path even when the response root/cardinality is object. If "
        "operation selection or request binding is not deterministic, never invent semantics.",
        "Within each entity, a generated stage depends only on the nearest earlier generated "
        "stage in upstream -> mapping -> service -> controller order. If no earlier stage is "
        "part of this incremental Candidate, use dependencies `[]`; do not copy retained "
        "Task IDs. Different entities do not depend on one another. External API entities "
        "must not create Entity/PO, Mapper, Repository, datasource, migration, or seed work.",
    )


def resolve_backend_unit_task_rules(
    context: UnitGenerationContext,
) -> tuple[str, ...]:
    """为 Bootstrap 或 Endpoint Backend Unit 选择唯一旧规则投影。"""

    if context.unit_id == "backend:bootstrap":
        return _bootstrap_rules(context)
    if not context.unit_id.startswith("backend:endpoint:"):
        raise ValueError(f"Backend Unit {context.unit_id} 没有旧任务规划规则投影。")
    source_types = {
        _text(requirement.source_refs.get("data_source_type"))
        for requirement in context.generation_requirements
    }
    unknown = source_types - {"database", "external_api"}
    if unknown or "" in source_types:
        raise ValueError(
            f"Backend Endpoint Unit {context.unit_id} 含不受支持的数据源: "
            + ", ".join(sorted(unknown or {"<missing>"}))
        )
    manifest = _backend_stage_manifest(context)
    if any(not item["entity_id"] or not item["stage"] for item in manifest):
        raise ValueError(
            f"Backend Endpoint Unit {context.unit_id} 的增量职责无法映射到旧版固定阶段。"
        )
    rules = list(_backend_common_rules())
    if "database" in source_types:
        rules.extend(_database_endpoint_rules(context))
    if "external_api" in source_types:
        rules.extend(_external_api_endpoint_rules(context))
    return tuple(rules)
