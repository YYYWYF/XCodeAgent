"""把 Bootstrap 和后端模块职责投影为任务规划规则。"""

from __future__ import annotations

import json
from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.unit_task_manifest import (
    BACKEND_MODULE_PREFIX, PHYSICAL_SOURCE_TYPES, backend_bootstrap_task_id, backend_stage_manifest,
)
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
        f"Emit exactly one root Task with id `{backend_bootstrap_task_id(context)}` and dependencies "
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
        "Write titles and per-file descriptions in Simplified Chinese. For an existing "
        "path, plan only the minimum correction against the frozen contracts; for a "
        "missing path, plan direct creation from those contracts.",
        "For each endpoint whose authorized contract slice contains operationResourceKeys, "
        "only the controller Task may add exactly one @RequireAnyResource annotation before "
        "business logic, using platform-generated AuthConstants symbols and preserving "
        "ANY-OF semantics. Never create or modify AuthConstants, authorization services, "
        "request-derived permission checks, or data rules.",
        "Never emit owner=database Tasks, database Units, DDL, schema/table changes, "
        "migrations, seed SQL, tests, builds, lint, verification, runtime checks, or "
        "acceptance responsibilities. Only backend:bootstrap may modify pom.xml or global "
        "datasource/MyBatis/OpenFeign activation.",
    )


def _module_topology_rules(
    manifest: tuple[dict[str, Any], ...],
) -> tuple[str, ...]:
    """列出模块阶段与文件必须覆盖的交付物序号，平台负责 ID 和依赖。"""

    prompt_manifest = [
        {
            "key": item["key"],
            "endpoint_ids": item["endpoint_ids"],
            "data_source_types": item["data_source_types"],
            "deliverables": [
                {"index": index, "kind": spec["kind"], "endpoint_id": spec["target_id"]}
                for index, spec in enumerate(item["deliverables"], start=1)
            ],
        }
        for item in manifest
    ]
    return (
        "Emit one file plan for each exact key in this module manifest. Every files[].covers "
        "array lists the 1-based deliverable indices that file implements for that key. "
        "Assign at least one file to every listed deliverable. The platform sets Task IDs, "
        "deliverables, and dependencies. Do not add standalone mapping tasks.\n"
        + _stable_json(prompt_manifest),
        "Use add for a missing file and modify for a file on disk or prepared by an earlier "
        "task in the module. Reuse shared paths across the scoped endpoints where appropriate. "
        "Describe the exact change for each file on one line.",
    )


def _foundation_rules() -> tuple[str, ...]:
    """定义共享基础骨架的文件边界与复用要求。"""

    return (
        "The foundation task prepares the module's shared skeletons from confirmed "
        "sourceSnapshots. For database sources plan the PO, BaseMapper interface, and "
        "Repository with Mapper injection. For external sources plan the FeignClient "
        "interface and exact client-local connection configuration. Reuse compatible "
        "files and preserve existing methods. Do not add operation methods, transport DTOs, "
        "custom Mapper XML, business logic, or routes in foundation.",
    )


def _repository_rules() -> tuple[str, ...]:
    """定义 database 分支的 Endpoint repository 职责边界。"""

    return (
        "For a business task with a backend.repository deliverable, use the prepared PO, "
        "BaseMapper and Repository. Plan the concrete Repository/Mapper methods required by "
        "databaseWrites, databaseQuery, and read-side database fieldMappings. "
        "For single-table insert, primary-key lookup/update/delete, conditional single/list "
        "queries, count, pagination, sorting, fuzzy matching, and IN predicates, use "
        "BaseMapper with LambdaQueryWrapper/LambdaUpdateWrapper. "
        "Plan a custom Mapper method and XML statement only when the confirmed Endpoint "
        "requires a join, aggregation/grouping, UNION, subquery, window function, "
        "database-specific SQL, or a documented performance-sensitive query that BaseMapper "
        "cannot express clearly; name that reason in the task description.",
    )


def _upstream_rules() -> tuple[str, ...]:
    """定义 external_api 分支的 Endpoint upstream 职责边界。"""

    return (
        "For a business task with a backend.upstream deliverable, complete the concrete "
        "Client method on the prepared Client. Use the full upstream request/response "
        "contract from sourceSnapshots and the frozen Endpoint API Design. Reuse one "
        "Client method and transport DTO set for the same upstream operation. Preserve "
        "all declared Path/Query/Header parameters, including optional and unmapped targets, "
        "and complete request/response DTOs. externalApiBindings determines service call-site "
        "values, never the transport signature.",
        "Upstream helpers adapt transport. Keep fieldMappings and source composition in "
        "the Service. Do not add persistence work to an external-only business task.",
    )


def _service_rules() -> tuple[str, ...]:
    """定义统一 service 对字段映射、组合和业务说明的职责边界。"""

    return (
        "Each business task plans one Endpoint's request/response DTOs, necessary Domain "
        "types, typed conversions, and Service. Derive the internal API DTOs from "
        "endpointContract and keep them separate from PO and upstream transport DTOs. "
        "The Service is the sole owner of Endpoint fieldMappings, databaseQuery value rules, "
        "databaseWrites, externalApiBindings, business processing, and final response composition. "
        "Coordinate only repository/upstream branches present for this Endpoint and pass confirmed "
        "externalApiBindings values to external request targets while retaining the complete Client signature. "
        "Unmapped optional targets are absent from the HTTP request; preserve explicit presence, false, and zero. "
        "Follow field-value planning rules for "
        "typed boundaries and execution phases. Do not add a standalone mapping Task, expose upstream "
        "transport types through the internal API, or implement Controller routing.",
        "Whenever implementation of a confirmed business failure branch requires "
        "`throw new BizException(...)`, also plan the concrete module-level "
        "`domain/exception/<Module>ErrorCode.java` enum implementing `IBizErrorCode`. "
        "Decide from the confirmed API Contract error_codes and Endpoint semantics; only "
        "create ErrorCode work for business failure branches the implementation needs. "
        "Use operation=add when the exact file is absent from the workspace snapshot and "
        "operation=modify when it exists. Include the path in the responsible task's "
        "files array with coverage for its business deliverable so the plan never "
        "imports a missing ErrorCode type. Prefer business task ownership and raising the "
        "exception in ApplicationService; this is a planning recommendation, not an "
        "exclusive constraint. Preserve an explicit existing ownership convention.",
    )


def _controller_rules() -> tuple[str, ...]:
    """定义 Endpoint controller 的薄适配职责边界。"""

    return (
        "The shared controller task runs after the scoped business tasks. For each listed "
        "Endpoint, implement the exact internal endpointContract method/path, typed request "
        "binding, status/envelope contract, required authorization annotation, and delegation "
        "to its Service. It must never expose an upstream path, "
        "perform repository/client access, repeat fieldMappings, or hard-code sample values.",
    )


def resolve_backend_unit_task_rules(
    context: UnitGenerationContext,
) -> tuple[str, ...]:
    """按 Bootstrap 或后端模块清单选择本轮阶段及来源的规则。"""

    if context.unit_id == "backend:bootstrap":
        return _bootstrap_rules(context)
    if not context.unit_id.startswith(BACKEND_MODULE_PREFIX):
        raise ValueError(f"Backend Unit {context.unit_id} 没有模块任务规划规则。")
    manifest = backend_stage_manifest(context)
    if not manifest:
        raise ValueError(f"后端模块 Unit {context.unit_id} 缺少增量职责。")
    kinds = {spec["kind"] for item in manifest for spec in item["deliverables"]}
    stages = {item["stage"] for item in manifest}
    rules = [
        *_backend_common_rules(),
        *_module_topology_rules(manifest),
    ]
    if "foundation" in stages:
        rules.extend(_foundation_rules())
    if "backend.repository" in kinds:
        rules.extend(_repository_rules())
    if "backend.upstream" in kinds:
        rules.extend(_upstream_rules())
    if "business" in stages:
        rules.extend(FIELD_VALUE_PLANNING_RULES)
        rules.extend(_service_rules())
    if "controller" in stages:
        rules.extend(_controller_rules())
    return tuple(rules)
