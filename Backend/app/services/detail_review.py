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


def detail_review_payload(
    project_plan: dict[str, Any],
    *,
    selectedPageId: str | None = None,
    selectedDataSourceId: str | None = None,
) -> dict[str, Any]:
    """构造本轮细节审核载荷；选中目标时只投射该目标相关详情。"""

    project_plan = deepcopy(project_plan)
    selected_page_detail = _selected_page_detail(project_plan, selectedPageId)
    selected_data_source_ids = _selected_page_data_source_ids(
        project_plan,
        selected_page_detail,
    )
    if selectedDataSourceId:
        selected_data_source_ids.add(selectedDataSourceId)
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
            "data_sources": _list_value(detail.get("data_sources")),
            "response_bindings": _list_value(detail.get("response_bindings")),
            "acceptance_criteria": _list_value(detail.get("acceptance_criteria")),
        }
        for detail in project_plan.get("page_detail_plans", [])
        if isinstance(detail, dict)
        and detail.get("pageId")
        and (
            not selectedPageId
            and not selectedDataSourceId
            or str(detail.get("pageId")) == selectedPageId
        )
    ]
    data_sources = _data_source_review_items(
        project_plan,
        selectedPageId=selectedPageId,
        selectedDataSourceId=selectedDataSourceId,
        selected_data_source_ids=selected_data_source_ids,
    )
    missingSelectedPagePlan = bool(selectedPageId and not pages)
    missingSelectedDataSourcePlan = bool(selectedDataSourceId and not data_sources)
    return {
        "mode": "detail_review",
        "status": "requires_user_input",
        "question_schema": "xcodeagent.detail_review.v1",
        "questions": [],
        "message": (
            f"页面 `{selectedPageId}` 还没有生成细节设计，请先生成该页面的 plan。"
            if missingSelectedPagePlan
            else
            f"数据源 `{selectedDataSourceId}` 还没有生成细节设计，请先生成该数据源的 plan。"
            if missingSelectedDataSourcePlan
            else
            f"请审阅数据源 `{selectedDataSourceId}` 设计；仅展开需要调整的对象。"
            if selectedDataSourceId
            else
            f"请审阅页面 `{selectedPageId}` 及其直接数据源设计；仅展开需要调整的对象。"
            if selectedPageId
            else "请整体审阅全部页面和数据源初版设计；仅展开需要调整的对象，确认后一次进入任务拆分。"
        ),
        "review": {
            "pages": pages,
            "data_sources": data_sources,
            "summary": {
                "page_count": len(pages),
                "data_source_count": len(data_sources),
                "api_contract_count": len(project_plan.get("api_contracts", [])),
                "missingSelectedPagePlan": missingSelectedPagePlan,
                "missingSelectedDataSourcePlan": missingSelectedDataSourcePlan,
                "selectedPageId": selectedPageId,
                "selectedDataSourceId": selectedDataSourceId,
            },
        },
    }


def apply_detail_review_submission(
    project_plan: dict[str, Any],
    submission: dict[str, Any],
    *,
    selectedPageId: str | None = None,
    selectedDataSourceId: str | None = None,
) -> dict[str, Any]:
    """应用细节审核提交；选中目标审核只确认当前目标相关详情。"""

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

    selected_page_detail = _selected_page_detail(updated, selectedPageId)
    selected_data_source_ids = _selected_page_data_source_ids(
        updated,
        selected_page_detail,
    )
    if selectedDataSourceId:
        selected_data_source_ids.add(selectedDataSourceId)
    for detail in updated.get("page_detail_plans", []):
        if isinstance(detail, dict) and (
            not selectedPageId
            and not selectedDataSourceId
            or str(detail.get("pageId")) == selectedPageId
        ):
            detail["status"] = "confirmed"
            detail["approved"] = True
    for detail in updated.get("data_source_detail_plans", []):
        if isinstance(detail, dict) and (
            not selectedPageId
            and not selectedDataSourceId
            or str(detail.get("data_source_id")) in selected_data_source_ids
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
    confirmed_data_source_ids = {
        str(detail.get("data_source_id"))
        for detail in updated.get("data_source_detail_plans", [])
        if isinstance(detail, dict)
        and detail.get("data_source_id")
        and detail.get("status") == "confirmed"
    }
    for page in updated.get("frontend_pages", []):
        if isinstance(page, dict) and str(page.get("pageId")) in confirmedPageIds:
            page["detail_status"] = "confirmed"
    for source in updated.get("data_sources", []):
        if isinstance(source, dict) and str(source.get("id")) in confirmed_data_source_ids:
            source["detail_status"] = "confirmed"
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
    data_source_ids = [
        str(source.get("id"))
        for source in project_plan.get("data_sources", [])
        if isinstance(source, dict) and source.get("id")
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
            data_source_ids if isinstance(data_source_ids, list) else [],
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


def _selected_page_detail(
    project_plan: dict[str, Any],
    selectedPageId: str | None,
) -> dict[str, Any] | None:
    """返回当前选中页面的详情；未选页面时保持全量审核语义。"""

    if not selectedPageId:
        return None
    return next(
        (
            detail
            for detail in project_plan.get("page_detail_plans", [])
            if isinstance(detail, dict)
            and str(detail.get("pageId") or "") == selectedPageId
        ),
        None,
    )


def _data_source_review_items(
    project_plan: dict[str, Any],
    *,
    selectedPageId: str | None,
    selectedDataSourceId: str | None,
    selected_data_source_ids: set[str],
) -> list[dict[str, Any]]:
    """构造数据源审核对象，并按数据源 id 去重。"""

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for detail in project_plan.get("data_source_detail_plans", []):
        if not isinstance(detail, dict) or not detail.get("data_source_id"):
            continue
        source_id = str(detail.get("data_source_id"))
        if source_id in seen:
            continue
        if (selectedPageId or selectedDataSourceId) and source_id not in selected_data_source_ids:
            continue
        seen.add(source_id)
        items.append(
            {
                "target_type": "data_source",
                "target_id": detail.get("data_source_id"),
                "name": detail.get("data_source_name") or detail.get("data_source_id"),
                "source_type": _dict_value(detail.get("source_data_source")).get("type"),
                "entities": _list_value(detail.get("entities")),
                "schema_refs": _list_value(detail.get("schema_refs")),
                "relationships": _list_value(detail.get("relationships")),
                "validation_rules": _list_value(detail.get("validation_rules")),
                "seed_strategy": detail.get("seed_strategy"),
                "api_contracts": _list_value(detail.get("api_contracts")),
                "dependent_pages": _list_value(detail.get("dependent_pages")),
                "acceptance_criteria": _list_value(detail.get("acceptance_criteria")),
            }
        )
    return items


def _selected_page_data_source_ids(
    project_plan: dict[str, Any],
    page_detail: dict[str, Any] | None,
) -> set[str]:
    """从页面详情已有字段和 ProjectPlan 契约索引中找出直接数据源 id。"""

    if not isinstance(page_detail, dict):
        return set()
    result = {
        str(item.get("id") or item.get("data_source_id") or "")
        for item in _list_value(page_detail.get("data_sources"))
        if isinstance(item, dict)
        and (item.get("id") or item.get("data_source_id"))
    }
    result.update(
        str(item.get("data_source_id") or "")
        for item in _list_value(page_detail.get("api_dependencies"))
        if isinstance(item, dict) and item.get("data_source_id")
    )
    endpoint_ids = {
        str(item.get("endpoint_id") or "")
        for item in _page_reference_items(
            page_detail,
            "endpoint_dependencies",
            "api_dependencies",
        )
        if isinstance(item, dict) and item.get("endpoint_id")
    }
    result.update(
        str(contract.get("data_source_id") or "")
        for contract in project_plan.get("api_contracts", [])
        if isinstance(contract, dict)
        and contract.get("data_source_id")
        and any(
            isinstance(endpoint, dict)
            and str(endpoint.get("id") or "") in endpoint_ids
            for endpoint in _list_value(contract.get("endpoints"))
        )
    )
    return {item for item in result if item}


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
