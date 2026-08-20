from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_contracts import (
    endpoint_dependencies_from_page_api_dependencies,
    normalize_page_api_dependencies,
    normalize_response_bindings,
)
from app.services.entity_detail_plan import refresh_entity_detail_table_design
from app.services.entity_design import (
    entity_design_selection_summary,
    entity_design_stage,
    entity_design_summary,
)
from app.services.frontend_page_tree import update_frontend_page_leaves
from app.services.page_detail_plan import (
    refresh_endpoint_detail_from_decision,
)


PAGE_EDITABLE_FIELDS = {
    "page_goal",
    "basic_layout",
    "layout_design",
    "interactions",
    "state_feedback",
    "operation_interactions",
    "operation_visibility",
    "acceptance_criteria",
}
ENDPOINT_EDITABLE_FIELDS = {
    "data_usage",
    "endpoint_decision",
    "interface_design",
    "dependent_pages",
}
ENTITY_EDITABLE_FIELDS = {
    "data_source_type",
    "database_design",
    "external_api_design",
    "static_design",
    "business_rules",
    "relationships",
    "acceptance_criteria",
    "risks",
}


def detail_review_payload(
    project_plan: dict[str, Any],
    *,
    selectedPageId: str | None = None,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    selected_entity_id: str | None = None,
    detail_target_type: str | None = None,
) -> dict[str, Any]:
    """构造本轮细节审核载荷；选中目标时只投射该目标相关详情（页面/接口/实体）。"""

    project_plan = deepcopy(project_plan)
    pages = [
        {
            "target_type": "page",
            "target_id": detail.get("pageId"),
            "name": detail.get("page_name") or detail.get("pageId"),
            "path": detail.get("path"),
            "page_goal": detail.get("page_goal"),
            "basic_layout": _dict_value(detail.get("basic_layout")),
            "layout_design": _dict_value(detail.get("layout_design")),
            "interactions": _list_value(detail.get("interactions")),
            "state_feedback": _list_value(detail.get("state_feedback")),
            "operation_interactions": _list_value(detail.get("operation_interactions")),
            "operation_visibility": _list_value(detail.get("operation_visibility")),
            "page_navigation": _page_reference_items(detail, "navigation_targets", "page_navigation"),
            "permissions": _page_reference_items(detail, "permissions", "permissions"),
            "states": _list_value(_dict_value(detail.get("basic_layout")).get("states")),
            "api_dependencies": _page_reference_items(
                detail,
                "endpoint_dependencies",
                "api_dependencies",
            ),
            "response_bindings": _list_value(detail.get("response_bindings")),
            "acceptance_criteria": _list_value(detail.get("acceptance_criteria")),
        }
        for detail in project_plan.get("page_detail_plans", [])
        if isinstance(detail, dict)
        and detail.get("pageId")
        and (
            not selectedPageId
            or str(detail.get("pageId")) == selectedPageId
        )
    ]
    endpoints = _endpoint_review_items(
        project_plan,
        selected_api_contract_id=selected_api_contract_id,
        selected_endpoint_id=selected_endpoint_id,
    )
    entities = _entity_review_items(project_plan, selected_entity_id=selected_entity_id)
    missingSelectedPagePlan = bool(
        selectedPageId and not pages and not str(detail_target_type or "").startswith("endpoint")
    )
    missingSelectedEndpointPlan = bool(detail_target_type == "endpoint" and selected_endpoint_id and not endpoints)
    missingSelectedEntityPlan = bool(detail_target_type == "entity" and selected_entity_id and not entities)
    return {
        "mode": "detail_review",
        "status": "requires_user_input",
        "question_schema": "xcodeagent.detail_review.v1",
        "questions": [],
        "message": (
            f"页面 `{selectedPageId}` 还没有生成细节设计，请先生成该页面的 plan。"
            if missingSelectedPagePlan
            else
            f"接口 `{selected_endpoint_id}` 还没有生成细节设计，请先生成该接口的 plan。"
            if missingSelectedEndpointPlan
            else
            f"实体 `{selected_entity_id}` 尚未开始实体设计，请先选择数据源"
            f"（数据库 / 外部 API / 静态数据）。"
            if missingSelectedEntityPlan
            else
            f"请审阅接口 `{selected_endpoint_id}` 详细设计；仅展开需要调整的对象。"
            if detail_target_type == "endpoint" and selected_endpoint_id
            else
            f"请审阅实体 `{selected_entity_id}` 详细设计；仅展开需要调整的对象。"
            if detail_target_type == "entity" and selected_entity_id
            else
            f"请审阅页面 `{selectedPageId}` 依赖的接口详细设计；页面视觉以已确认 React UI 稿为准。"
            if detail_target_type == "endpoint_batch" and selectedPageId
            else
            f"请审阅页面 `{selectedPageId}` 详细设计；仅展开需要调整的对象。"
            if selectedPageId
            else "请整体审阅页面、接口与实体初版设计；仅展开需要调整的对象，确认后一次进入任务拆分。"
        ),
        "review": {
            "pages": pages,
            "endpoints": endpoints,
            "entities": entities,
            "summary": {
                "page_count": len(pages),
                "endpoint_count": len(endpoints),
                "entity_count": len(entities),
                "api_contract_count": len(project_plan.get("api_contracts", [])),
                "missingSelectedPagePlan": missingSelectedPagePlan,
                "missingSelectedEndpointPlan": missingSelectedEndpointPlan,
                "missingSelectedEntityPlan": missingSelectedEntityPlan,
                "selectedPageId": selectedPageId,
                "selectedApiContractId": selected_api_contract_id,
                "selectedEndpointId": selected_endpoint_id,
                "selectedEntityId": selected_entity_id,
                "entityDesign": _entity_design_summary(
                    project_plan,
                    selected_entity_id,
                    entities,
                ),
                "detailTargetType": detail_target_type,
            },
        },
    }


def apply_detail_review_submission(
    project_plan: dict[str, Any],
    submission: dict[str, Any],
    *,
    selectedPageId: str | None = None,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    selected_entity_id: str | None = None,
) -> dict[str, Any]:
    """应用细节审核提交；选中目标审核只确认当前目标（页面/接口/实体）相关详情。"""

    if submission.get("review_status") != "confirmed":
        raise ValueError("detail review submission must be explicitly confirmed")

    updated = deepcopy(project_plan)
    for patch in submission.get("target_changes", []):
        if not isinstance(patch, dict):
            continue
        target_type = str(patch.get("target_type") or "")
        target_id = str(patch.get("target_id") or "")
        changes = patch.get("changes")
        if not target_id or not isinstance(changes, dict):
            continue
        if target_type == "page":
            _apply_target_patch(
                updated.get("page_detail_plans", []),
                "pageId",
                target_id,
                changes,
                PAGE_EDITABLE_FIELDS,
            )
        elif target_type == "endpoint":
            _apply_endpoint_target_patch(
                updated.get("endpoint_detail_plans", []),
                target_id,
                changes,
            )
        elif target_type == "entity":
            _apply_target_patch(
                updated.get("entity_detail_plans", []),
                "entity_id",
                target_id,
                changes,
                ENTITY_EDITABLE_FIELDS,
            )
        else:
            raise ValueError(f"unsupported detail review target type: {target_type}")

    for detail in updated.get("page_detail_plans", []):
        if isinstance(detail, dict) and (
            not selectedPageId
            or str(detail.get("pageId")) == selectedPageId
        ):
            detail["status"] = "confirmed"
            detail["approved"] = True
    for detail in updated.get("endpoint_detail_plans", []):
        if isinstance(detail, dict) and (
            bool(selectedPageId)
            or (
                not selectedPageId
                and not selected_endpoint_id
            )
            or (
                str(detail.get("api_contract_id") or "") == str(selected_api_contract_id or "")
                and str(detail.get("endpoint_id") or "") == str(selected_endpoint_id or "")
            )
        ):
            detail["status"] = "confirmed"
            detail["approved"] = True
    for detail in updated.get("entity_detail_plans", []):
        if isinstance(detail, dict):
            refresh_entity_detail_table_design(detail)
        if isinstance(detail, dict) and (
            not selected_entity_id
            or str(detail.get("entity_id") or "") == str(selected_entity_id or "")
        ):
            detail["status"] = "confirmed"
            detail["approved"] = True
    # 仅提升本轮实际生成并审阅过的对象，避免选中单页时误确认其他页面。
    confirmedPageIds = {
        str(detail.get("pageId"))
        for detail in updated.get("page_detail_plans", [])
        if isinstance(detail, dict)
        and detail.get("pageId")
            and detail.get("status") == "confirmed"
    }
    page_field = "pages" if updated.get("artifact_type") == "technical-plan" else "frontend_pages"
    updated[page_field] = update_frontend_page_leaves(
        updated.get(page_field),
        {
            page_id: {"detail_status": "confirmed"}
            for page_id in confirmedPageIds
            if page_id
        },
    )
    for contract in updated.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        for endpoint_index, endpoint in enumerate(contract.get("endpoints", []) or []):
            if not isinstance(endpoint, dict):
                continue
            if any(
                isinstance(detail, dict)
                and detail.get("status") == "confirmed"
                and str(detail.get("api_contract_id") or "") == str(contract.get("id") or "")
                and str(detail.get("endpoint_id") or "")
                == _endpoint_identity(endpoint, endpoint_index)
                for detail in updated.get("endpoint_detail_plans", [])
            ):
                endpoint["detail_status"] = "confirmed"
    confirmedEntityIds = {
        str(detail.get("entity_id"))
        for detail in updated.get("entity_detail_plans", [])
        if isinstance(detail, dict)
        and detail.get("entity_id")
        and detail.get("status") == "confirmed"
    }
    updated["entities"] = [
        {
            **entity,
            "detail_status": "confirmed",
        }
        if isinstance(entity, dict)
        and str(entity.get("id") or "") in confirmedEntityIds
        else entity
        for entity in updated.get("entities", [])
    ]
    _repair_page_contract_fields(updated)
    _repair_missing_request_schemas(updated)
    updated["confirmation_status"] = "confirmed"
    updated["detail_review"] = {
        "status": "confirmed",
        "changed_target_count": len(submission.get("target_changes", [])),
        "overall_note": str(submission.get("overall_note") or "").strip(),
    }

    errors = validate_api_contract_consistency(updated)
    if errors:
        raise ValueError("Detail review violates API contracts: " + "; ".join(errors))
    return updated


def _repair_missing_request_schemas(project_plan: dict[str, Any]) -> None:
    """为缺少 request_schema_ref 的写操作端点补一个默认请求 Schema。

    大模型生成批量删除等端点时常漏写 request schema，导致契约校验失败。
    这里按端点语义补一个合理的默认 Schema，避免阻塞 detail_confirmation。
    """

    for contract in project_plan.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        schemas = contract.setdefault("schemas", {})
        for endpoint in contract.get("endpoints", []) or []:
            if not isinstance(endpoint, dict):
                continue
            method = str(endpoint.get("method") or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                continue
            if endpoint.get("request_schema_ref"):
                continue
            endpoint_id = str(endpoint.get("id") or "")
            path = str(endpoint.get("path") or "")
            # 批量删除类端点：{ ids: string[] }
            if "batch" in path.lower() or "batch" in endpoint_id.lower():
                schema_id = "BatchDeleteRequest"
                schemas[schema_id] = {
                    "type": "object",
                    "properties": {
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["ids"],
                }
            else:
                schema_id = f"{endpoint_id}Request"
                schemas[schema_id] = {"type": "object", "properties": {}}
            endpoint["request_schema_ref"] = schema_id


def _repair_page_contract_fields(project_plan: dict[str, Any]) -> None:
    """按页面声明的 Endpoint 依赖重建受契约控制的依赖和响应绑定。"""

    contracts = project_plan.get("api_contracts", [])
    planned_entity_ids = [
        str(entity.get("id"))
        for entity in project_plan.get("entities", [])
        if isinstance(entity, dict) and entity.get("id")
    ]
    for detail in project_plan.get("page_detail_plans", []):
        if not isinstance(detail, dict) or not detail.get("pageId"):
            continue
        declared_api_dependencies = _page_reference_items(
            detail,
            "endpoint_dependencies",
            "api_dependencies",
        )
        api_dependencies = normalize_page_api_dependencies(
            contracts if isinstance(contracts, list) else [],
            planned_entity_ids,
            declared_api_dependencies,
            page_path=str(detail.get("path") or ""),
            page_name=str(
                detail.get("page_name")
                or detail.get("pageId")
                or "",
            ),
        )
        endpoint_dependencies = endpoint_dependencies_from_page_api_dependencies(
            api_dependencies
        )
        detail["api_dependencies"] = api_dependencies
        detail["response_bindings"] = normalize_response_bindings(
            contracts if isinstance(contracts, list) else [],
            endpoint_dependencies if isinstance(endpoint_dependencies, list) else [],
            detail.get("response_bindings"),
        )


def _page_reference_items(
    detail: dict[str, Any],
    reference_key: str,
    runtime_key: str,
) -> list[Any]:
    """优先展示详情文件里的 references；运行期详情尚未外置时读取同源字段。"""

    references = detail.get("references") if isinstance(detail.get("references"), dict) else {}
    value = references.get(reference_key) if reference_key in references else detail.get(runtime_key)
    return value if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    """只接受对象字段，避免展示真实数据时链式 get 崩溃。"""

    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    """只接受数组字段，字符串等历史脏值按空数组展示。"""

    return value if isinstance(value, list) else []


def _endpoint_identity(endpoint: dict[str, Any], index: int) -> str:
    """返回 endpoint 的确认状态匹配标识；没有显式 id 时使用选择器序号。"""

    return str(endpoint.get("id") or index + 1)


def _endpoint_review_items(
    project_plan: dict[str, Any],
    *,
    selected_api_contract_id: str | None,
    selected_endpoint_id: str | None,
) -> list[dict[str, Any]]:
    """构造 endpoint 审核对象；选中接口时只返回该接口。"""

    items: list[dict[str, Any]] = []
    for detail in project_plan.get("endpoint_detail_plans", []):
        if not isinstance(detail, dict) or not detail.get("endpoint_id"):
            continue
        api_contract_id = str(detail.get("api_contract_id") or "")
        endpoint_id = str(detail.get("endpoint_id") or "")
        if selected_api_contract_id and api_contract_id != selected_api_contract_id:
            continue
        if selected_endpoint_id and endpoint_id != selected_endpoint_id:
            continue
        items.append(
            {
                "target_type": "endpoint",
                "target_id": f"{api_contract_id}:{endpoint_id}",
                "name": detail.get("name") or f"{detail.get('method')} {detail.get('path')}",
                "api_contract_id": api_contract_id,
                "endpoint_id": endpoint_id,
                "method": detail.get("method"),
                "path": detail.get("path"),
                "summary": detail.get("summary"),
                "data_usage": _dict_value(detail.get("data_usage")),
                "endpoint_decision": _dict_value(detail.get("endpoint_decision")),
                "interface_design": _dict_value(detail.get("interface_design")),
                "processing_logic": _list_value(detail.get("processing_logic")),
                "dependent_pages": _list_value(
                    _dict_value(detail.get("data_usage")).get("served_pages")
                    or detail.get("dependent_pages")
                ),
                "acceptance_criteria": _list_value(detail.get("acceptance_criteria")),
                "risks": _list_value(detail.get("risks")),
            }
        )
    return items


def _entity_review_items(
    project_plan: dict[str, Any],
    *,
    selected_entity_id: str | None,
) -> list[dict[str, Any]]:
    """构造实体审核对象；选中实体时只返回该实体。"""

    items: list[dict[str, Any]] = []
    for detail in project_plan.get("entity_detail_plans", []):
        if not isinstance(detail, dict) or not detail.get("entity_id"):
            continue
        entity_id = str(detail.get("entity_id") or "")
        if selected_entity_id and entity_id != str(selected_entity_id or ""):
            continue
        items.append(
            {
                "target_type": "entity",
                "target_id": entity_id,
                "name": detail.get("entity_name") or entity_id,
                "entity_id": entity_id,
                "description": detail.get("description"),
                "module_id": detail.get("module_id"),
                "data_source_id": detail.get("data_source_id"),
                "data_source_type": detail.get("data_source_type"),
                "design_stage": entity_design_stage(detail),
                "fields": _list_value(detail.get("fields")),
                "table_design": _dict_value(detail.get("table_design")),
                "database_design": _dict_value(detail.get("database_design")),
                "external_api_design": _dict_value(detail.get("external_api_design")),
                "static_design": _dict_value(detail.get("static_design")),
                "database_execution": _dict_value(detail.get("database_execution")),
                "table_operations_executed": bool(detail.get("table_operations_executed")),
                "business_rules": _list_value(detail.get("business_rules")),
                "relationships": _list_value(detail.get("relationships")),
                "acceptance_criteria": _list_value(detail.get("acceptance_criteria")),
                "risks": _list_value(detail.get("risks")),
            }
        )
    return items


def _entity_design_summary(
    project_plan: dict[str, Any],
    selected_entity_id: str | None,
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """构造实体设计阶段摘要；无详情时投射数据源选择摘要，有详情时投射分方案摘要。"""

    if not selected_entity_id:
        return None
    if entities:
        detail_like = {
            "entity_id": entities[0].get("entity_id"),
            "entity_name": entities[0].get("name"),
            "fields": _list_value(entities[0].get("fields")),
            "data_source_type": entities[0].get("data_source_type"),
            "status": "pending_user_confirmation",
            "design_stage": entities[0].get("design_stage"),
            "database_design": entities[0].get("database_design"),
            "external_api_design": entities[0].get("external_api_design"),
            "static_design": entities[0].get("static_design"),
            "database_execution": entities[0].get("database_execution"),
        }
        return entity_design_summary(detail_like)
    entity = next(
        (
            item
            for item in project_plan.get("entities", [])
            if isinstance(item, dict) and str(item.get("id") or "") == selected_entity_id
        ),
        None,
    )
    if entity is None:
        return {"stage": "data_source_selection", "entity_id": selected_entity_id}
    return entity_design_selection_summary(entity)


def _apply_target_patch(
    details: Any,
    id_field: str,
    target_id: str,
    changes: dict[str, Any],
    allowed_fields: set[str],
) -> None:
    unknown_fields = set(changes) - allowed_fields
    if unknown_fields:
        raise ValueError(
            f"detail review cannot change contract-controlled fields: {sorted(unknown_fields)}"
        )
    target = next(
        (
            detail
            for detail in details
            if isinstance(detail, dict) and str(detail.get(id_field)) == target_id
        ),
        None,
    )
    if target is None:
        raise ValueError(f"unknown detail review target: {target_id}")
    for key, value in changes.items():
        target[key] = _normalize_editable_value(key, value, target.get(key))


def _apply_endpoint_target_patch(
    details: Any,
    target_id: str,
    changes: dict[str, Any],
) -> None:
    """按 apiContractId:endpointId 定位 endpoint 详情并应用用户可编辑字段。"""

    unknown_fields = set(changes) - ENDPOINT_EDITABLE_FIELDS
    if unknown_fields:
        raise ValueError(
            f"endpoint detail review cannot change contract-controlled fields: {sorted(unknown_fields)}"
        )
    target = next(
        (
            detail
            for detail in details
            if isinstance(detail, dict)
            and f"{detail.get('api_contract_id')}:{detail.get('endpoint_id')}" == target_id
        ),
        None,
    )
    if target is None:
        raise ValueError(f"unknown endpoint detail review target: {target_id}")
    for key, value in changes.items():
        target[key] = _normalize_editable_value(key, value, target.get(key))
    decision = target.get("endpoint_decision")
    if isinstance(decision, dict):
        # 接口不携带数据源，EndpointDecision 只含行为语义，调整后重建派生字段。
        refresh_endpoint_detail_from_decision(target)


def _normalize_editable_value(key: str, value: Any, current: Any) -> Any:
    if key == "basic_layout":
        layout = value if isinstance(value, dict) else {}
        return {
            **(current if isinstance(current, dict) else {}),
            **layout,
            "structure": _string_list(layout.get("structure")),
        }
    if key == "layout_design":
        layout = value if isinstance(value, dict) else {}
        return {
            **(current if isinstance(current, dict) else {}),
            **layout,
            "regions": _dict_list(layout.get("regions")),
        }
    if key in {
        "state_feedback",
        "operation_interactions",
        "api_dependencies",
        "response_bindings",
        "page_navigation",
        "operation_visibility",
        "business_rules",
        "relationships",
    }:
        return _dict_list(value)
    if key in {
        "interactions",
        "permissions",
        "acceptance_criteria",
        "entities",
        "schema_refs",
        "validation_rules",
    }:
        return _string_list(value)
    if key in {
        "api_contracts",
        "dependent_pages",
    }:
        return _dict_list(value)
    if key in {
        "data_usage",
        "endpoint_decision",
        "interface_design",
        "database_design",
        "external_api_design",
        "static_design",
    }:
        return value if isinstance(value, dict) else {}
    if key == "data_source_type":
        normalized = str(value or "").strip()
        return normalized if normalized in {"database", "external_api", "static"} else ""
    if key in {"processing_logic", "risks"}:
        return _string_list(value)
    return str(value).strip() if value is not None else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.replace("，", "\n").replace("；", "\n")
        return [item.strip() for item in normalized.splitlines() if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
