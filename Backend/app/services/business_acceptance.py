"""DAG 任务业务验收标准的确定性编译器。

本模块只负责把已确认正式产物编译成可追溯的业务检查，不读取工作区源码，
也不调用模型。源码读取和断言执行由 ``business_acceptance_verifier`` 负责。
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any


BUSINESS_ACCEPTANCE_KINDS = (
    "frontend.api_contract",
    "frontend.page_endpoint_usage",
    "frontend.static_data_contract",
    "backend.domain_mapping",
    "backend.repository_contract",
    "backend.application_service_contract",
    "backend.endpoint_contract",
    "backend.external_api_client_contract",
    "backend.external_api_mapping_contract",
)

BUSINESS_VERIFIER_NAMES = {
    "frontend.api_contract": "frontend_api_contract",
    "frontend.page_endpoint_usage": "frontend_page_endpoint_usage",
    "frontend.static_data_contract": "frontend_static_data_contract",
    "backend.domain_mapping": "backend_domain_mapping",
    "backend.repository_contract": "backend_repository_contract",
    "backend.application_service_contract": "backend_application_service_contract",
    "backend.endpoint_contract": "backend_endpoint_contract",
    "backend.external_api_client_contract": "backend_external_api_client_contract",
    "backend.external_api_mapping_contract": "backend_external_api_mapping_contract",
}

DELIVERABLE_KINDS = (
    "frontend.page",
    "frontend.api_module",
    "frontend.static_data_module",
    "frontend.shared_capability",
    "backend.domain_mapping",
    "backend.repository",
    "backend.application_service",
    "backend.endpoint_controller",
    "backend.external_api_client",
    "backend.external_api_mapping",
    "backend.bootstrap",
)

_FRONTEND_DELIVERABLE_KINDS = {
    "frontend.page",
    "frontend.api_module",
    "frontend.static_data_module",
    "frontend.shared_capability",
}
_BACKEND_DELIVERABLE_KINDS = set(DELIVERABLE_KINDS) - _FRONTEND_DELIVERABLE_KINDS
_CHECK_ORDER = {kind: index for index, kind in enumerate(BUSINESS_ACCEPTANCE_KINDS)}
_MAX_ITEMS = 100
_MAX_SCHEMA_DEPTH = 6


def compile_business_acceptance(
    tasks: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """在任务完成归一化和 Unit 编译后生成业务验收检查。"""

    compile_context = context if isinstance(context, dict) else {}
    return [_compile_task(task, compile_context) for task in tasks]


def compile_repair_business_acceptance(
    task: dict[str, Any],
    parent_task: dict[str, Any],
) -> dict[str, Any]:
    """让 Repair Task 原样继承失败父任务的业务检查，禁止降低预期。"""

    compiled = deepcopy(task)
    parent_checks = _dict_items(parent_task.get("business_acceptance_checks"))
    compiled["business_acceptance_checks"] = deepcopy(parent_checks)
    compiled["business_acceptance_inherited_from"] = str(
        parent_task.get("id") or ""
    )
    return compiled


def normalize_deliverables(value: Any) -> list[dict[str, Any]]:
    """把模型交付物投射到当前允许字段，忽略所有平台拥有的检查字段。"""

    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        deliverable_id = _text(item.get("id"))
        kind = _text(item.get("kind"))
        paths = _dedupe_paths(item.get("paths"))
        if not deliverable_id or not kind:
            continue
        result.append(
            {
                "id": deliverable_id,
                "kind": kind,
                "target_id": _text(item.get("target_id")),
                "paths": paths,
                "provides": _dedupe_strings(item.get("provides")),
            }
        )
    return result


def business_acceptance_contract_errors(
    task: dict[str, Any],
    *,
    allow_missing_deliverable: bool = False,
) -> list[str]:
    """校验交付物、业务检查、路径和来源是否满足当前 DAG 契约。"""

    task_id = _text(task.get("id"), "<unknown>")
    owner = _text(task.get("owner"))
    unit_id = _text(task.get("unit_id"), "application:root")
    deliverables = normalize_deliverables(task.get("deliverables"))
    errors: list[str] = []
    if (
        not allow_missing_deliverable
        and _requires_business_deliverable(task)
        and not deliverables
    ):
        errors.append(f"Task {task_id} must declare at least one deliverable.")

    deliverable_ids: set[str] = set()
    owned_paths: set[str] = set()
    allowed_paths = _task_allowed_paths(task)
    for deliverable in deliverables:
        deliverable_id = deliverable["id"]
        kind = deliverable["kind"]
        if deliverable_id in deliverable_ids:
            errors.append(f"Task {task_id} contains duplicate deliverable id {deliverable_id}.")
        deliverable_ids.add(deliverable_id)
        if kind not in DELIVERABLE_KINDS:
            errors.append(f"Task {task_id} has unsupported deliverable kind {kind}.")
        if kind in _FRONTEND_DELIVERABLE_KINDS and (
            owner != "frontend" or not unit_id.startswith(("page:", "frontend:"))
        ):
            errors.append(
                f"Task {task_id} frontend deliverable {deliverable_id} has invalid owner or Unit."
            )
        if kind in _BACKEND_DELIVERABLE_KINDS and (
            owner != "backend" or not unit_id.startswith("backend:")
        ):
            errors.append(
                f"Task {task_id} backend deliverable {deliverable_id} has invalid owner or Unit."
            )
        paths = deliverable["paths"]
        if not paths and kind != "frontend.shared_capability":
            errors.append(f"Deliverable {deliverable_id} must declare paths.")
        for path in paths:
            normalized = normalize_repo_path(path)
            if not normalized or _is_absolute_repo_path(path) or ".." in normalized.split("/"):
                errors.append(f"Deliverable {deliverable_id} contains unsafe path {path}.")
                continue
            if not _path_matches_any(normalized, allowed_paths):
                errors.append(
                    f"Deliverable {deliverable_id} path {path} is outside the task scope."
                )
            normalized_key = normalized.casefold()
            if normalized_key in owned_paths:
                errors.append(f"Task {task_id} assigns path {path} to multiple deliverables.")
            owned_paths.add(normalized_key)
        if kind == "frontend.page":
            errors.extend(_page_deliverable_errors(task, deliverable))
        if kind == "backend.endpoint_controller":
            errors.extend(_endpoint_deliverable_errors(task, deliverable))

    source_refs = _dict_value(task.get("source_refs"))
    entity_ids = set(_string_list(source_refs.get("entity_ids")))
    endpoint_ids = set(_string_list(source_refs.get("endpoint_ids")))
    entity_designs = {
        _text(item.get("entity_id"))
        for item in _dict_items(source_refs.get("entity_designs"))
        if _text(item.get("entity_id"))
    }
    if entity_ids and entity_designs and not entity_ids.issubset(entity_designs):
        errors.append(
            f"Task {task_id} references entities outside its Unit: "
            + ", ".join(sorted(entity_ids - entity_designs))
            + "."
        )

    checks = _dict_items(task.get("business_acceptance_checks"))
    check_ids: set[str] = set()
    for check in checks:
        check_id = _text(check.get("id"))
        deliverable_id = _text(check.get("deliverable_id"))
        kind = _text(check.get("kind"))
        if not check_id:
            errors.append(f"Task {task_id} contains a business check without id.")
        if check_id in check_ids:
            errors.append(f"Task {task_id} contains duplicate business check id {check_id}.")
        check_ids.add(check_id)
        if deliverable_id not in deliverable_ids:
            errors.append(
                f"Business check {check_id or '<unknown>'} references unknown deliverable {deliverable_id or '<empty>'}."
            )
        if kind not in BUSINESS_ACCEPTANCE_KINDS:
            errors.append(f"Business check {check_id or '<unknown>'} has unsupported kind {kind}.")
        if kind not in BUSINESS_VERIFIER_NAMES:
            errors.append(f"Business check {check_id or '<unknown>'} has no registered verifier.")
        sources = _dict_items(check.get("sources"))
        if not sources:
            errors.append(f"Business check {check_id or '<unknown>'} has no formal sources.")
        for source in sources:
            if not _text(source.get("artifact")):
                errors.append(f"Business check {check_id or '<unknown>'} has an empty source artifact.")
            if not _text(source.get("target_id")):
                errors.append(f"Business check {check_id or '<unknown>'} has an empty source target.")
            if not _text(source.get("pointer")):
                errors.append(f"Business check {check_id or '<unknown>'} has an empty source pointer.")
            if not _text(source.get("sha256")):
                errors.append(f"Business check {check_id or '<unknown>'} has an empty source hash.")
            source_target = _text(source.get("target_id"))
            artifact = _text(source.get("artifact"))
            if artifact in {"api_contract", "endpoint_detail"} and endpoint_ids and source_target not in endpoint_ids:
                errors.append(
                    f"Business check {check_id or '<unknown>'} references endpoint {source_target} outside the Unit."
                )
            if artifact == "entity_design" and entity_ids and source_target not in entity_ids:
                errors.append(
                    f"Business check {check_id or '<unknown>'} references entity {source_target} outside the Unit."
                )
            if artifact == "page_implementation_contract":
                page_id = unit_id.split(":", 1)[1] if unit_id.startswith("page:") else ""
                if page_id and source_target != page_id:
                    errors.append(
                        f"Business check {check_id or '<unknown>'} references page {source_target} outside the Unit."
                    )
        target_paths = _dedupe_paths(check.get("target_paths"))
        if not target_paths:
            errors.append(f"Business check {check_id or '<unknown>'} has no target paths.")
        for path in target_paths:
            if not _path_matches_any(path, allowed_paths):
                errors.append(
                    f"Business check {check_id or '<unknown>'} target path {path} is outside the task scope."
                )
        verification = _dict_value(check.get("verification"))
        if verification.get("mode") != "deterministic":
            errors.append(f"Business check {check_id or '<unknown>'} must use deterministic verification.")
        if verification.get("verifier") != BUSINESS_VERIFIER_NAMES.get(kind):
            errors.append(f"Business check {check_id or '<unknown>'} verifier does not match its kind.")
        if check.get("required") is not True:
            errors.append(f"Business check {check_id or '<unknown>'} must be required.")
        if check.get("verification_stage") != "build":
            errors.append(f"Business check {check_id or '<unknown>'} must run at build stage.")
        errors.extend(_expected_field_errors(check_id, kind, check.get("expected")))
    return _dedupe_strings(errors)


def normalize_repo_path(value: Any) -> str:
    """将 Windows、macOS 和 Linux 输入归一为相对仓库路径。"""

    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return "/".join(part for part in text.split("/") if part not in {"", "."})


def _compile_task(task: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """根据单个任务的交付物和正式产物生成稳定的业务检查列表。"""

    compiled = deepcopy(task)
    deliverables = normalize_deliverables(task.get("deliverables"))
    compiled["deliverables"] = deliverables
    if not deliverables:
        compiled["business_acceptance_checks"] = []
        return compiled
    if task.get("kind") == "repair" and _dict_items(task.get("business_acceptance_checks")):
        return compiled
    formal = _formal_inputs(context, task)
    checks: list[dict[str, Any]] = []
    # 同一种业务契约可能由 Entity、PO、DTO、Converter 等多个兄弟交付物共同实现。
    # 验收必须读取该类交付物的完整路径集合，不能要求每个单文件独立承担整条契约。
    for deliverable in _aggregate_deliverables_by_kind(deliverables):
        checks.extend(_checks_for_deliverable(task, deliverable, formal))
    checks.sort(key=lambda item: (_CHECK_ORDER.get(str(item.get("kind")), 999), str(item.get("id"))))
    compiled["business_acceptance_checks"] = checks
    return compiled


def _aggregate_deliverables_by_kind(
    deliverables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按交付物 kind 聚合兄弟路径，并保留首个交付物作为检查锚点。"""

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for deliverable in deliverables:
        kind = _text(deliverable.get("kind"))
        if kind not in grouped:
            grouped[kind] = deepcopy(deliverable)
            grouped[kind]["paths"] = []
            grouped[kind]["provides"] = []
            order.append(kind)
        grouped[kind]["paths"] = _dedupe_paths(
            [*grouped[kind]["paths"], *deliverable.get("paths", [])]
        )
        grouped[kind]["provides"] = _dedupe_strings(
            [*grouped[kind]["provides"], *deliverable.get("provides", [])]
        )
    return [grouped[kind] for kind in order]


def _checks_for_deliverable(
    task: dict[str, Any],
    deliverable: dict[str, Any],
    formal: dict[str, Any],
) -> list[dict[str, Any]]:
    """把单个交付物映射到已实现的确定性检查类型。"""

    kind = deliverable["kind"]
    if kind == "frontend.api_module":
        endpoints = _endpoint_expectations(formal)
        return [
            _business_check(
                task,
                deliverable,
                "frontend.api_contract",
                "前端业务 API 模块必须实现已确认的 method、path、请求参数和响应结构。",
                _api_sources(formal, endpoints),
                {"endpoints": endpoints},
            )
        ] if endpoints else []
    if kind == "frontend.page":
        required = _required_endpoint_ids(formal)
        if not required:
            return []
        endpoints = [item for item in _endpoint_expectations(formal) if item.get("endpoint_id") in required]
        sources = _page_sources(formal, endpoints)
        return [
            _business_check(
                task,
                deliverable,
                "frontend.page_endpoint_usage",
                "页面或其任务内可达组件必须实际调用 PageImplementationContract 声明的业务接口。",
                sources,
                {
                    "page_id": _text(formal.get("page_contract", {}).get("pageId")),
                    "required_endpoint_ids": required,
                    "endpoints": endpoints,
                },
            )
        ]
    if kind == "frontend.static_data_module":
        entity = _primary_entity(formal)
        if not entity:
            return []
        endpoints = _endpoint_expectations(formal)
        operations = _operation_expectations(formal)
        return [
            _business_check(
                task,
                deliverable,
                "frontend.static_data_contract",
                "静态数据业务模块必须实现已确认字段、种子数据、返回 envelope 和声明的操作结构。",
                _entity_sources(formal, entity) + _api_sources(formal, endpoints),
                {
                    "entity": _entity_expectation(entity),
                    "endpoints": endpoints,
                    "operations": operations,
                    "static_design": _dict_value(entity.get("static_design")),
                    "forbidden_imports": ["axios", "fetch", "src/apis/service", "@/apis/service"],
                },
            )
        ]
    if kind == "backend.domain_mapping":
        entities = _selected_entities(formal)
        if not entities:
            return []
        endpoints = _endpoint_expectations(formal)
        return [
            _business_check(
                task,
                deliverable,
                "backend.domain_mapping",
                "后端 Entity、PO、DTO 与已确认实体字段和数据库列映射必须完整，并通过转换层衔接。",
                [source for entity in entities for source in _entity_sources(formal, entity)]
                + _api_sources(formal, endpoints),
                {
                    "entities": [_entity_expectation(entity) for entity in entities],
                    "endpoints": endpoints,
                },
            )
        ]
    if kind == "backend.repository":
        operations = _operation_expectations(formal)
        entities = _selected_entities(formal)
        if not operations and not entities:
            return []
        return [
            _business_check(
                task,
                deliverable,
                "backend.repository_contract",
                "Repository/Mapper 必须按已确认实体绑定和 EndpointDetail 操作语义实现查询、分页及返回 cardinality。",
                [source for entity in entities for source in _entity_sources(formal, entity)]
                + _endpoint_detail_sources(formal),
                {"entities": [_entity_expectation(entity) for entity in entities], "operations": operations},
            )
        ]
    if kind == "backend.application_service":
        operations = _operation_expectations(formal)
        if not operations:
            return []
        endpoints = _endpoint_expectations(formal)
        return [
            _business_check(
                task,
                deliverable,
                "backend.application_service_contract",
                "ApplicationService 必须委托 Repository、执行已确认 operation 语义并保留事务边界。",
                _endpoint_detail_sources(formal) + _api_sources(formal, endpoints),
                {"operations": operations, "endpoints": endpoints},
            )
        ]
    if kind == "backend.endpoint_controller":
        endpoints = _endpoint_expectations(formal)
        if not endpoints:
            return []
        return [
            _business_check(
                task,
                deliverable,
                "backend.endpoint_contract",
                "Controller 必须按 API Contract 暴露 method/path、绑定 DTO、返回约定状态码并委托 ApplicationService。",
                _api_sources(formal, endpoints) + _endpoint_detail_sources(formal),
                {"endpoints": endpoints, "operations": _operation_expectations(formal)},
            )
        ]
    if kind == "backend.external_api_client":
        designs = _external_designs(formal)
        if not designs:
            return []
        return [
            _business_check(
                task,
                deliverable,
                "backend.external_api_client_contract",
                "外部 API Client 必须使用已确认上游 method/path、请求/响应 DTO 和公共 HTTP Client。",
                [source for entity in designs for source in _entity_sources(formal, entity)],
                {"external_apis": [_external_api_expectation(entity) for entity in designs]},
            )
        ]
    if kind == "backend.external_api_mapping":
        designs = _external_designs(formal)
        if not designs:
            return []
        endpoints = _endpoint_expectations(formal)
        return [
            _business_check(
                task,
                deliverable,
                "backend.external_api_mapping_contract",
                "外部 API 返回字段必须按已确认 source_field 到 entity_field 的嵌套路径逐项映射。",
                [source for entity in designs for source in _entity_sources(formal, entity)]
                + _api_sources(formal, endpoints),
                {
                    "external_apis": [_external_api_expectation(entity) for entity in designs],
                    "endpoints": endpoints,
                },
            )
        ]
    return []


def _business_check(
    task: dict[str, Any],
    deliverable: dict[str, Any],
    kind: str,
    description: str,
    sources: list[dict[str, Any]],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """创建带稳定 ID、来源哈希和有界目标路径的业务检查。"""

    target_paths = _dedupe_paths(deliverable.get("paths"))
    payload = {
        "kind": kind,
        "deliverable_id": deliverable.get("id"),
        "sources": sources,
        "expected": expected,
        "target_paths": target_paths,
    }
    digest = _stable_hash(payload)[:16]
    return {
        "id": f"business:{task.get('id') or 'task'}:{kind}:{digest}",
        "deliverable_id": deliverable.get("id"),
        "kind": kind,
        "description": description,
        "sources": sources,
        "expected": expected,
        "target_paths": target_paths,
        "verification": {
            "mode": "deterministic",
            "verifier": BUSINESS_VERIFIER_NAMES[kind],
        },
        "required": True,
        "verification_stage": "build",
    }


def _formal_inputs(context: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """从当前构建上下文和 ProjectPlan 读取完整正式输入而非摘要计数。"""

    project_plan = _dict_value(context.get("project_plan"))
    executable = _dict_value(context.get("executable_details"))
    source_refs = _dict_value(task.get("source_refs"))
    endpoint_ids = _string_list(source_refs.get("endpoint_ids"))
    if not endpoint_ids:
        endpoint_ids = _string_list(context.get("endpoint_ids"))
    contracts = _dict_items(project_plan.get("api_contracts")) or _dict_items(executable.get("api_contracts"))
    page_contract = _dict_value(context.get("page_implementation_contract"))
    if not page_contract:
        page_contracts = _dict_items(project_plan.get("page_implementation_contracts"))
        if not page_contracts:
            page_contracts = _dict_items(executable.get("page_implementation_contracts"))
        target_id = _text(_dict_value(context.get("target")).get("id"))
        page_contract = next(
            (item for item in page_contracts if _text(item.get("pageId")) == target_id),
            {},
        )
    endpoint_details = _dict_items(context.get("direct_endpoint_details"))
    endpoint_details.extend(_dict_items(context.get("endpoint_details")))
    endpoint_details.extend(_dict_items(project_plan.get("endpoint_detail_plans")))
    if not endpoint_details:
        endpoint_details.extend(_dict_items(executable.get("endpoint_detail_plans")))
    endpoint_details = _unique_objects(endpoint_details, ("api_contract_id", "endpoint_id"))
    task_entity_ids = set(_string_list(source_refs.get("entity_ids")))
    context_entity_ids = set(_string_list(context.get("entity_ids")))
    # Task 已经过 Unit 编译并携带精确实体子集；仅在任务没有实体声明时，
    # 才回退到页面或 endpoint 的 BuildContext，避免单实体任务继承同页其他实体。
    requested_entity_ids = set(task_entity_ids or context_entity_ids)
    entity_details = [
        item
        for item in _dict_items(project_plan.get("entity_detail_plans"))
        if _text(item.get("status")) == "confirmed"
        and (not requested_entity_ids or _text(item.get("entity_id")) in requested_entity_ids)
    ]
    if not entity_details:
        entity_details = [
            item for item in _dict_items(context.get("formal_entity_designs"))
            if not requested_entity_ids or _text(item.get("entity_id")) in requested_entity_ids
        ]
    return {
        "project_plan": project_plan,
        "contracts": contracts,
        "page_contract": page_contract,
        "endpoint_details": endpoint_details,
        "entity_details": entity_details,
        "endpoint_ids": endpoint_ids,
        "source_refs": source_refs,
    }


def _endpoint_expectations(formal: dict[str, Any]) -> list[dict[str, Any]]:
    """提取当前任务负责的 endpoint、参数和请求响应结构。"""

    endpoint_ids = set(_string_list(formal.get("endpoint_ids")))
    result: list[dict[str, Any]] = []
    for contract in _dict_items(formal.get("contracts")):
        contract_id = _text(contract.get("id"))
        schemas = _dict_value(contract.get("schemas"))
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = _text(endpoint.get("id"))
            if endpoint_ids and endpoint_id not in endpoint_ids:
                continue
            if not endpoint_id:
                continue
            result.append(
                {
                    "api_contract_id": contract_id,
                    "endpoint_id": endpoint_id,
                    "method": _text(endpoint.get("method"), "GET").upper(),
                    "path": _text(endpoint.get("path")),
                    "request_schema_ref": _text(endpoint.get("request_schema_ref")),
                    "response_schema_ref": _text(endpoint.get("response_schema_ref")),
                    "parameters": _project_parameters(endpoint.get("parameters")),
                    "request_schema": _schema_descriptor(schemas, endpoint.get("request_schema_ref")),
                    "response_schema": _schema_descriptor(schemas, endpoint.get("response_schema_ref")),
                }
            )
    return result[:_MAX_ITEMS]


def _required_endpoint_ids(formal: dict[str, Any]) -> list[str]:
    """读取 PageImplementationContract 的 requiredEndpointIds。"""

    contract = _dict_value(formal.get("page_contract"))
    return _dedupe_strings(
        contract.get("requiredEndpointIds") or contract.get("required_endpoint_ids")
    )


def _operation_expectations(formal: dict[str, Any]) -> list[dict[str, Any]]:
    """从 EndpointDetail 提取可转换为确定性断言的操作语义。"""

    result: list[dict[str, Any]] = []
    for detail in _dict_items(formal.get("endpoint_details")):
        decision = _dict_value(detail.get("endpoint_decision"))
        semantics = _dict_value(decision.get("operation_semantics"))
        interface = _dict_value(detail.get("interface_design"))
        response = _dict_value(interface.get("response_format"))
        if not semantics and not response:
            continue
        selector = _dict_value(semantics.get("selector"))
        result.append(
            {
                "api_contract_id": _text(detail.get("api_contract_id")),
                "endpoint_id": _text(detail.get("endpoint_id")),
                "operation_kind": _text(semantics.get("operation_kind")),
                "target_cardinality": _text(semantics.get("target_cardinality")),
                "selector": {
                    "source": _text(selector.get("source")),
                    "fields": _dedupe_strings(selector.get("fields")),
                },
                "transaction_required": bool(semantics.get("transaction_required")),
                "zero_match_behavior": _text(semantics.get("zero_match_behavior")),
                "multiple_match_behavior": _text(semantics.get("multiple_match_behavior")),
                "success_status_code": semantics.get("success_status_code") or response.get("status_code"),
                "side_effect": _text(semantics.get("side_effect"), "none"),
            }
        )
    return result[:_MAX_ITEMS]


def _selected_entities(formal: dict[str, Any]) -> list[dict[str, Any]]:
    """按任务实体范围返回完整已确认实体设计。"""

    return _dict_items(formal.get("entity_details"))[:_MAX_ITEMS]


def _primary_entity(formal: dict[str, Any]) -> dict[str, Any]:
    """返回静态数据交付物对应的第一个实体设计。"""

    entities = _selected_entities(formal)
    return entities[0] if entities else {}


def _external_designs(formal: dict[str, Any]) -> list[dict[str, Any]]:
    """筛选包含完整 external_api_design 的已确认实体设计。"""

    return [
        entity
        for entity in _selected_entities(formal)
        if _dict_value(entity.get("external_api_design"))
    ]


def _entity_expectation(entity: dict[str, Any]) -> dict[str, Any]:
    """投射实体字段、类型、必填性和数据库绑定。"""

    database = _dict_value(entity.get("database_design"))
    return {
        "entity_id": _text(entity.get("entity_id")),
        "entity_name": _text(entity.get("entity_name") or entity.get("entity_id")),
        "data_source_type": _text(entity.get("data_source_type") or entity.get("data_source_id")),
        "fields": [
            {
                "name": _text(field.get("name")),
                "type": _text(field.get("type"), "text"),
                "required": bool(field.get("required")),
                "enum_values": _dedupe_strings(field.get("enum_values")),
            }
            for field in _dict_items(entity.get("fields"))[:_MAX_ITEMS]
            if _text(field.get("name"))
        ],
        "database_bindings": [
            {
                "entity_field": _text(binding.get("entity_field")),
                "table": _text(binding.get("table")),
                "table_column": _text(binding.get("table_column")),
                "rule": _text(binding.get("rule")),
            }
            for binding in _dict_items(database.get("bindings"))[:_MAX_ITEMS]
        ],
    }


def _external_api_expectation(entity: dict[str, Any]) -> dict[str, Any]:
    """投射外部 API 上游接口和完整字段映射，不使用 mapping_count 摘要。"""

    design = _dict_value(entity.get("external_api_design"))
    api_info = _dict_value(design.get("api_info"))
    return {
        "entity_id": _text(entity.get("entity_id")),
        "api_info": {
            "method": _text(api_info.get("method"), "GET").upper(),
            "path": _text(api_info.get("path")),
            "request_body": api_info.get("request_body"),
            "response_body": api_info.get("response_body"),
        },
        "field_mappings": [
            {
                "entity_field": _text(mapping.get("entity_field")),
                "source_field": _text(mapping.get("source_field")),
                "rule": _text(mapping.get("rule")),
            }
            for mapping in _dict_items(design.get("field_mappings"))[:_MAX_ITEMS]
        ],
    }


def _entity_sources(formal: dict[str, Any], entity: dict[str, Any]) -> list[dict[str, Any]]:
    """为实体设计生成带完整内容哈希的正式来源引用。"""

    entity_id = _text(entity.get("entity_id"))
    return [_source("entity_design", entity_id, f"/entity_detail_plans/{entity_id}", entity)]


def _api_sources(formal: dict[str, Any], endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为 API Contract endpoint 切片生成来源引用。"""

    sources: list[dict[str, Any]] = []
    for endpoint in endpoints:
        contract_id = _text(endpoint.get("api_contract_id"))
        endpoint_id = _text(endpoint.get("endpoint_id"))
        payload = {
            "contract_id": contract_id,
            "endpoint": endpoint,
            "schemas": _contract_schemas_for_endpoint(formal, contract_id),
        }
        sources.append(
            _source(
                "api_contract",
                endpoint_id,
                f"/api_contracts/{contract_id}/endpoints/{endpoint_id}",
                payload,
            )
        )
    return sources


def _contract_schemas_for_endpoint(formal: dict[str, Any], contract_id: str) -> dict[str, Any]:
    """返回 API Contract 当前契约的完整 schema 切片，保证来源哈希反映结构变化。"""

    for contract in _dict_items(formal.get("contracts")):
        if _text(contract.get("id")) == contract_id:
            return deepcopy(_dict_value(contract.get("schemas")))
    return {}


def _page_sources(formal: dict[str, Any], endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为页面契约和其接口依赖生成来源引用。"""

    page = _dict_value(formal.get("page_contract"))
    page_id = _text(page.get("pageId"))
    sources = [_source("page_implementation_contract", page_id, f"/page_implementation_contracts/{page_id}", page)] if page else []
    return sources + _api_sources(formal, endpoints)


def _endpoint_detail_sources(formal: dict[str, Any]) -> list[dict[str, Any]]:
    """为当前 EndpointDetail 切片生成正式来源引用，优先使用外置 sha256。"""

    ref_by_id = {
        _text(item.get("id")): item
        for item in _dict_items(_dict_value(formal.get("source_refs")).get("endpoint_details"))
        if _text(item.get("id"))
    }
    result: list[dict[str, Any]] = []
    for detail in _dict_items(formal.get("endpoint_details")):
        endpoint_id = _text(detail.get("endpoint_id"))
        if not endpoint_id:
            continue
        ref = ref_by_id.get(endpoint_id, {})
        preferred_hash = _text(ref.get("sha256")) or _endpoint_reference_hash(
            formal,
            endpoint_id,
        )
        result.append(
            _source(
                "endpoint_detail",
                endpoint_id,
                f"/endpoint_detail_plans/{endpoint_id}",
                detail,
                preferred_hash=preferred_hash,
            )
        )
    return result


def _endpoint_reference_hash(formal: dict[str, Any], endpoint_id: str) -> str:
    """读取 API Contract 中 EndpointDetail 的当前正式引用哈希。"""

    for contract in _dict_items(formal.get("contracts")):
        for endpoint in _dict_items(contract.get("endpoints")):
            if _text(endpoint.get("id")) != endpoint_id:
                continue
            detail_ref = endpoint.get("detail_design")
            if isinstance(detail_ref, dict) and _text(detail_ref.get("sha256")):
                return _text(detail_ref.get("sha256"))
    return ""


def _source(
    artifact: str,
    target_id: str,
    pointer: str,
    payload: Any,
    *,
    preferred_hash: str = "",
) -> dict[str, Any]:
    """创建不含源码正文的可追溯正式来源引用。"""

    return {
        "artifact": artifact,
        "target_id": target_id,
        "pointer": pointer,
        "sha256": preferred_hash or _stable_hash(payload),
    }


def _expected_field_errors(check_id: str, kind: str, value: Any) -> list[str]:
    """校验每种检查的结构化 expected 至少包含可执行输入。"""

    expected = _dict_value(value)
    required_fields = {
        "frontend.api_contract": ("endpoints",),
        "frontend.page_endpoint_usage": ("required_endpoint_ids",),
        "frontend.static_data_contract": ("entity", "endpoints", "operations"),
        "backend.domain_mapping": ("entities", "endpoints"),
        "backend.repository_contract": ("entities", "operations"),
        "backend.application_service_contract": ("operations",),
        "backend.endpoint_contract": ("endpoints",),
        "backend.external_api_client_contract": ("external_apis",),
        "backend.external_api_mapping_contract": ("external_apis",),
    }.get(kind, ())
    return [
        f"Business check {check_id or '<unknown>'} expected is missing {field}."
        for field in required_fields
        if field not in expected
    ]


def _page_deliverable_errors(task: dict[str, Any], deliverable: dict[str, Any]) -> list[str]:
    """校验页面交付物包含当前页面入口。"""

    unit_id = _text(task.get("unit_id"))
    page_id = unit_id.split(":", 1)[1] if unit_id.startswith("page:") else _text(deliverable.get("target_id"))
    if not page_id:
        return []
    expected_keys = {_page_key(page_id).casefold()}
    # 既有 React 模板同时使用 ``Orders`` 和 ``OrdersPage`` 目录命名，
    # 校验只接受这两个由当前 PageKey 确定的形式，不依赖宿主系统大小写策略。
    expected_keys.add(f"{_page_key(page_id)}Page".casefold())
    if not any(
        "/pages/" in f"/{normalize_repo_path(path).casefold()}/"
        and normalize_repo_path(path).casefold().endswith("/index.tsx")
        and any(
            f"/pages/{expected}/" in f"/{normalize_repo_path(path).casefold()}/"
            for expected in expected_keys
        )
        for path in deliverable.get("paths", [])
    ):
        return [f"Page deliverable {deliverable['id']} must include the {page_id} page entry."]
    return []


def _endpoint_deliverable_errors(task: dict[str, Any], deliverable: dict[str, Any]) -> list[str]:
    """校验 Controller 交付物属于当前 endpoint Unit。"""

    if not _text(deliverable.get("target_id")):
        return [f"Endpoint controller deliverable {deliverable['id']} must declare target_id."]
    source_refs = _dict_value(task.get("source_refs"))
    endpoint_ids = set(_string_list(source_refs.get("endpoint_ids")))
    target_id = _text(deliverable.get("target_id"))
    unit_endpoint_id = _text(task.get("unit_id")).rsplit(":", 1)[-1]
    allowed_endpoint_ids = endpoint_ids or ({unit_endpoint_id} if unit_endpoint_id else set())
    return [] if target_id in allowed_endpoint_ids else [
        f"Endpoint controller deliverable {deliverable['id']} targets an endpoint outside the Unit."
    ]


def _requires_business_deliverable(task: dict[str, Any]) -> bool:
    """判断任务是否属于需要业务交付物的可执行代码任务。"""

    if _text(task.get("kind")) == "repair":
        return False
    owner = _text(task.get("owner"))
    unit_id = _text(task.get("unit_id"))
    if owner not in {"frontend", "backend"}:
        return False
    return unit_id not in {"frontend:shell", "frontend:api-client", "frontend:auth-guard", "backend:bootstrap"}


def _task_allowed_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务声明的所有授权路径并归一化分隔符。"""

    paths = _dedupe_paths(task.get("allowed_paths"))
    paths.extend(_dedupe_paths(task.get("target_files")))
    paths.extend(
        normalize_repo_path(item.get("path"))
        for item in _dict_items(task.get("change_scope"))
        if normalize_repo_path(item.get("path"))
    )
    return _dedupe_strings(paths)


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    """以跨平台大小写不敏感方式匹配精确、目录和通配授权路径。"""

    candidate = normalize_repo_path(path).casefold()
    for raw_pattern in patterns:
        pattern = normalize_repo_path(raw_pattern).casefold()
        if not pattern:
            continue
        if pattern.endswith("/**") and candidate.startswith(pattern[:-3].rstrip("/") + "/"):
            return True
        if candidate == pattern or candidate.startswith(pattern.rstrip("/") + "/"):
            return True
        regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        if re.fullmatch(regex, candidate):
            return True
    return False


def _is_absolute_repo_path(value: Any) -> bool:
    """识别 POSIX、UNC 和 Windows drive 绝对路径。"""

    text = str(value or "").strip().replace("\\", "/")
    return text.startswith("/") or bool(re.match(r"^[A-Za-z]:/", text)) or text.startswith("//")


def _schema_descriptor(schemas: dict[str, Any], schema_ref: Any) -> dict[str, Any] | None:
    """把 JSON Schema 投射为有界结构描述，保留嵌套、数组、枚举和必填性。"""

    name = _text(schema_ref).rsplit("/", 1)[-1]
    if not name:
        return None
    visited: set[str] = set()

    def visit(value: Any, depth: int) -> dict[str, Any] | None:
        """递归读取 schema 节点并限制引用深度。"""

        if depth > _MAX_SCHEMA_DEPTH or not isinstance(value, dict):
            return None
        ref = _text(value.get("$ref")).rsplit("/", 1)[-1]
        if ref:
            if ref in visited:
                return {"$ref": ref}
            visited.add(ref)
            resolved = visit(schemas.get(ref), depth + 1)
            if resolved is None:
                return {"$ref": ref}
            return {"$ref": ref, **resolved}
        result: dict[str, Any] = {}
        if value.get("type"):
            result["type"] = value.get("type")
        if isinstance(value.get("enum"), list):
            result["enum"] = deepcopy(value["enum"][:_MAX_ITEMS])
        if isinstance(value.get("required"), list):
            result["required"] = _dedupe_strings(value.get("required"))
        properties = value.get("properties")
        if isinstance(properties, dict):
            required_names = {
                str(item)
                for item in value.get("required", [])
                if str(item).strip()
            }
            result["properties"] = {
                str(key): {
                    **(visit(child, depth + 1) or {}),
                    "required": str(key) in required_names,
                }
                for key, child in list(properties.items())[:_MAX_ITEMS]
                if str(key).strip()
            }
        if value.get("items") is not None:
            result["items"] = visit(value.get("items"), depth + 1) or {}
        return result

    return visit(schemas.get(name), 0)


def _project_parameters(value: Any) -> list[dict[str, Any]]:
    """投射 endpoint 参数位置、必填性和结构引用。"""

    return [
        {
            "name": _text(item.get("name")),
            "in": _text(item.get("in"), "query"),
            "required": bool(item.get("required")),
            "schema": deepcopy(item.get("schema")) if isinstance(item.get("schema"), dict) else {},
        }
        for item in _dict_items(value)[:_MAX_ITEMS]
        if _text(item.get("name"))
    ]


def _page_key(value: str) -> str:
    """按模板约定把 page id 转换为稳定 PascalCase 页面目录名。"""

    pieces = [piece for piece in re.split(r"[-_\s]+", str(value or "")) if piece]
    return "".join(piece[:1].upper() + piece[1:].lower() for piece in pieces) or "Page"


def _unique_objects(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """按复合标识去重对象，保留正式输入顺序。"""

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        key = tuple(_text(item.get(name)) for name in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _stable_hash(value: Any) -> str:
    """用排序 JSON 生成跨平台稳定 SHA-256。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _dedupe_paths(value: Any) -> list[str]:
    """归一化并去重路径，保留首次出现顺序。"""

    if not isinstance(value, list):
        return []
    normalized = [normalize_repo_path(item) for item in value]
    return _dedupe_strings([item for item in normalized if item])


def _dedupe_strings(value: Any) -> list[str]:
    """把不可信列表归一为非空唯一字符串。"""

    result: list[str] = []
    for item in value if isinstance(value, (list, tuple, set)) else []:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    """读取字符串数组并去除空白项。"""

    return _dedupe_strings(value)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """读取列表中的字典项。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    """读取字典输入并避免不可信对象向下传播。"""

    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any, default: str = "") -> str:
    """规整单个文本值并提供安全默认值。"""

    text = str(value or "").strip()
    return text or default
