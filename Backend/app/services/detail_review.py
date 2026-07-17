from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_contracts import (
    endpoint_dependencies_from_page_api_dependencies,
    normalize_page_api_dependencies,
    normalize_response_bindings,
)


PAGE_EDITABLE_FIELDS = {
    "page_goal",
    "basic_layout",
    "layout_design",
    "interactions",
    "state_feedback",
    "operation_interactions",
    "response_bindings",
    "operation_visibility",
    "acceptance_criteria",
}
DATA_SOURCE_EDITABLE_FIELDS = {
    "source_type",
    "entities",
    "schema_refs",
    "relationships",
    "validation_rules",
    "seed_strategy",
    "api_contracts",
    "dependent_pages",
    "acceptance_criteria",
}


def detail_review_payload(project_plan: dict[str, Any]) -> dict[str, Any]:
    project_plan = deepcopy(project_plan)
    _repair_page_contract_fields(project_plan)
    pages = [
        {
            "target_type": "page",
            "target_id": detail.get("page_id"),
            "name": detail.get("page_name") or detail.get("page_id"),
            "path": detail.get("path"),
            "page_goal": detail.get("page_goal"),
            "basic_layout": detail.get("basic_layout", {}),
            "layout_design": detail.get("layout_design", {}),
            "interactions": detail.get("interactions", []),
            "state_feedback": detail.get("state_feedback", []),
            "operation_interactions": detail.get("operation_interactions", []),
            "operation_visibility": detail.get("operation_visibility", []),
            "page_navigation": detail.get("page_navigation", []),
            "permissions": detail.get("permissions", []),
            "states": detail.get("basic_layout", {}).get("states", []),
            "api_dependencies": detail.get("api_dependencies", []),
            "data_sources": detail.get("data_sources", []),
            "response_bindings": detail.get("response_bindings", []),
            "acceptance_criteria": detail.get("acceptance_criteria", []),
        }
        for detail in project_plan.get("page_detail_plans", [])
        if isinstance(detail, dict) and detail.get("page_id")
    ]
    data_sources = [
        {
            "target_type": "data_source",
            "target_id": detail.get("data_source_id"),
            "name": detail.get("data_source_name") or detail.get("data_source_id"),
            "source_type": detail.get("source_data_source", {}).get("type"),
            "entities": detail.get("entities", []),
            "schema_refs": detail.get("schema_refs", []),
            "relationships": detail.get("relationships", []),
            "validation_rules": detail.get("validation_rules", []),
            "seed_strategy": detail.get("seed_strategy"),
            "api_contracts": detail.get("api_contracts", []),
            "dependent_pages": detail.get("dependent_pages", []),
            "acceptance_criteria": detail.get("acceptance_criteria", []),
        }
        for detail in project_plan.get("data_source_detail_plans", [])
        if isinstance(detail, dict) and detail.get("data_source_id")
    ]
    return {
        "mode": "detail_review",
        "status": "requires_user_input",
        "question_schema": "xcodeagent.detail_review.v1",
        "questions": [],
        "message": "请整体审阅全部页面和数据源初版设计；仅展开需要调整的对象，确认后一次进入任务拆分。",
        "review": {
            "pages": pages,
            "data_sources": data_sources,
            "summary": {
                "page_count": len(pages),
                "data_source_count": len(data_sources),
                "api_contract_count": len(project_plan.get("api_contracts", [])),
            },
        },
    }


def apply_detail_review_submission(
    project_plan: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
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
                "page_id",
                target_id,
                changes,
                PAGE_EDITABLE_FIELDS,
            )
        elif target_type == "data_source":
            _apply_target_patch(
                updated.get("data_source_detail_plans", []),
                "data_source_id",
                target_id,
                changes,
                DATA_SOURCE_EDITABLE_FIELDS,
            )
        else:
            raise ValueError(f"unsupported detail review target type: {target_type}")

    for detail in updated.get("page_detail_plans", []):
        if isinstance(detail, dict):
            detail["status"] = "confirmed"
            detail["approved"] = True
    for detail in updated.get("data_source_detail_plans", []):
        if isinstance(detail, dict):
            detail["status"] = "confirmed"
            detail["approved"] = True
    # 仅提升本轮实际生成并审阅过的对象，避免选中单页时误确认其他页面。
    confirmed_page_ids = {
        str(detail.get("page_id"))
        for detail in updated.get("page_detail_plans", [])
        if isinstance(detail, dict) and detail.get("page_id")
    }
    confirmed_data_source_ids = {
        str(detail.get("data_source_id"))
        for detail in updated.get("data_source_detail_plans", [])
        if isinstance(detail, dict) and detail.get("data_source_id")
    }
    for page in updated.get("frontend_pages", []):
        if isinstance(page, dict) and str(page.get("id")) in confirmed_page_ids:
            page["detail_status"] = "confirmed"
    for source in updated.get("data_sources", []):
        if isinstance(source, dict) and str(source.get("id")) in confirmed_data_source_ids:
            source["detail_status"] = "confirmed"
    _repair_page_contract_fields(updated)
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


def _repair_page_contract_fields(project_plan: dict[str, Any]) -> None:
    contracts = project_plan.get("api_contracts", [])
    data_source_ids = [
        str(source.get("id"))
        for source in project_plan.get("data_sources", [])
        if isinstance(source, dict) and source.get("id")
    ]
    for detail in project_plan.get("page_detail_plans", []):
        if not isinstance(detail, dict) or not detail.get("page_id"):
            continue
        api_dependencies = normalize_page_api_dependencies(
            contracts if isinstance(contracts, list) else [],
            data_source_ids if isinstance(data_source_ids, list) else [],
            detail.get("api_dependencies") or [],
            page_path=str(detail.get("path") or ""),
            page_name=str(
                detail.get("page_name")
                or detail.get("page_id")
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
    }:
        return _dict_list(value)
    if key in {
        "interactions",
        "permissions",
        "acceptance_criteria",
        "entities",
        "schema_refs",
        "relationships",
        "validation_rules",
    }:
        return _string_list(value)
    if key in {
        "api_contracts",
        "dependent_pages",
    }:
        return _dict_list(value)
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
