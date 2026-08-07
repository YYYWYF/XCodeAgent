from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_contracts import (
    endpoint_dependencies_from_page_api_dependencies,
    normalize_page_api_dependencies,
    normalize_response_bindings,
)
from app.services.frontend_page_tree import update_frontend_page_leaves
from app.services.page_detail_plan import (
    apply_endpoint_datasource_policy,
    normalize_endpoint_data_origin,
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
    "data_origin",
    "endpoint_decision",
    "interface_design",
    "dependent_pages",
}


def detail_review_payload(
    project_plan: dict[str, Any],
    *,
    selectedPageId: str | None = None,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    detail_target_type: str | None = None,
) -> dict[str, Any]:
    """构造本轮细节审核载荷；选中目标时只投射该目标相关详情。"""

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
    missingSelectedPagePlan = bool(selectedPageId and not pages)
    missingSelectedEndpointPlan = bool(detail_target_type == "endpoint" and selected_endpoint_id and not endpoints)
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
            f"请审阅接口 `{selected_endpoint_id}` 详细设计；仅展开需要调整的对象。"
            if detail_target_type == "endpoint" and selected_endpoint_id
            else
            f"请审阅页面 `{selectedPageId}` 详细设计；仅展开需要调整的对象。"
            if selectedPageId
            else "请整体审阅页面与接口初版设计；仅展开需要调整的对象，确认后一次进入任务拆分。"
        ),
        "review": {
            "pages": pages,
            "endpoints": endpoints,
            "summary": {
                "page_count": len(pages),
                "endpoint_count": len(endpoints),
                "api_contract_count": len(project_plan.get("api_contracts", [])),
                "missingSelectedPagePlan": missingSelectedPagePlan,
                "missingSelectedEndpointPlan": missingSelectedEndpointPlan,
                "selectedPageId": selectedPageId,
                "selectedApiContractId": selected_api_contract_id,
                "selectedEndpointId": selected_endpoint_id,
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
        elif target_type == "endpoint":
            _apply_endpoint_target_patch(
                updated,
                updated.get("endpoint_detail_plans", []),
                target_id,
                changes,
            )
        else:
            raise ValueError(f"unsupported detail review target type: {target_type}")

    _assert_endpoint_data_origins_resolved(
        updated,
        selectedPageId=selectedPageId,
        selected_api_contract_id=selected_api_contract_id,
        selected_endpoint_id=selected_endpoint_id,
    )

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
    # 仅提升本轮实际生成并审阅过的对象，避免选中单页时误确认其他页面。
    confirmedPageIds = {
        str(detail.get("pageId"))
        for detail in updated.get("page_detail_plans", [])
        if isinstance(detail, dict)
        and detail.get("pageId")
            and detail.get("status") == "confirmed"
    }
    updated["frontend_pages"] = update_frontend_page_leaves(
        updated.get("frontend_pages"),
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
                "data_source_id": detail.get("data_source_id"),
                "method": detail.get("method"),
                "path": detail.get("path"),
                "summary": detail.get("summary"),
                "data_usage": _dict_value(detail.get("data_usage")),
                "data_origin": normalize_endpoint_data_origin(detail.get("data_origin")),
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
    project_plan: dict[str, Any],
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
        if key == "data_origin":
            target[key] = apply_endpoint_datasource_policy(
                project_plan,
                {"data_source_id": target.get("data_source_id")},
                value,
            )
            continue
        target[key] = _normalize_editable_value(key, value, target.get(key))
    decision = target.get("endpoint_decision")
    if isinstance(decision, dict):
        # 新结构以 EndpointDecision 为唯一语义来源；用户调整来源后立即重建派生字段。
        if "data_origin" in changes:
            decision["data_origin"] = normalize_endpoint_data_origin(
                target.get("data_origin")
            )
        refresh_endpoint_detail_from_decision(target)


def _assert_endpoint_data_origins_resolved(
    project_plan: dict[str, Any],
    *,
    selectedPageId: str | None,
    selected_api_contract_id: str | None,
    selected_endpoint_id: str | None,
) -> None:
    """确认前校验 endpoint 数据来源，避免未决数据库方案绕过用户确认。"""

    errors: list[str] = []
    for detail in project_plan.get("endpoint_detail_plans", []):
        if not isinstance(detail, dict):
            continue
        if selected_endpoint_id and (
            str(detail.get("api_contract_id") or "") != str(selected_api_contract_id or "")
            or str(detail.get("endpoint_id") or "") != str(selected_endpoint_id or "")
        ):
            continue
        data_origin = normalize_endpoint_data_origin(detail.get("data_origin"))
        effective_source = data_origin.get("effective_source")
        source_type = str(data_origin.get("source_type") or "")
        effective_kind = ""
        if isinstance(effective_source, dict):
            effective_kind = str(effective_source.get("kind") or "")
        endpoint_name = str(
            detail.get("endpoint_id") or detail.get("name") or "endpoint"
        )
        if effective_kind == "needs_user_confirmation":
            errors.append(f"{endpoint_name}: data source needs user confirmation")
            continue
        errors.extend(
            f"{endpoint_name}: {message}"
            for message in _endpoint_database_design_errors(
                data_origin, source_type, effective_kind
            )
        )
    if errors:
        raise ValueError(
            "endpoint data_origin still needs user confirmation: "
            + ", ".join(errors)
        )


def _endpoint_database_design_errors(
    data_origin: dict[str, Any],
    source_type: str,
    effective_kind: str,
) -> list[str]:
    """校验结构化字段决策和数据库操作之间的引用与必填信息。"""

    errors: list[str] = []
    operations = [
        item
        for item in data_origin.get("database_operations", [])
        if isinstance(item, dict)
    ]
    operation_index: dict[str, dict[str, Any]] = {}
    referenced_operation_ids: set[str] = set()
    for operation in operations:
        operation_id = str(operation.get("id") or "")
        if not operation_id:
            errors.append("database operation id is required")
            continue
        if operation_id in operation_index:
            errors.append(f"duplicate database operation id {operation_id}")
            continue
        operation_index[operation_id] = operation
        errors.extend(_database_operation_errors(operation))

    for difference in data_origin.get("differences", []):
        if not isinstance(difference, dict):
            continue
        field = str(difference.get("field") or "unknown field")
        kind = str(difference.get("resolution_kind") or "")
        refs = [str(item) for item in difference.get("operation_refs", []) if str(item)]
        adaptation = difference.get("backend_adaptation")
        if kind == "needs_user_confirmation":
            if refs:
                errors.append(f"{field} needs_user_confirmation cannot reference operations")
            errors.append(f"{field} needs user confirmation")
        elif kind == "database_change":
            if not refs:
                errors.append(f"{field} database_change requires operation_refs")
            for ref in refs:
                if ref not in operation_index:
                    errors.append(f"{field} references unknown database operation {ref}")
                else:
                    referenced_operation_ids.add(ref)
            if isinstance(adaptation, dict):
                errors.append(f"{field} database_change cannot declare backend_adaptation")
        elif kind == "backend_adaptation":
            if refs:
                errors.append(f"{field} backend_adaptation cannot reference database operations")
            if not isinstance(adaptation, dict) or not adaptation.get("strategy"):
                errors.append(f"{field} backend_adaptation requires strategy")
        elif kind == "already_supported":
            if refs or isinstance(adaptation, dict):
                errors.append(f"{field} already_supported cannot declare a resolution action")
        else:
            errors.append(f"{field} has invalid resolution_kind {kind}")

    for operation_id in operation_index:
        if operation_id not in referenced_operation_ids:
            errors.append(
                f"database operation {operation_id} is not referenced by a difference"
            )

    if source_type in {"static", "external_api"} and operations:
        errors.append(f"{source_type} data source cannot declare database operations")
    if effective_kind == "mysql_new_table" and not any(
        operation.get("operation") == "create_table" for operation in operations
    ):
        errors.append("mysql_new_table requires create_table operation")
    return errors


def _database_operation_errors(operation: dict[str, Any]) -> list[str]:
    """校验单个数据库操作具备生成确定性 Schema 需求所需的字段。"""

    operation_id = str(operation.get("id") or "database operation")
    operation_kind = str(operation.get("operation") or "")
    supported = {
        "create_table",
        "add_column",
        "alter_column_type",
        "alter_column_nullable",
        "alter_column_default",
    }
    if operation_kind not in supported:
        return [f"{operation_id} has unsupported operation {operation_kind}"]
    if not operation.get("database"):
        return [f"{operation_id} database is required"]
    table = operation.get("table")
    if operation_kind == "create_table":
        if (
            not isinstance(table, dict)
            or not table.get("name")
            or not table.get("columns")
        ):
            return [f"{operation_id} create_table requires table name and columns"]
        for column in table.get("columns", []):
            if not isinstance(column, dict) or not column.get("name") or not column.get("type"):
                return [f"{operation_id} create_table columns require name and type"]
        return []
    if not isinstance(table, str) or not table:
        return [f"{operation_id} table is required"]
    column = operation.get("column")
    if not isinstance(column, str) or not column:
        return [f"{operation_id} column is required"]
    target = operation.get("to")
    if operation_kind == "add_column":
        if not isinstance(target, dict) or not target.get("type"):
            return [f"{operation_id} add_column requires to.type"]
        return []
    required_key = {
        "alter_column_type": "type",
        "alter_column_nullable": "nullable",
        "alter_column_default": "default",
    }[operation_kind]
    if not isinstance(target, dict) or required_key not in target:
        return [f"{operation_id} {operation_kind} requires to.{required_key}"]
    return []


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
    if key in {
        "data_usage",
        "endpoint_decision",
        "interface_design",
    }:
        return value if isinstance(value, dict) else {}
    if key == "data_origin":
        return normalize_endpoint_data_origin(value)
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
