import logging

from langgraph.config import get_stream_writer

from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.main.planner import (
    plan_project_with_chat_model,
    revise_project_plan_with_chat_model,
)
from app.agents.main.page_designer import (
    PageDependencyGapError,
    design_endpoint_with_chat_model,
    design_page_with_chat_model,
)
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.detail_review import (
    apply_detail_review_submission,
    detail_review_payload,
)
from app.services.frontend_page_tree import (
    flatten_frontend_pages,
    update_frontend_page_leaves,
)
from app.services.database_context import prepare_endpoint_database_context
from app.services.project_plan import apply_project_plan_feedback
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.page_detail_plan import (
    attach_endpoint_detail_plan,
    attach_page_detail_plan,
    detail_design_targets,
    extract_endpoint_detail_context,
    extract_page_detail_context,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    edited_project_plan_markdown,
    project_plan_json_path,
    project_plan_markdown_path,
    write_project_plan_document,
    write_project_plan_json,
)


logger = logging.getLogger("uvicorn.error")


def _planning_token_callback(token: str) -> None:
    """将规划模型流式 token 转发到 LangGraph custom stream。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({"type": "llm.token", "token": token, "node": "project_planning"})


def _detail_progress(message: str, **detail: object) -> None:
    """向 LangGraph custom stream 和后端日志同步发送细节设计进度。"""

    logger.info("detail_confirmation progress: %s %s", message, detail)
    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        writer = None
    if writer:
        writer(
            {
                "type": "detail_confirmation.progress",
                "node_name": "detail_confirmation",
                "message": message,
                "detail": detail,
            }
        )


def project_planning(state: ProjectState) -> dict:
    existing_plan = state.get("project_plan")
    if (
        isinstance(existing_plan, dict)
        and existing_plan.get("confirmation_status") == "pending_user_confirmation"
        and not _has_explicit_user_submission(state)
    ):
        return {
            "phase": "project_planning",
            "status": "requires_user_input",
            "project_plan": existing_plan,
            "project_plan_path": state.get("project_plan_path", ""),
            "project_plan_json_path": state.get("project_plan_json_path", ""),
            "clarification": _project_plan_confirmation_payload(existing_plan),
            "timeline": ["project_planning"],
        }
    if state.get("project_plan") and _user_confirmed_project_plan(
        state.get("request", "")
    ):
        edited_markdown = edited_project_plan_markdown(
            state,
            state["project_plan"],
        )
        synchronized_plan = (
            sync_project_plan_from_markdown(
                state["project_plan"],
                state.get("requirement_spec", {}),
                edited_markdown,
            )
            if edited_markdown is not None
            else state["project_plan"]
        )
        project_plan = {
            **apply_project_plan_feedback(
                synchronized_plan,
                state.get("request", ""),
            ),
            "confirmation_status": "confirmed",
        }
        validation_errors = _project_plan_validation_errors(project_plan)
        if validation_errors:
            repaired_plan, remaining_errors = _repair_project_plan_validation_errors(
                project_plan,
                validation_errors,
            )
            repaired_path = write_project_plan_document(state, repaired_plan)
            return {
                "phase": "project_planning",
                "status": "requires_user_input",
                "project_plan": repaired_plan,
                "project_plan_path": repaired_path,
                "project_plan_json_path": _project_plan_json_path_for_state(state),
                "clarification": (
                    _project_plan_dependency_error_payload(remaining_errors)
                    if remaining_errors
                    else _project_plan_confirmation_payload(repaired_plan)
                ),
                "timeline": ["project_planning"],
            }
        markdown_path = project_plan_markdown_path(state)
        if markdown_path.is_file():
            project_plan_path = str(markdown_path)
            write_project_plan_json(state, project_plan)
        else:
            project_plan_path = write_project_plan_document(state, project_plan)
        return {
            "phase": "project_planning",
            "status": "completed",
            "project_plan": project_plan,
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _project_plan_confirmed_payload(project_plan),
            "timeline": ["project_planning"],
        }

    requirement_spec = state["requirement_spec"]
    if state.get("project_plan") and state.get("request"):
        requirement_spec = {
            **requirement_spec,
            "planning_adjustment_request": state["request"],
        }
    project_plan = plan_project_with_chat_model(
        requirement_spec,
        **(
            {"existing_plan": state["project_plan"]}
            if state.get("project_plan")
            else {}
        ),
        on_token=_planning_token_callback,
    )
    project_plan = apply_project_plan_feedback(
        project_plan,
        state.get("request", ""),
    )
    project_plan["confirmation_status"] = "pending_user_confirmation"
    validation_errors = _project_plan_validation_errors(project_plan)
    if validation_errors:
        project_plan, validation_errors = _repair_project_plan_validation_errors(
            project_plan,
            validation_errors,
        )
    project_plan_path = write_project_plan_document(state, project_plan)
    clarification = (
        _project_plan_dependency_error_payload(validation_errors)
        if validation_errors
        else _project_plan_confirmation_payload(project_plan)
    )

    return {
        "phase": "project_planning",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "clarification": clarification,
        "timeline": ["project_planning"],
    }


def detail_confirmation(state: ProjectState) -> dict:
    """基于完整 ProjectPlan 和初始页面功能概览生成批量细节确认。"""

    pending_plan = state.get("pending_project_plan")
    submission = state.get("detail_review_submission")
    selectedPageId = str(state.get("selectedPageId") or "")
    selected_api_contract_id = str(state.get("selected_api_contract_id") or "")
    selected_endpoint_id = str(state.get("selected_endpoint_id") or "")
    detail_target_type = str(
        state.get("detail_target_type")
        or ("endpoint" if selected_endpoint_id else "page" if selectedPageId else "")
    )
    selected_endpoint_state = {
        **({"selected_api_contract_id": selected_api_contract_id} if selected_api_contract_id else {}),
        **({"selected_endpoint_id": selected_endpoint_id} if selected_endpoint_id else {}),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
    }
    if pending_plan and isinstance(submission, dict):
        edited_markdown = edited_project_plan_markdown(state, pending_plan)
        synchronized_plan = (
            sync_project_plan_from_markdown(
                pending_plan,
                state.get("requirement_spec", {}),
                edited_markdown,
            )
            if edited_markdown is not None and state.get("requirement_spec")
            else pending_plan
        )
        confirmed_plan = apply_detail_review_submission(
            synchronized_plan,
            submission,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
        )
        project_plan_path = write_project_plan_document(state, confirmed_plan)
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": confirmed_plan,
            "pending_project_plan": {},
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _project_plan_confirmed_payload(confirmed_plan),
            "detail_selection": {
                "status": "completed",
                "mode": "batch_review",
                "targets": [],
            },
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "detail_plans": [
                *confirmed_plan.get("page_detail_plans", []),
                *confirmed_plan.get("endpoint_detail_plans", []),
            ],
            "detail_review_submission": {},
            "timeline": ["detail_confirmation"],
        }

    if pending_plan and _user_confirmed_project_plan(state.get("request", "")):
        legacy_submission = {
            "review_status": "confirmed",
            "target_changes": [],
            "overall_note": "legacy text confirmation",
        }
        return detail_confirmation(
            {**state, "detail_review_submission": legacy_submission}
        )

    if pending_plan and (selectedPageId or selected_endpoint_id):
        review_plan = pending_plan
        project_plan_path = state.get("project_plan_path")
        if selectedPageId and not _has_selected_page_detail(review_plan, selectedPageId):
            # 旧会话可能保留上一页面的待确认计划；新选择的页面缺失时必须基于最新正式计划补生成。
            source_plan = state.get("project_plan")
            if not isinstance(source_plan, dict):
                source_plan = pending_plan
            try:
                review_plan = _generate_all_detail_plans(
                    source_plan,
                    frontend_pages=state.get("frontend_pages"),
                    selectedPageId=selectedPageId,
                )
            except PageDependencyGapError as exc:
                return {
                    "phase": "detail_confirmation",
                    "status": "requires_user_input",
                    "project_plan": source_plan,
                    "clarification": _project_plan_revision_required_payload(str(exc)),
                    "selectedPageId": selectedPageId,
                    "timeline": ["detail_confirmation"],
                }
            review_plan["confirmation_status"] = "pending_user_confirmation"
            project_plan_path = write_project_plan_document(state, review_plan)
        if selected_endpoint_id and not _has_selected_endpoint_detail(
            review_plan,
            selected_api_contract_id,
            selected_endpoint_id,
        ):
            source_plan = state.get("project_plan")
            if not isinstance(source_plan, dict):
                source_plan = pending_plan
            review_plan = _generate_all_detail_plans(
                source_plan,
                selected_api_contract_id=selected_api_contract_id,
                selected_endpoint_id=selected_endpoint_id,
                detail_target_type=detail_target_type or "endpoint",
            )
            review_plan["confirmation_status"] = "pending_user_confirmation"
            project_plan_path = write_project_plan_document(state, review_plan)
        clarification = detail_review_payload(
            review_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            detail_target_type=detail_target_type or None,
        )
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": clarification,
            "pending_project_plan": review_plan,
            "project_plan": state.get("project_plan"),
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "batch_review",
                "selectedPageId": selectedPageId or None,
                **selected_endpoint_state,
                "targets": _selected_detail_design_targets(
                    review_plan,
                    selectedPageId,
                    selected_api_contract_id=selected_api_contract_id or None,
                    selected_endpoint_id=selected_endpoint_id or None,
                ),
            },
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "detail_plans": _selected_detail_plans(
                review_plan,
                selectedPageId,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id or None,
            ),
            "timeline": ["detail_confirmation"],
        }

    if pending_plan:
        revised_plan = revise_project_plan_with_chat_model(
            pending_plan,
            state.get("request", ""),
            on_token=_planning_token_callback,
        )
        revised_plan = _generate_all_detail_plans(revised_plan)
        revised_plan["confirmation_status"] = "pending_user_confirmation"
        project_plan_path = write_project_plan_document(state, revised_plan)
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(revised_plan),
            "pending_project_plan": revised_plan,
            "project_plan": state.get("project_plan"),
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "batch_review",
                "targets": detail_design_targets(revised_plan),
            },
            "timeline": ["detail_confirmation"],
        }

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError(
            "主 Workflow 需要工作区 .xcodeagent/plans/project-plan.json "
            "（兼容 plans/project-plan.json）作为初始输入。"
        )
    if not selectedPageId and not selected_endpoint_id:
        raise ValueError(
            "开始详细设计时必须提供 selectedPageId 或 selectedEndpointId。"
        )
    if (
        selectedPageId
        and _has_selected_page_detail(project_plan, selectedPageId)
        and _page_endpoint_details_complete(project_plan, selectedPageId)
    ):
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(
                project_plan,
                selectedPageId=selectedPageId,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id or None,
                detail_target_type=detail_target_type or None,
            ),
            "pending_project_plan": project_plan,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "batch_review",
                "selectedPageId": selectedPageId,
                **selected_endpoint_state,
                "targets": _selected_detail_design_targets(
                    project_plan,
                    selectedPageId,
                ),
            },
            "selectedPageId": selectedPageId,
            **selected_endpoint_state,
            "detail_plans": _selected_detail_plans(project_plan, selectedPageId),
            "timeline": ["detail_confirmation"],
        }
    if selected_endpoint_id and _has_selected_endpoint_detail(
        project_plan,
        selected_api_contract_id,
        selected_endpoint_id,
    ):
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(
                project_plan,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id,
                detail_target_type=detail_target_type or "endpoint",
            ),
            "pending_project_plan": project_plan,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "endpoint_review",
                **selected_endpoint_state,
                "targets": _selected_detail_design_targets(
                    project_plan,
                    "",
                    selected_api_contract_id=selected_api_contract_id or None,
                    selected_endpoint_id=selected_endpoint_id,
                ),
            },
            **selected_endpoint_state,
            "detail_plans": _selected_detail_plans(
                project_plan,
                "",
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id,
            ),
            "timeline": ["detail_confirmation"],
        }
    try:
        pending_plan = _generate_all_detail_plans(
            project_plan,
            frontend_pages=state.get("frontend_pages"),
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            detail_target_type=detail_target_type or None,
        )
    except PageDependencyGapError as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(str(exc)),
            "timeline": ["detail_confirmation"],
        }
    pending_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, pending_plan)
    targets = _selected_detail_design_targets(
        pending_plan,
        selectedPageId,
        selected_api_contract_id=selected_api_contract_id or None,
        selected_endpoint_id=selected_endpoint_id or None,
    )
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            pending_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            detail_target_type=detail_target_type or None,
        ),
        "pending_project_plan": pending_plan,
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "batch_review",
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "targets": targets,
        },
        "selectedPageId": selectedPageId or None,
        **selected_endpoint_state,
        "detail_plans": _selected_detail_plans(
            pending_plan,
            selectedPageId,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
        ),
        "timeline": ["detail_confirmation"],
    }


def _generate_all_detail_plans(
    project_plan: dict,
    *,
    frontend_pages: list[dict] | None = None,
    selectedPageId: str | None = None,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    detail_target_type: str | None = None,
) -> dict:
    """为用户选中的页面或 endpoint 生成功能详细设计。"""

    project_pages = project_plan.get("frontend_pages", [])
    normalized_project_page_leaves = [
        _normalize_detail_page(page)
        for page in flatten_frontend_pages(project_pages)
        if isinstance(page, dict)
    ]
    normalized_project_pages = update_frontend_page_leaves(
        project_pages,
        {
            str(page.get("pageId") or page.get("id") or "").strip(): page
            for page in normalized_project_page_leaves
            if str(page.get("pageId") or page.get("id") or "").strip()
        },
    )
    updated_plan = (
        project_plan
        if normalized_project_pages == project_pages
        else {**project_plan, "frontend_pages": normalized_project_pages}
    )
    source_pages = (
        frontend_pages
        if isinstance(frontend_pages, list)
        else normalized_project_page_leaves
    )
    pages = [
        _normalize_detail_page(page)
        for page in source_pages
        if isinstance(page, dict)
    ]
    if selectedPageId:
        pages = [
            page
            for page in pages
            if page.get("pageId") == selectedPageId
        ]
        if not pages:
            raise ValueError(f"项目计划中不存在页面：{selectedPageId}")
        # 单页设计只清理页面正文；已有 EndpointDetail 仍需用于判断缺口和生成页面摘要。
        updated_plan = {
            **updated_plan,
            "page_detail_plans": [],
        }
        _drop_legacy_detail_fields(updated_plan)
    if detail_target_type == "endpoint" or selected_endpoint_id:
        if not selected_api_contract_id or not selected_endpoint_id:
            raise ValueError("接口详细设计必须提供 selectedApiContractId 和 selectedEndpointId。")
        updated_plan, detail = _generate_endpoint_detail_plan(
            updated_plan,
            selected_api_contract_id,
            selected_endpoint_id,
        )
        updated_plan = {
            **updated_plan,
            "page_detail_plans": [],
            "endpoint_detail_plans": [detail],
        }
        _drop_legacy_detail_fields(updated_plan)
        updated_plan["detail_confirmation_summary"] = {
            "confirmed_pages": 0,
            "confirmed_endpoints": 0,
            "total_pages": 0,
            "total_endpoints": 1,
            "mode": "endpoint_review",
        }
        return updated_plan

    endpoint_review_details: list[dict] = []
    endpoint_review_keys: set[tuple[str, str]] = set()
    for page in pages:
        pageId = page.get("pageId") if isinstance(page, dict) else None
        if not pageId:
            continue
        references = extract_page_detail_context(updated_plan, pageId).get("references", {})
        for dependency in references.get("endpoint_dependencies", []):
            if not isinstance(dependency, dict):
                continue
            api_contract_id, endpoint_id = _resolve_endpoint_dependency(
                updated_plan,
                dependency,
            )
            detail_key = (api_contract_id, endpoint_id)
            if detail_key in endpoint_review_keys:
                continue
            existing_detail = _find_formal_endpoint_detail(
                updated_plan,
                api_contract_id,
                endpoint_id,
            )
            if existing_detail is None:
                updated_plan, existing_detail = _generate_endpoint_detail_plan(
                    updated_plan,
                    api_contract_id,
                    endpoint_id,
                )
            if str(existing_detail.get("status") or "") != "confirmed":
                endpoint_review_details.append(existing_detail)
                endpoint_review_keys.add(detail_key)

    for page in pages:
        pageId = page.get("pageId") if isinstance(page, dict) else None
        if not pageId:
            continue
        page_context = extract_page_detail_context(updated_plan, pageId)
        detail = design_page_with_chat_model(updated_plan, page_context)
        detail["status"] = "pending_user_confirmation"
        detail["approved"] = False
        updated_plan = attach_page_detail_plan(updated_plan, detail)

    # 页面审核只携带本轮需要共同确认的 EndpointDetail；已确认详情仍通过独立文件引用复用。
    updated_plan["endpoint_detail_plans"] = endpoint_review_details

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": 0,
        "confirmed_endpoints": 0,
        "total_pages": len(pages),
        "total_endpoints": len(endpoint_review_details),
        "mode": "batch_review",
    }
    selectedPageIds = {
        str(page.get("pageId")) for page in pages if isinstance(page, dict) and page.get("pageId")
    }
    updated_plan["frontend_pages"] = update_frontend_page_leaves(
        updated_plan.get("frontend_pages"),
        {
            page_id: {"detail_status": "pending_user_confirmation"}
            for page_id in selectedPageIds
        },
    )
    return updated_plan


def _resolve_endpoint_dependency(
    project_plan: dict,
    dependency: dict,
) -> tuple[str, str]:
    """把页面 endpoint 引用解析为唯一的契约与接口标识。"""

    endpoint_id = str(dependency.get("endpoint_id") or "").strip()
    requested_contract_id = str(dependency.get("api_contract_id") or "").strip()
    matches: list[str] = []
    for contract in project_plan.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "")
        if requested_contract_id and contract_id != requested_contract_id:
            continue
        if any(
            isinstance(endpoint, dict) and str(endpoint.get("id") or "") == endpoint_id
            for endpoint in contract.get("endpoints", []) or []
        ):
            matches.append(contract_id)
    if len(matches) != 1:
        raise ValueError(f"页面依赖无法唯一定位接口：{requested_contract_id}:{endpoint_id}")
    return matches[0], endpoint_id


def _find_formal_endpoint_detail(
    project_plan: dict,
    api_contract_id: str,
    endpoint_id: str,
) -> dict | None:
    """查找已存在且内容完整的 EndpointDetail，避免页面设计重复生成。"""

    return next(
        (
            detail
            for detail in project_plan.get("endpoint_detail_plans", [])
            if isinstance(detail, dict)
            and str(detail.get("api_contract_id") or "") == api_contract_id
            and str(detail.get("endpoint_id") or "") == endpoint_id
            and _has_formal_endpoint_detail_content(detail)
        ),
        None,
    )


def _generate_endpoint_detail_plan(
    project_plan: dict,
    api_contract_id: str,
    endpoint_id: str,
) -> tuple[dict, dict]:
    """复用独立 endpoint 设计链路生成详情并挂回 ProjectPlan 内存态。"""

    _detail_progress(
        "开始生成接口详细设计。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
    )
    endpoint_context = extract_endpoint_detail_context(
        project_plan,
        api_contract_id,
        endpoint_id,
    )
    _detail_progress(
        "正在确认接口数据来源。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        data_source_id=endpoint_context.get("data_source_id"),
    )
    database_context = prepare_endpoint_database_context(project_plan, endpoint_context)
    endpoint_context = {**endpoint_context, "database_context": database_context}
    _detail_progress(
        database_context.get("message") or "数据库上下文准备完成。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        database_context_status=database_context.get("status"),
        reason=database_context.get("reason"),
        enabled=database_context.get("enabled"),
    )
    _detail_progress(
        "已定位接口契约，正在调用模型生成接口决策。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        method=endpoint_context.get("method"),
        path=endpoint_context.get("path"),
    )
    detail = design_endpoint_with_chat_model(project_plan, endpoint_context, "")
    _detail_progress(
        (
            "接口决策仍需用户确认，已暂停处理逻辑与验收标准组装。"
            if detail.get("design_stage") == "needs_user_confirmation"
            else "接口决策已闭合，完整接口详情已确定性组装。"
        ),
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        design_source=detail.get("design_source"),
    )
    detail["status"] = "pending_user_confirmation"
    detail["approved"] = False
    updated_plan = attach_endpoint_detail_plan(project_plan, detail)
    _detail_progress(
        "接口详细设计已挂回 ProjectPlan，等待用户确认。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        detail_plan_id=detail.get("id"),
    )
    return updated_plan, detail


def _normalize_detail_page(page: dict) -> dict:
    """把正式计划中的 id 兼容映射为细节设计内部使用的 pageId。"""

    pageId = str(page.get("pageId") or page.get("id") or "").strip()
    if not pageId or page.get("pageId") == pageId:
        return page
    return {**page, "pageId": pageId}


def _drop_legacy_detail_fields(project_plan: dict) -> None:
    """清理旧版数据源详细设计字段，避免新 endpoint 流程继续透传。"""

    project_plan.pop("data_source_detail_plans", None)
    project_plan.pop("data_source_detail_confirmation_summary", None)


def _selected_detail_plans(
    project_plan: dict,
    selectedPageId: str,
    *,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
) -> list[dict]:
    """只返回当前页面或当前 endpoint 的详细设计。"""

    if selected_endpoint_id:
        return [
            detail
            for detail in project_plan.get("endpoint_detail_plans", [])
            if isinstance(detail, dict)
            and str(detail.get("api_contract_id") or "") == str(selected_api_contract_id or "")
            and str(detail.get("endpoint_id") or "") == selected_endpoint_id
        ]

    selected_page_details = [
        detail
        for detail in project_plan.get("page_detail_plans", [])
        if isinstance(detail, dict)
        and str(detail.get("pageId") or "") == selectedPageId
    ]
    return selected_page_details


def _has_selected_page_detail(project_plan: dict, selectedPageId: str) -> bool:
    """判断计划中是否已经包含当前页面的详情正文。"""

    return any(
        isinstance(detail, dict)
        and str(detail.get("pageId") or "") == selectedPageId
        for detail in project_plan.get("page_detail_plans", [])
    )


def _page_endpoint_details_complete(project_plan: dict, selectedPageId: str) -> bool:
    """判断页面声明的全部 endpoint 是否已有可独立复用的正式详情。"""

    references = extract_page_detail_context(project_plan, selectedPageId).get("references", {})
    for dependency in references.get("endpoint_dependencies", []):
        if not isinstance(dependency, dict):
            return False
        api_contract_id, endpoint_id = _resolve_endpoint_dependency(project_plan, dependency)
        if _find_formal_endpoint_detail(project_plan, api_contract_id, endpoint_id) is None:
            return False
    return True


def _has_selected_endpoint_detail(
    project_plan: dict,
    selected_api_contract_id: str,
    selected_endpoint_id: str,
) -> bool:
    """判断计划中是否已经包含当前 endpoint 的详情正文。"""

    return any(
        isinstance(detail, dict)
        and str(detail.get("api_contract_id") or "") == selected_api_contract_id
        and str(detail.get("endpoint_id") or "") == selected_endpoint_id
        and _has_formal_endpoint_detail_content(detail)
        for detail in project_plan.get("endpoint_detail_plans", [])
    )


def _has_formal_endpoint_detail_content(detail: dict) -> bool:
    """判断 endpoint 详情是否包含可供用户确认的正式三段设计内容。"""

    return all(
        isinstance(detail.get(field), dict) and bool(detail.get(field))
        for field in ("data_usage", "data_origin", "interface_design")
    )


def _selected_detail_design_targets(
    project_plan: dict,
    selectedPageId: str,
    *,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
) -> list[dict]:
    """把全量目标清单收敛到当前页面或当前 endpoint。"""

    selected_plans = _selected_detail_plans(
        project_plan,
        selectedPageId,
        selected_api_contract_id=selected_api_contract_id,
        selected_endpoint_id=selected_endpoint_id,
    )
    if selected_endpoint_id:
        return [
            {
                "id": f"{selected_api_contract_id}:{selected_endpoint_id}",
                "type": "endpoint",
                "label": f"接口：{plan.get('method')} {plan.get('path')}",
                "name": plan.get("name") or selected_endpoint_id,
                "description": plan.get("summary") or "",
            }
            for plan in selected_plans
        ]
    selected_ids = {
        str(plan.get("pageId") or "")
        for plan in selected_plans
        if isinstance(plan, dict) and plan.get("pageId")
    }
    return [
        target
        for target in detail_design_targets(project_plan)
        if str(target.get("id") or "") in selected_ids
    ]


def _project_plan_json_path_for_state(state: ProjectState) -> str:
    return str(state.get("project_plan_json_path") or project_plan_json_path(state))


def _project_plan_confirmation_payload(project_plan: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划确认",
                question=(
                    "请确认已生成的项目规划书是否正确。"
                    "如果正确，请回复“正确，继续”；"
                    "如果需要调整，请直接写出要修改的架构、API、页面、数据源、权限或验收标准。"
                ),
                type="text",
                placeholder="例如：正确，继续 / 需要增加库存盘点页面和盘点记录数据源。",
            )
        ]
    )
    payload["mode"] = "project_plan_confirmation"
    payload["message"] = "请确认项目计划后再继续页面/接口细节设计。"
    payload["plan_summary"] = project_plan.get("app", {}).get("name", "未命名应用")
    return payload


def _project_plan_dependency_error_payload(errors: list[str]) -> dict:
    """要求用户修订 ProjectPlan 中无法自动修复的依赖或 API 契约缺口。"""

    error_summary = _project_plan_dependency_error_summary(errors)
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划一致性校验",
                question=(
                    "系统已自动尝试修复项目计划中的页面依赖和 API 契约，但仍有无法安全推断的问题。"
                    f"{error_summary}"
                    "请补充业务决策后，我会重新生成项目计划；无需手动编辑 JSON。"
                ),
                type="text",
                placeholder="例如：为入职表单补充 create endpoint，并修正页面路由。",
            )
        ]
    )
    payload["mode"] = "project_plan_dependency_validation_error"
    payload["message"] = "项目计划自动修复后仍未通过一致性校验，页面设计未开始。"
    payload["errors"] = errors
    return payload


def _project_plan_dependency_error_summary(errors: list[str]) -> str:
    """把计划一致性错误压缩成用户可见的简短问题清单。"""

    visible_errors = [
        str(error).strip()
        for error in errors
        if str(error).strip()
    ][:5]
    if not visible_errors:
        return ""
    return "当前剩余问题：" + "；".join(visible_errors) + "。"


def _project_plan_validation_errors(project_plan: dict) -> list[str]:
    """汇总 ProjectPlan 页面依赖和 API 契约闭合性错误。"""

    return [
        *validate_project_plan_dependencies(project_plan),
        *validate_api_contract_consistency(project_plan),
    ]


def _repair_project_plan_validation_errors(
    project_plan: dict,
    errors: list[str],
) -> tuple[dict, list[str]]:
    """把确定性校验错误回灌给规划模型，最多自动修订一次完整计划。"""

    feedback = "系统计划一致性校验失败，请在本次重新生成中完整修复以下问题：\n" + "\n".join(
        f"- {error}" for error in errors
    )
    repaired = revise_project_plan_with_chat_model(
        project_plan, feedback,
        on_token=_planning_token_callback,
    )
    repaired["confirmation_status"] = "pending_user_confirmation"
    return repaired, _project_plan_validation_errors(repaired)


def _project_plan_revision_required_payload(reason: str) -> dict:
    """页面设计发现依赖缺口时阻止自由扩展，并要求修订 ProjectPlan。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="需要修订计划",
                question="页面设计需要尚未声明的 endpoint 或跳转目标，不能自由添加。请返回项目计划修订依赖后重新确认。",
                type="text",
                placeholder="例如：在入职页面的 endpoint_dependencies 中补充员工创建接口。",
            )
        ]
    )
    payload["mode"] = "project_plan_revision_required"
    payload["message"] = "页面设计已停止，必须先修订并重新确认项目计划。"
    payload["reason"] = reason
    return payload


def _project_plan_confirmed_payload(project_plan: dict) -> dict:
    return {
        "mode": "project_plan_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "项目计划已由用户确认，可以继续后续流程。",
        "plan_summary": project_plan.get("app", {}).get("name", "未命名应用"),
    }


def _user_confirmed_project_plan(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续", "可以继续", "无误", "确认"),
        negative_signals=(
            "不正确",
            "需要修改",
            "要修改",
            "请修改",
            "想修改",
            "修改一下",
            "去修改",
            "重新修改",
            "需要调整",
            "要调整",
            "请调整",
            "调整一下",
            "需要补充",
            "要补充",
            "请补充",
            "补充一下",
            "不对",
        ),
    )


def _has_explicit_user_submission(state: ProjectState) -> bool:
    """创建规划只接受本轮确认卡提交，避免恢复文案越过计划门禁。"""

    return (
        state.get("workflow_scope") != "application_planning"
        or state.get("user_interaction_submission") is True
    )
