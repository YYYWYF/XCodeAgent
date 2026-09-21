"""构造当前单 Unit 生成器使用的 Prompt 规则。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.agents.main.backend_unit_task_rules import resolve_backend_unit_task_rules
from app.services.business_acceptance import (
    DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND,
    canonical_frontend_api_module_path,
    frontend_api_task_id,
)
from app.services.page_identity import canonical_page_entry_path, page_id_to_page_key
from app.services.unit_generation_contracts import (
    GenerationRequirement,
    UnitGenerationContext,
)


_BACKEND_STAGE_BY_KIND = {
    "backend.objects": "objects",
    "backend.repository": "repository",
    "backend.upstream": "upstream",
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
        target_field = DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND.get(kind)
        if target_field is None:
            raise ValueError(
                f"generation requirement {requirement.requirement_id} 使用了未知 deliverable kind："
                f"{kind or '<missing>'}。"
            )
        target_id = _text(refs.get(target_field))
        if not target_id:
            raise ValueError(
                f"generation requirement {requirement.requirement_id} 缺少 "
                f"{kind} 所需的精确 {target_field}。"
            )
        contracts.append({
            "requirement_id": requirement.requirement_id,
            "deliverable_kind": kind,
            "target_field": target_field,
            "target_id": target_id,
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
        manifest = frontend_api_task_manifest(context)
        if manifest:
            return str(manifest[0]["task_id"])
        contract_id = _text(refs.get("api_contract_id")) or "api-contract-id"
        return frontend_api_task_id(context.unit_id, contract_id, ("endpoint-id",))
    stage = _BACKEND_STAGE_BY_KIND.get(kind)
    if stage is None:
        raise ValueError(
            f"Backend Unit {context.unit_id} 的职责 kind 未定义 Task stage：{kind or '<missing>'}。"
        )
    return f"{context.unit_id}::{stage}"


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
        "otherwise use operation=add. Do not duplicate a path within one path array, field, "
        "Task ID, deliverable ID, or capability. Different logical deliverables may reference "
        "the same physical file when the Unit rule explicitly requires a shared module.",
        "Dependencies may reference only Candidate Task IDs or explicitly listed same-Unit "
        "retained Task IDs. Use the exact dependencies prescribed by the Unit rules below; "
        "never reference another Unit or a cross-Unit Task, because the platform compiles "
        "cross-Unit edges. Do not recreate a responsibility absent from the incremental "
        "requirement manifest, even when retained summaries mention it.",
    )


def _page_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """定义当前 PageImplementationContract 驱动的页面任务规则。"""

    page_requirements = tuple(
        requirement
        for requirement in context.generation_requirements
        if _text(requirement.source_refs.get("kind")) == "frontend.page"
    )
    page_ids = tuple(
        _text(requirement.source_refs.get("page_id"))
        for requirement in page_requirements
    )
    if (
        not page_requirements
        or any(not page_id for page_id in page_ids)
        or len(set(page_ids)) != 1
    ):
        raise ValueError(
            f"Page Unit {context.unit_id} 必须携带唯一且明确的 frontend.page page_id。"
        )
    page_id = page_ids[0]
    unit_page_id = context.unit_id.removeprefix("page:")
    if page_id != unit_page_id:
        raise ValueError(
            f"Page Unit {context.unit_id} 的 page_id 必须与 Unit 身份一致。"
        )
    page_key = page_id_to_page_key(page_id)
    page_entry = canonical_page_entry_path(page_id)
    return (
        f"Emit exactly one page implementation Task with id `{context.unit_id}::page`, "
        "dependencies `[]`, and exactly one `frontend.page` deliverable. Its target_id "
        f"must be `{page_id}` and its provides must contain the exact page requirement.",
        f"The platform deterministically computes PageKey from formal pageId `{page_id}`: "
        f"the canonical PageKey is `{page_key}`, and the exact page entry is `{page_entry}`. "
        "Read the authorized `page_contract` root and its `uiDesignRef` only for UI/design "
        "and implementation reference; neither may determine or rewrite PageKey or the "
        "page entry. Treat these platform-provided values as immutable: the model must not "
        "independently derive or rewrite paths from pageId spelling, unit_id, route path, "
        "uiDesignRef content, or workspace guesses. The exact page entry must appear "
        "identically in target_files, change_scope, allowed_paths, and "
        "the frontend.page deliverable paths. Reuse the existing entry when present and "
        "add only page-owned components or styles required by the current contract.",
        "Implement only the current PageImplementationContract. Do not emit API-client, "
        "backend, database, route/menu registration, shared scaffold, page placeholder, "
        "test, build, lint, verification, or acceptance responsibilities.",
    )


def _frontend_api_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """定义复用模板 service 的前端业务 API 模块规则。"""

    manifest = frontend_api_task_manifest(context)
    return (
        "Group all `frontend.api_module` requirements by `api_contract_id`. For each "
        "distinct api_contract_id, emit exactly ONE business API Task. The deterministic "
        "Contract manifest below supplies the exact Task ID, Endpoint set, and canonical "
        "module path. The base namespace is "
        "`frontend:api-client::<api_contract_id>::api-module`; append the manifest's stable "
        "suffix, and do not shorten, rename, or reuse an ID from a retained Task:\n"
        + _stable_json(manifest)
        + "\nThat single Task implements "
        "every endpoint belonging to that API Contract and writes all of them into exactly "
        "ONE shared file `frontend/src/apis/<biz>Api.ts`, where `<biz>` is the "
        "api_contract_id converted to lowerCamelCase with the `_api` suffix stripped "
        "(e.g. `product_api` → `productApi.ts`, `category_api` → `categoryApi.ts`). "
        "Inside that file, implement each endpoint's exact method/path and typed "
        "request/response shape as a separate exported async function. Declare one "
        "`frontend.api_module` deliverable per requirement inside that Task, each with its "
        "own target_id (the endpoint_id) and the exact requirement_id in provides. "
        "Multiple endpoints of the same API Contract share the same Task, the same file, "
        "and the same change_scope path; do not split them into separate tasks or files.",
        "Across this Candidate, each api_contract_id + endpoint_id has exactly one "
        "implementation owner. Every business API Task has dependencies `[]`, owns only "
        "`frontend/src/apis/<biz>Api.ts`, and reuses the platform-owned "
        "`frontend/src/apis/service.ts` for HTTP requests.",
        "Do not emit pages, static-data modules, backend work, route/menu registration, "
        "tests, builds, verification, or acceptance responsibilities.",
    )


def frontend_api_task_manifest(
    context: UnitGenerationContext,
) -> tuple[dict[str, Any], ...]:
    """把当前 frontend API requirements 编译为按 Contract 分组的稳定任务清单。"""

    grouped: dict[str, list[tuple[str, str]]] = {}
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        if _text(refs.get("kind")) != "frontend.api_module":
            raise ValueError(
                f"Frontend API Unit {context.unit_id} 含非 frontend.api_module 职责。"
            )
        contract_id = _text(refs.get("api_contract_id"))
        endpoint_id = _text(refs.get("endpoint_id"))
        if not contract_id or not endpoint_id:
            raise ValueError(
                f"Frontend API Unit {context.unit_id} 的职责缺少 Contract 或 Endpoint 身份。"
            )
        grouped.setdefault(contract_id, []).append((endpoint_id, requirement.requirement_id))

    manifest: list[dict[str, Any]] = []
    for contract_id in sorted(grouped):
        records = grouped[contract_id]
        endpoint_ids = tuple(sorted(endpoint_id for endpoint_id, _ in records))
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError(
                f"Frontend API Unit {context.unit_id} 含重复职责 {contract_id} 的 Endpoint。"
            )
        manifest.append({
            "api_contract_id": contract_id,
            "endpoint_ids": list(endpoint_ids),
            "requirement_ids": [
                requirement_id
                for _, requirement_id in sorted(records, key=lambda item: item[0])
            ],
            "task_id": frontend_api_task_id(context.unit_id, contract_id, endpoint_ids),
            "module_path": canonical_frontend_api_module_path(contract_id),
        })
    return tuple(manifest)


def _frontend_static_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """定义当前静态数据 Unit 的单模块任务规则。"""

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
            f"模型 Unit {frozen.unit_id} 没有可用的任务规划规则投影。"
        )
    rules = (*_common_rules(frozen), *specific)
    if not rules:
        raise ValueError(f"模型 Unit {frozen.unit_id} 的任务规划规则不得为空。")
    return rules
