"""把旧 Scope 任务规划算法投影为当前单 Unit Prompt 规则。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.agents.main.backend_unit_task_rules import resolve_backend_unit_task_rules
from app.services.unit_generation_contracts import (
    GenerationRequirement,
    UnitGenerationContext,
)


_TARGET_FIELD_BY_KIND = {
    "frontend.page": "page_id",
    "frontend.api_module": "endpoint_id",
    "frontend.static_data_module": "endpoint_id",
    "frontend.shared_capability": "target_id",
    "backend.bootstrap": "data_source_type",
    "backend.endpoint_controller": "endpoint_id",
}
_BACKEND_STAGE_BY_KIND = {
    "backend.domain_mapping": "objects",
    "backend.repository": "repository",
    "backend.external_api_client": "upstream",
    "backend.external_api_mapping": "mapping",
    "backend.application_service": "service",
    "backend.endpoint_controller": "controller",
}


def _stable_json(value: Any) -> str:
    """把规则内的确定性清单序列化为稳定 JSON。"""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _text(value: Any) -> str:
    """只读取无需修剪的非空文本，避免规则层偷偷规范化正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


def expected_task_owner(context: UnitGenerationContext) -> str:
    """返回当前模型 Unit 唯一允许的 Task owner。"""

    owner = _text(context.constraints.get("owner"))
    if owner:
        return owner
    return "frontend" if context.unit_kind in {"frontend", "page"} else context.unit_kind


def expected_task_type(context: UnitGenerationContext) -> str:
    """返回与当前 owner 对应的 Local Validator 合法 task_type。"""

    owner = expected_task_owner(context)
    if owner == "frontend":
        return "frontend.code"
    if owner == "backend":
        return "backend.code"
    if owner == "database":
        return "database.change"
    raise ValueError(f"模型 Unit {context.unit_id} 没有受支持的 Task owner: {owner}")


def requirement_output_contracts(
    requirements: Sequence[GenerationRequirement],
) -> tuple[dict[str, Any], ...]:
    """把每条增量职责编译为 deliverable 必须精确匹配的输出合同。"""

    contracts: list[dict[str, Any]] = []
    for requirement in requirements:
        refs: Mapping[str, Any] = requirement.source_refs
        kind = _text(refs.get("kind"))
        target_field = _TARGET_FIELD_BY_KIND.get(kind, "entity_id")
        contracts.append({
            "requirement_id": requirement.requirement_id,
            "deliverable_kind": kind,
            "target_field": target_field,
            "target_id": refs.get(target_field),
        })
    return tuple(contracts)


def example_task_id(context: UnitGenerationContext) -> str:
    """为输出形状示例选择当前 Unit 的真实固定 ID，避免模型复制占位身份。"""

    if context.unit_id.startswith("page:"):
        return f"{context.unit_id}::page"
    if context.unit_id == "frontend:data:static":
        return f"{context.unit_id}::data-module"
    if context.unit_id == "backend:bootstrap":
        return "backend:bootstrap::bootstrap"
    requirements = context.generation_requirements
    first = requirements[0] if requirements else None
    refs = first.source_refs if first is not None else {}
    kind = _text(refs.get("kind"))
    if context.unit_id == "frontend:api-client":
        if any(
            _text(requirement.source_refs.get("kind"))
            == "frontend.shared_capability"
            for requirement in requirements
        ):
            return "frontend:api-client::response-entity-adapter"
        contract_id = _text(refs.get("api_contract_id")) or "api-contract-id"
        endpoint_id = _text(refs.get("endpoint_id")) or "endpoint-id"
        return f"{context.unit_id}::{contract_id}::{endpoint_id}::api-module"
    entity_id = _text(refs.get("entity_id")) or "entity-id"
    stage = _BACKEND_STAGE_BY_KIND.get(kind, "stage")
    return f"{context.unit_id}::{entity_id}::{stage}"


def _common_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """生成所有模型 Unit 共用且与严格 Local Validator 对齐的字段规则。"""

    owner = expected_task_owner(context)
    task_type = expected_task_type(context)
    requirement_contracts = requirement_output_contracts(
        context.generation_requirements
    )
    return (
        f"Every Task must use unit_id `{context.unit_id}`, owner `{owner}`, task_type "
        f"`{task_type}`, and status `pending`. Every new Task ID and deliverable ID must "
        f"be globally stable and start with `{context.unit_id}::`; never use a generic "
        "ID that another concurrent Unit could also emit.",
        "The following requirement-to-deliverable manifest is authoritative. Cover every "
        "record exactly once and emit no unrequested capability. For each record, "
        "deliverables[].kind must equal deliverable_kind, target_id must equal target_id, "
        "and provides must contain the exact requirement_id:\n"
        + _stable_json(requirement_contracts),
        "For every Task, target_files and change_scope[].path must be the same non-empty "
        "set of exact workspace-relative paths. allowed_paths must authorize every one of "
        "those paths, and every deliverables[].paths item must belong to that same Task "
        "scope. Use operation=modify only when the exact path exists in WorkspaceContext; "
        "otherwise use operation=add. Do not emit duplicate paths, fields, Task IDs, "
        "deliverable IDs, or capabilities.",
        "Dependencies may reference only Candidate Task IDs or explicitly listed same-Unit "
        "retained Task IDs, but the migrated Unit rules below do not require copying a "
        "retained ID: use only the stated Candidate-local chain. Never reference another "
        "Unit or a cross-Unit Task; the platform compiles cross-Unit edges. Do not recreate "
        "a responsibility absent from the incremental requirement manifest, even when "
        "retained summaries mention it.",
    )


def _page_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """恢复旧版 PageImplementationContract 驱动的页面任务规则。"""

    page_id = context.unit_id.removeprefix("page:")
    return (
        f"Emit exactly one page implementation Task with id `{context.unit_id}::page`, "
        "dependencies `[]`, and exactly one `frontend.page` deliverable. Its target_id "
        f"must be `{page_id}` and its provides must contain the exact page requirement.",
        "Read the authorized `page_contract` root before choosing implementation paths. "
        "Derive PageKey from the directory that contains the contract's uiDesignRef file; "
        "the exact business page entry is `frontend/src/pages/<PageKey>/index.tsx`. That "
        "entry must appear identically in target_files, change_scope, allowed_paths, and "
        "the frontend.page deliverable paths. Reuse the existing entry when present and "
        "add only page-owned components or styles required by the current contract.",
        "Implement only the current PageImplementationContract. Do not emit API-client, "
        "backend, database, route/menu registration, shared scaffold, page placeholder, "
        "test, build, lint, verification, or acceptance responsibilities.",
    )


def _frontend_api_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """恢复旧版共享响应适配器和前端业务 API 模块规则。"""

    kinds = {
        _text(requirement.source_refs.get("kind"))
        for requirement in context.generation_requirements
    }
    rules = [
        "For every `frontend.api_module` requirement, emit exactly one business API Task "
        "and exactly one business API module path. Use Task ID "
        "`frontend:api-client::<api_contract_id>::<endpoint_id>::api-module`; read the "
        "authorized API Contract endpoint, implement its exact method/path and typed "
        "request/response shape, and declare one matching frontend.api_module deliverable.",
        "Across this Candidate, each api_contract_id + endpoint_id has exactly one "
        "implementation owner and one business API module. Business API Tasks must import "
        "`frontend/src/apis/responseEntity.ts`; they must not repeat ResponseEntity types, "
        "success-code handling, protocol errors, business errors, or unwrap logic.",
        "Do not emit pages, static-data modules, backend work, route/menu registration, "
        "tests, builds, verification, or acceptance responsibilities.",
    ]
    if "frontend.shared_capability" in kinds:
        rules.insert(
            0,
            "Emit exactly one shared transport Task with id "
            "`frontend:api-client::response-entity-adapter` and dependencies `[]`. It owns "
            "only `frontend/src/apis/responseEntity.ts` and declares exactly one "
            "frontend.shared_capability deliverable with target_id "
            "`response-entity-adapter` and provides "
            "`frontend.response-entity-adapter`. Its Chinese description must require "
            "ResponseEntity<T>, ResponseEntityBusinessError, ResponseEntityProtocolError, "
            "unwrapResponseEntity<T>(), unwrapEmptyResponseEntity(), and `SUC0000` as the "
            "only success code. Every business API Task in this Candidate depends on it."
        )
    else:
        rules.insert(
            0,
            "The shared ResponseEntity adapter is not an incremental requirement, so it is "
            "already satisfied or outside this attempt. Do not recreate or modify it and "
            "do not copy a retained adapter Task ID into dependencies; new business API "
            "modules still import `frontend/src/apis/responseEntity.ts`."
        )
    return tuple(rules)


def _frontend_static_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """恢复旧版静态数据 Unit 的单模块任务规则。"""

    return (
        f"Emit exactly one data-module Task with id `{context.unit_id}::data-module` and "
        "dependencies `[]`. It owns one frontend business data/API module plus its "
        "module-local types and constants for all current static requirements. Declare one "
        "`frontend.static_data_module` deliverable per requirement, using the exact "
        "endpoint_id and requirement_id from the authoritative manifest.",
        "Use only confirmed static entity values and shapes from authorized contracts. "
        "Never create backend, database, Controller, Mapper, PO, Spring Boot, MyBatis, "
        "datasource, real HTTP endpoint, proxy, mock-plugin, route/menu registration, test, "
        "build, verification, or acceptance work.",
    )


def resolve_unit_task_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """为当前模型 Unit 自动选择完整规则，未知 Unit 不允许以空规则调用模型。"""

    frozen = UnitGenerationContext.model_validate(context)
    if frozen.unit_id.startswith("page:") and frozen.unit_kind == "page":
        specific = _page_rules(frozen)
    elif frozen.unit_id == "frontend:api-client" and frozen.unit_kind == "frontend":
        specific = _frontend_api_rules(frozen)
    elif frozen.unit_id == "frontend:data:static" and frozen.unit_kind == "frontend":
        specific = _frontend_static_rules(frozen)
    elif frozen.unit_kind == "backend":
        specific = resolve_backend_unit_task_rules(frozen)
    else:
        raise ValueError(
            f"模型 Unit {frozen.unit_id} 没有可用的旧任务规划规则投影。"
        )
    rules = (*_common_rules(frozen), *specific)
    if not rules:
        raise ValueError(f"模型 Unit {frozen.unit_id} 的任务规划规则不得为空。")
    return rules
