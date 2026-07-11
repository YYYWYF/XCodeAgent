from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.api_contract_validation import validate_api_contract_consistency


PAGE_EDITABLE_FIELDS = {
    "page_goal",
    "basic_layout",
    "interactions",
    "permissions",
    "acceptance_criteria",
}
DATA_SOURCE_EDITABLE_FIELDS = {
    "relationships",
    "validation_rules",
    "seed_strategy",
    "acceptance_criteria",
}


def detail_review_payload(project_plan: dict[str, Any]) -> dict[str, Any]:
    pages = [
        {
            "target_type": "page",
            "target_id": detail.get("page_id"),
            "name": detail.get("page_name") or detail.get("page_id"),
            "path": detail.get("path"),
            "page_goal": detail.get("page_goal"),
            "basic_layout": detail.get("basic_layout", {}),
            "interactions": detail.get("interactions", []),
            "permissions": detail.get("permissions", []),
            "states": detail.get("basic_layout", {}).get("states", []),
            "data_sources": detail.get("data_sources", []),
            "page_dependencies": detail.get("page_dependencies", {}),
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
    for page in updated.get("frontend_pages", []):
        if isinstance(page, dict):
            page["detail_status"] = "confirmed"
    for source in updated.get("data_sources", []):
        if isinstance(source, dict):
            source["detail_status"] = "confirmed"
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
    if key in {
        "interactions",
        "permissions",
        "acceptance_criteria",
        "relationships",
        "validation_rules",
    }:
        return _string_list(value)
    return str(value).strip() if value is not None else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.replace("，", "\n").replace("；", "\n")
        return [item.strip() for item in normalized.splitlines() if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
