"""将受支持的 HTTP 和 AG-UI 请求结构归一化为主工作流输入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.acceptance_adjustment import (
    acceptance_adjustment_resume_node,
    normalize_acceptance_adjustment,
)
from app.services.execution_resource_scope import resolve_execution_resource_claims
from app.services.frontend_page_tree import flatten_frontend_pages, frontend_page_ids

from app.workspace.plan_documents import load_project_plan_json
from app.workspace.spec_documents import load_requirement_spec_json, load_ui_designs_json
from app.workspace.task_documents import (
    load_build_task_plan_json,
    load_repair_task_plan_json,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json
from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.workspace_inspector import snapshot_hash


MAX_SELECTED_SKILLS = 64
MAX_SELECTED_SKILL_NAME_CHARS = 128


class InvalidSelectedSkillsError(ValueError):
    """表示 Workflow 请求中的技能名称集合格式无效。"""

    code = "invalid_selected_skills"


class SelectedSkillConflictError(ValueError):
    """表示恢复请求试图替换原 Workflow 的技能集合。"""

    code = "selected_skill_conflict"


def workflow_run_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """应用兼容性回退规则并返回统一的运行时输入。

    显式顶层字段优先于 forwardedProps；只有 `request` 和 `message` 都不存在时，
    才会使用 AG-UI 消息列表中的最后一条用户消息。
    """

    forwarded_props = _optional_dict(payload.get("forwardedProps")) or {}
    application = _optional_dict(forwarded_props.get("application")) or {}
    state = _optional_dict(payload.get("state")) or {}
    clarification_answers = (
        payload.get("clarificationAnswers")
        or forwarded_props.get("clarificationAnswers")
    )
    small_task_handoff_submission = _small_task_handoff_submission(
        clarification_answers
    )
    edited_requirement_spec = (
        _optional_dict(payload.get("editedRequirementSpec"))
        or _optional_dict(payload.get("edited_requirement_spec"))
        or _optional_dict(forwarded_props.get("editedRequirementSpec"))
        or _optional_dict(forwarded_props.get("edited_requirement_spec"))
    )
    requirement_spec_feedback = (
        _optional_text(payload.get("requirementSpecFeedback"))
        or _optional_text(payload.get("requirement_spec_feedback"))
        or _optional_text(forwarded_props.get("requirementSpecFeedback"))
        or _optional_text(forwarded_props.get("requirement_spec_feedback"))
    )
    request = (
        _optional_text(payload.get("request"))
        or _optional_text(payload.get("message"))
        or _last_user_message(payload.get("messages"))
    )
    request = _merge_clarification_answers(
        request=request,
        original_request=(
            _optional_text(payload.get("originalRequest"))
            or _optional_text(forwarded_props.get("originalRequest"))
        ),
        clarification_answers=clarification_answers,
    )
    user_interaction_submission = bool(
        _clarification_answers_to_text(clarification_answers)
    )
    resume_state = (
        _optional_dict(payload.get("resumeState"))
        or _optional_dict(payload.get("resume_state"))
        or _optional_dict(forwarded_props.get("resumeState"))
        or _optional_dict(forwarded_props.get("resume_state"))
    )
    resume_values_from_state = _resume_values(resume_state)
    debug_state = (
        _optional_dict(payload.get("workflowDebug"))
        or _optional_dict(payload.get("debugState"))
        or _optional_dict(forwarded_props.get("workflowDebug"))
        or _optional_dict(forwarded_props.get("debugState"))
        or {}
    )
    workflow_action = _supported_workflow_action(
        _optional_text(payload.get("workflowAction"))
        or _optional_text(payload.get("workflow_action"))
        or _optional_text(forwarded_props.get("workflowAction"))
        or _optional_text(forwarded_props.get("workflow_action"))
    )
    workflow_scope = (
        _optional_text(payload.get("workflowScope"))
        or _optional_text(forwarded_props.get("workflowScope"))
    )
    # 节点调试选择是用户本轮明确指定的恢复入口，优先于旧快照中的阻断节点。
    explicit_resume_from = (
        _optional_text(debug_state.get("resume_from"))
        or _optional_text(debug_state.get("resumeFrom"))
        or _optional_text(payload.get("resume_from"))
        or _optional_text(payload.get("resumeFrom"))
        or _optional_text(forwarded_props.get("resume_from"))
        or _optional_text(forwarded_props.get("resumeFrom"))
    )
    resume_from = _supported_resume_node(
        explicit_resume_from
        or _resume_from_state(resume_state, workflow_scope=workflow_scope),
        workflow_scope=workflow_scope,
    )
    if workflow_action == "retry_failed_tasks":
        if workflow_scope == "application_planning":
            raise ValueError("retry_failed_tasks 只适用于主工作流的 Build 阶段。")
        # 显式重试动作拥有最高恢复优先级，不能被旧快照中的事件或自然语言推断覆盖。
        resume_from = "build"
    elif small_task_handoff_submission and workflow_scope != "application_planning":
        resume_from = "small_task_repair"
    if not resume_from and _clarification_answers_to_text(clarification_answers):
        resume_from = (
            "requirements"
            if workflow_scope == "application_planning"
            else "detail_confirmation"
        )
    if not request and resume_from:
        request = f"从 {resume_from} 节点继续执行 workflow 调试。"
    detail_review_submission = _detail_review_submission(clarification_answers)
    acceptance_decision = _page_acceptance_decision(clarification_answers)
    ui_design_action = _ui_design_action(clarification_answers)
    if acceptance_decision:
        resume_from = "acceptance"
    selectedPageId = (
        _optional_text(payload.get("selectedPageId"))
        or _optional_text(payload.get("selected_page_id"))
        or _optional_text(forwarded_props.get("selectedPageId"))
        or _optional_text(forwarded_props.get("selected_page_id"))
        or _optional_text(resume_values_from_state.get("selectedPageId"))
    )
    selected_api_contract_id = (
        _optional_text(payload.get("selectedApiContractId"))
        or _optional_text(payload.get("selected_api_contract_id"))
        or _optional_text(forwarded_props.get("selectedApiContractId"))
        or _optional_text(forwarded_props.get("selected_api_contract_id"))
        or _optional_text(resume_values_from_state.get("selected_api_contract_id"))
    )
    selected_endpoint_id = (
        _optional_text(payload.get("selectedEndpointId"))
        or _optional_text(payload.get("selected_endpoint_id"))
        or _optional_text(forwarded_props.get("selectedEndpointId"))
        or _optional_text(forwarded_props.get("selected_endpoint_id"))
        or _optional_text(resume_values_from_state.get("selected_endpoint_id"))
    )
    page_template = _optional_dict(
        payload.get("pageTemplate")
        or forwarded_props.get("pageTemplate")
    )
    detail_target_type = _supported_detail_target_type(
        _optional_text(payload.get("detailTargetType"))
        or _optional_text(payload.get("detail_target_type"))
        or _optional_text(forwarded_props.get("detailTargetType"))
        or _optional_text(forwarded_props.get("detail_target_type"))
        or _optional_text(resume_values_from_state.get("detail_target_type"))
    )
    # endpoint 详细设计和页面详细设计互斥；本次明确选择接口时，不允许恢复态里的旧页面 ID 回流。
    if detail_target_type == "endpoint" or selected_endpoint_id:
        selectedPageId = ""
    workspace = (
        _optional_text(payload.get("workspace"))
        or _optional_text(payload.get("workspaceRoot"))
        or _optional_text(forwarded_props.get("workspaceRoot"))
        or _optional_text(application.get("workspaceRoot"))
    )
    if workflow_action == "retry_failed_tasks":
        # 失败运行的公开快照可能只保留摘要；重试必须从工作区落盘计划补回真实修复候选。
        resume_values_from_state = {
            **resume_values_from_state,
            **_persisted_retry_values(
                workspace,
                resume_values_from_state,
            ),
        }
    editor_mode = _supported_editor_mode(
        _optional_text(payload.get("editor_mode"))
        or _optional_text(payload.get("editorMode"))
        or _optional_text(forwarded_props.get("editor_mode"))
        or _optional_text(forwarded_props.get("editorMode"))
    )
    selected_skill_names, selected_skills_error = _workflow_selected_skill_names(
        payload,
        forwarded_props=forwarded_props,
        resume_state=resume_state,
    )
    project_plan_start_values = (
        _project_plan_start_values(workspace, selected_page_id=selectedPageId)
        if workflow_scope != "application_planning"
        else {}
    )
    selectedPageId = _canonical_selected_page_id(
        project_plan_start_values.get("project_plan"),
        selectedPageId,
    )
    build_execution_scope = _build_execution_scope(
        payload,
        forwarded_props=forwarded_props,
        resume_values=resume_values_from_state,
        selected_page_id=selectedPageId,
        selected_api_contract_id=selected_api_contract_id,
        selected_endpoint_id=selected_endpoint_id,
    )
    # endpoint scope 是正式 handoff 的权威目标；即使客户端只发送了 scope，也要补回详情确认所需的显式 ID。
    if build_execution_scope.get("type") == "endpoint":
        selected_api_contract_id = selected_api_contract_id or _optional_text(
            build_execution_scope.get("apiContractId")
        )
        selected_endpoint_id = selected_endpoint_id or _optional_text(
            build_execution_scope.get("targetId")
        )
        detail_target_type = "endpoint"
        selectedPageId = ""
    execution_resource_claims = (
        resolve_execution_resource_claims(
            project_plan_start_values.get("project_plan"),
            build_execution_scope,
        )
        if workflow_scope != "application_planning"
        else []
    )
    resume_execution_run_id = (
        _optional_text(payload.get("resumeExecutionRunId"))
        or _optional_text(payload.get("resume_execution_run_id"))
        or _optional_text(forwarded_props.get("resumeExecutionRunId"))
        or _optional_text(forwarded_props.get("resume_execution_run_id"))
    )
    resume_values = {
        **resume_values_from_state,
        **project_plan_start_values,
        **_debug_resume_values(debug_state, workspace=workspace),
        "retry_failed_tasks": workflow_action == "retry_failed_tasks",
        "selected_skill_names": list(selected_skill_names),
        **(
            {"detail_review_submission": detail_review_submission}
            if detail_review_submission
            else {}
        ),
        **({"ui_design_action": ui_design_action} if ui_design_action else {}),
        **({"acceptance_decision": acceptance_decision} if acceptance_decision else {}),
        **(
            {"acceptance_adjustment": acceptance_adjustment}
            if acceptance_adjustment
            else {}
        ),
        **(
            {"small_task_handoff_submission": small_task_handoff_submission}
            if small_task_handoff_submission
            else {}
        ),
        **({"selectedPageId": selectedPageId} if selectedPageId else {}),
        **({"selected_api_contract_id": selected_api_contract_id} if selected_api_contract_id else {}),
        **({"selected_endpoint_id": selected_endpoint_id} if selected_endpoint_id else {}),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
        **({"page_template": page_template} if page_template else {}),
        "build_execution_scope": build_execution_scope,
        "execution_resource_claims": [
            claim.model_dump(mode="json", by_alias=True)
            for claim in execution_resource_claims
        ],
        **(
            {"resume_execution_run_id": resume_execution_run_id}
            if resume_execution_run_id
            else {}
        ),
        **(
            {"lifecycle_interaction_submission": _lifecycle_interaction_submission(resume_state)}
            if _lifecycle_interaction_submission(resume_state)
            else {}
        ),
        **(
            {"edited_requirement_spec": edited_requirement_spec}
            if edited_requirement_spec and workflow_scope == "application_planning"
            else {}
        ),
        **(
            {"requirement_spec_feedback": requirement_spec_feedback}
            if workflow_scope == "application_planning"
            else {}
        ),
    }
    return {
        "plan_control_run_id": (
            _optional_text(payload.get("planControlRunId"))
            or _optional_text(payload.get("plan_control_run_id"))
            or _optional_text(forwarded_props.get("planControlRunId"))
            or _optional_text(forwarded_props.get("plan_control_run_id"))
        ),
        "plan_control_action": (
            _optional_text(payload.get("planControlAction"))
            or _optional_text(payload.get("plan_control_action"))
            or _optional_text(forwarded_props.get("planControlAction"))
            or _optional_text(forwarded_props.get("plan_control_action"))
        ),
        "cancel_run_id": (
            _optional_text(payload.get("cancelRunId"))
            or _optional_text(payload.get("cancel_run_id"))
            or _optional_text(forwarded_props.get("cancelRunId"))
            or _optional_text(forwarded_props.get("cancel_run_id"))
        ),
        "request": request,
        "workflow_action": workflow_action,
        "user_interaction_submission": user_interaction_submission,
        "resume_from": resume_from,
        "resume_values": resume_values,
        "selected_skill_names": list(selected_skill_names),
        "selected_skills_error": selected_skills_error,
        "project_id": (
            _optional_text(payload.get("project_id"))
            or _optional_text(payload.get("projectId"))
            or _optional_text(state.get("project_id"))
            or _optional_text(state.get("projectId"))
            or _optional_text(application.get("id"))
        ),
        "application_name": (
            _optional_text(application.get("appName"))
            or _optional_text(application.get("name"))
        ),
        "workspace": workspace,
        "editor_mode": editor_mode,
        "workflow_scope": workflow_scope,
        "workflow_debug_enabled": bool(debug_state.get("enabled")),
        "thread_id": (
            _optional_text(payload.get("thread_id"))
            or _optional_text(payload.get("threadId"))
        ),
        "run_id": (
            _optional_text(payload.get("run_id"))
            or _optional_text(payload.get("runId"))
        ),
    }


def _build_execution_scope(
    payload: dict[str, Any],
    *,
    forwarded_props: dict[str, Any],
    resume_values: dict[str, Any],
    selected_page_id: str,
    selected_api_contract_id: str,
    selected_endpoint_id: str,
) -> dict[str, str]:
    """标准化 AG-UI 构建范围，并为页面或 endpoint 详情入口推导局部 scope。"""

    explicit_scope = (
        _optional_dict(payload.get("buildExecutionScope"))
        or _optional_dict(payload.get("build_execution_scope"))
        or _optional_dict(forwarded_props.get("buildExecutionScope"))
        or _optional_dict(forwarded_props.get("build_execution_scope"))
    )
    if selected_endpoint_id and not explicit_scope:
        if not selected_api_contract_id:
            raise ValueError("endpoint 构建必须提供 selectedApiContractId。")
        return {
            "type": "endpoint",
            "targetId": selected_endpoint_id,
            "apiContractId": selected_api_contract_id,
        }
    if selected_page_id and not explicit_scope:
        return {"type": "page", "targetId": selected_page_id}
    raw_scope = explicit_scope or _optional_dict(
        resume_values.get("build_execution_scope")
    )
    if not raw_scope:
        return (
            {"type": "page", "targetId": selected_page_id}
            if selected_page_id
            else {"type": "application", "targetId": "application"}
        )
    target_type = _optional_text(raw_scope.get("type"))
    target_id = _optional_text(raw_scope.get("targetId") or raw_scope.get("target_id"))
    api_contract_id = _optional_text(
        raw_scope.get("apiContractId") or raw_scope.get("api_contract_id")
    )
    if target_type not in {"application", "page", "data_source", "endpoint"}:
        raise ValueError("buildExecutionScope.type 必须是 application、page、data_source 或 endpoint。")
    if target_type == "application":
        if selected_page_id and not explicit_scope:
            return {"type": "page", "targetId": selected_page_id}
        return {"type": "application", "targetId": "application"}
    if not target_id:
        raise ValueError("页面、数据源或 endpoint 构建必须提供 buildExecutionScope.targetId。")
    if target_type == "endpoint":
        api_contract_id = api_contract_id or selected_api_contract_id
        if not api_contract_id:
            raise ValueError("endpoint 构建必须提供 buildExecutionScope.apiContractId。")
        return {"type": "endpoint", "targetId": target_id, "apiContractId": api_contract_id}
    return {"type": target_type, "targetId": target_id}


def _lifecycle_interaction_submission(
    resume_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """只从客户端恢复快照提取交互并发令牌，不接受其 lifecycle 阶段。"""

    if not resume_state:
        return None
    for source_name in ("state", "result"):
        source = _optional_dict(resume_state.get(source_name)) or {}
        lifecycle = _optional_dict(source.get("lifecycle")) or {}
        active_executions = _optional_dict(lifecycle.get("activeExecutions")) or {}
        previous_run_id = _optional_text(resume_state.get("runId"))
        execution = (
            _optional_dict(active_executions.get(previous_run_id))
            if previous_run_id
            else None
        ) or {}
        pending = _optional_dict(execution.get("pendingInteraction")) or {}
        interaction_id = _optional_text(pending.get("id"))
        based_on_revision = pending.get("basedOnRevision")
        if interaction_id and isinstance(based_on_revision, int) and based_on_revision >= 1:
            return {
                "id": interaction_id,
                "basedOnRevision": based_on_revision,
                **({"runId": previous_run_id} if execution and previous_run_id else {}),
            }
    return None


def _canonical_selected_page_id(project_plan: Any, selected_page_id: str) -> str:
    """用最新 ProjectPlan 中的正式 pageId 纠正旧会话或旧快照里的页面标识。"""

    selected = selected_page_id.strip()
    if not selected:
        return ""
    page_ids = _project_plan_page_ids(project_plan)
    if not page_ids or selected in page_ids:
        return selected
    selected_alias = _page_id_alias(selected)
    for page_id in page_ids:
        if _page_id_alias(page_id) == selected_alias:
            return page_id
    return page_ids[0] if len(page_ids) == 1 else selected


def _project_plan_page_ids(project_plan: Any) -> list[str]:
    """从 ProjectPlan 页面目录中提取去重后的正式 pageId。"""

    plan = project_plan if isinstance(project_plan, dict) else {}
    return frontend_page_ids(plan.get("frontend_pages"))


def _page_id_alias(value: str) -> str:
    """生成页面标识的宽松别名，用于兼容 page- 前缀和下划线差异。"""

    normalized = value.strip().lower().replace("_", "-")
    return normalized.removeprefix("page-")


def _workflow_selected_skill_names(
    payload: dict[str, Any],
    *,
    forwarded_props: dict[str, Any],
    resume_state: dict[str, Any] | None,
) -> tuple[tuple[str, ...], ValueError | None]:
    """解析显式选择和恢复状态，并把校验错误延迟到 AG-UI 生命周期内。"""

    try:
        explicit_present, explicit_names = _selected_skill_names_from_sources(
            payload,
            forwarded_props,
        )
        resumed_present, resumed_names = _selected_skill_names_from_resume(resume_state)
        if resumed_present:
            if explicit_present and explicit_names != resumed_names:
                raise SelectedSkillConflictError(
                    "恢复 Workflow 时不能更换最初选择的用户技能。"
                )
            return resumed_names, None
        return explicit_names, None
    except (InvalidSelectedSkillsError, SelectedSkillConflictError) as exc:
        return (), exc


def _selected_skill_names_from_sources(
    *sources: dict[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """按来源优先级读取 camelCase 或 snake_case 技能字段。"""

    for source in sources:
        for field_name in ("selectedSkillNames", "selected_skill_names"):
            if field_name in source:
                return True, _normalize_selected_skill_names(source[field_name])
    return False, ()


def _selected_skill_names_from_resume(
    resume_state: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    """从公开 state 或 result 中恢复初始技能集合。"""

    if not resume_state:
        return False, ()
    state = _optional_dict(resume_state.get("state")) or {}
    result = _optional_dict(resume_state.get("result")) or {}
    return _selected_skill_names_from_sources(state, result)


def _normalize_selected_skill_names(value: Any) -> tuple[str, ...]:
    """严格校验并生成稳定、去重的技能名称元组。"""

    if not isinstance(value, list):
        raise InvalidSelectedSkillsError("selectedSkillNames 必须是字符串数组。")
    if len(value) > MAX_SELECTED_SKILLS:
        raise InvalidSelectedSkillsError(
            f"一次最多选择 {MAX_SELECTED_SKILLS} 个用户技能。"
        )
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise InvalidSelectedSkillsError("selectedSkillNames 只能包含字符串。")
        name = item.strip()
        if not name:
            raise InvalidSelectedSkillsError("selectedSkillNames 不能包含空名称。")
        if len(name) > MAX_SELECTED_SKILL_NAME_CHARS:
            raise InvalidSelectedSkillsError("用户技能名称过长。")
        normalized.add(name)
    return tuple(sorted(normalized, key=lambda name: (name.casefold(), name)))


def _last_user_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _message_content_to_text(message.get("content"))
            if text:
                return text

    return ""


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        return "\n".join(part for part in parts if part).strip()

    return str(content).strip() if content is not None else ""


def _optional_text(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else ""


def _supported_workflow_action(value: str) -> str:
    """限制主 Workflow 当前允许的显式控制动作，避免未知动作悄悄改变恢复路由。"""

    return value if value in {"retry_failed_tasks"} else ""


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信列表中筛出字典项，供协议边界安全读取。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _supported_editor_mode(value: str) -> str:
    return value if value in {"frontend", "backend"} else ""


def _supported_detail_target_type(value: str) -> str:
    """校验详细设计目标类型；批次 A 仅开放页面与 endpoint 两种新语义。"""

    return value if value in {"page", "endpoint"} else ""


def _resume_from_state(
    value: dict[str, Any] | None,
    *,
    workflow_scope: str = "",
) -> str:
    """根据当前 Graph 范围从公开状态推断可恢复节点。"""

    if not value:
        return ""

    events = value.get("events")
    if isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if event.get("status") != "requires_user_input":
                continue
            node_name = _optional_text(event.get("nodeName"))
            if node_name:
                return _supported_resume_node(
                    node_name,
                    workflow_scope=workflow_scope,
                )
            node = _optional_dict(event.get("node"))
            node_id = _optional_text(node.get("id")) if node else ""
            if node_id:
                return _supported_resume_node(
                    node_id,
                    workflow_scope=workflow_scope,
                )

    state = _optional_dict(value.get("state")) or {}
    summary = _optional_dict(value.get("summary")) or {}
    for source in (state, summary, value):
        if source.get("status") == "requires_user_input":
            phase = _optional_text(source.get("phase"))
            if phase:
                return _supported_resume_node(
                    phase,
                    workflow_scope=workflow_scope,
                )

    return ""


def _supported_resume_node(node_name: str, *, workflow_scope: str = "") -> str:
    """限制独立规划 Graph 与主 Graph 各自可恢复的节点集合。"""

    supported = (
        {"requirements", "ui_confirmation", "project_planning"}
        if workflow_scope == "application_planning"
        else {
            "detail_confirmation",
            "project_planning",
            "inspect_workspace",
            "inspect_database_context",
            "prepare_build_tasks",
            "build",
            "integration_test",
            "small_task_repair",
            "launch_project",
            "acceptance",
            "finalize_project",
        }
    )
    return node_name if node_name in supported else ""


def _resume_values(value: dict[str, Any] | None) -> dict[str, Any]:
    """从前端 Workflow 快照恢复主流程允许公开往返的紧凑状态。"""

    if not value:
        return {}

    state = _optional_dict(value.get("state")) or {}
    result = _optional_dict(value.get("result")) or {}
    merged = {**state, **result}
    allowed_keys = {
        "project_plan",
        "frontend_pages",
        "pending_project_plan",
        "project_plan_path",
        "project_plan_json_path",
        "detail_selection",
        "selectedPageId",
        "selected_api_contract_id",
        "selected_endpoint_id",
        "detail_target_type",
        "page_spec_draft",
        "data_source_spec_draft",
        "detail_plans",
        "detail_review_submission",
        "workspace_snapshot_summary",
        "workspace_snapshot_path",
        "workspace_snapshot_hash",
        "workspace_revision",
        "requirement_spec",
        "requirement_spec_path",
        "requirement_spec_json_path",
        "build_task_plan",
        "build_task_plan_path",
        "build_execution_scope",
        "build_context",
        "database_planning_context",
        "execution_resource_claims",
        "tasks",
        "build_results",
        "build_summary",
        "repair_task_plan",
        "repair_tasks",
        "repair_iteration",
        "max_repair_iterations",
        "small_task_tasks",
        "small_task_results",
        "small_task_code_change_sets",
        "small_task_handoff",
        "small_task_handoff_submission",
        "small_task_route",
        "small_task_max_concurrency",
        "integration_next_action",
        "clarification",
        "selected_skill_names",
        "workflow_scope",
        "acceptance_adjustment",
        "ui_designs",
    }
    resumed_values = {
        key: merged[key]
        for key in allowed_keys
        if key in merged and merged[key] is not None
    }
    # 前端 StateSnapshot 使用 camelCase；恢复失败任务时必须把修复计划和构建结果
    # 归一化回 Graph State，否则按钮虽然显示，点击后后端会丢失实际恢复候选。
    camel_aliases = {
        "build_task_plan": "buildTaskPlan",
        "build_results": "buildResults",
        "build_summary": "buildSummary",
        "repair_task_plan": "repairTaskPlan",
        "repair_tasks": "repairTasks",
        "integration_next_action": "integrationNextAction",
    }
    for snake_key, camel_key in camel_aliases.items():
        if snake_key not in resumed_values and merged.get(camel_key) is not None:
            resumed_values[snake_key] = merged[camel_key]
    raw_adjustment = resumed_values.get("acceptance_adjustment") or merged.get(
        "acceptanceAdjustment"
    )
    # 公开 Workflow 快照会用空对象表示“尚未提交验收调整”；恢复其他节点时应视为缺省值。
    empty_adjustment = raw_adjustment is None or raw_adjustment == {} or (
        isinstance(raw_adjustment, str) and not raw_adjustment.strip()
    )
    if empty_adjustment:
        resumed_values.pop("acceptance_adjustment", None)
    else:
        resumed_values["acceptance_adjustment"] = normalize_acceptance_adjustment(
            raw_adjustment
        ) or {}
    # 前端快照使用 camelCase；Graph State 只保留 snake_case，避免同一语义双字段流转。
    selected_api_contract_id = _optional_text(
        merged.get("selected_api_contract_id") or merged.get("selectedApiContractId")
    )
    selected_endpoint_id = _optional_text(
        merged.get("selected_endpoint_id") or merged.get("selectedEndpointId")
    )
    detail_target_type = _supported_detail_target_type(
        _optional_text(merged.get("detail_target_type") or merged.get("detailTargetType"))
    )
    if selected_api_contract_id:
        resumed_values["selected_api_contract_id"] = selected_api_contract_id
    if selected_endpoint_id:
        resumed_values["selected_endpoint_id"] = selected_endpoint_id
    if detail_target_type:
        resumed_values["detail_target_type"] = detail_target_type
    return resumed_values


def _project_plan_start_values(
    workspace: str,
    *,
    selected_page_id: str = "",
) -> dict[str, Any]:
    """加载主 Workflow 计划，并按需接入 RequirementSpec 中的待设计页面。"""

    workspace_root = _workspace_root_path(workspace)
    if workspace_root is None:
        return {}
    for relative_path in (
        Path(".xcodeagent/plans/project-plan.json"),
        Path("plans/project-plan.json"),
    ):
        project_plan_path = workspace_root / relative_path
        if not project_plan_path.is_file():
            continue
        project_plan = load_project_plan_json(
            project_plan_path,
            hydrate_detail_designs=True,
        )
        if not isinstance(project_plan, dict):
            raise ValueError("project-plan.json 的根结构必须是 JSON 对象。")
        frontend_pages_tree = project_plan.get("frontend_pages", [])
        normalized_pages = [
            dict(page)
            for page in flatten_frontend_pages(frontend_pages_tree)
            if isinstance(page, dict)
        ]
        selected_page = _selected_requirement_page(
            workspace_root,
            selected_page_id,
        )
        if selected_page and not any(
            str(page.get("pageId") or page.get("id") or "") == selected_page_id
            for page in normalized_pages
        ):
            normalized_pages.append(selected_page)
            existing_page_tree = (
                list(frontend_pages_tree)
                if isinstance(frontend_pages_tree, list)
                else []
            )
            project_plan = {
                **project_plan,
                "frontend_pages": [*existing_page_tree, selected_page],
            }
        return {
            "project_plan": project_plan,
            "frontend_pages": normalized_pages,
            "project_plan_path": _markdown_sibling_path(project_plan_path),
            "project_plan_json_path": str(project_plan_path),
            # 加载 UI确认阶段持久化的设计稿索引（pageId→page_key 映射），
            # 供 build 阶段前端 agent read_file 还原设计稿视觉。缺失时为空 dict 降级。
            "ui_designs": load_ui_designs_json(
                workspace_root / ".xcodeagent" / "specs" / "ui-designs.json"
            ),
        }
    return {}


def _selected_requirement_page(
    workspace_root: Path,
    selected_page_id: str,
) -> dict[str, Any] | None:
    """从 RequirementSpec 读取用户选择的页面，供现有细节设计节点消费。"""

    if not selected_page_id:
        return None
    for relative_path in (
        Path(".xcodeagent/specs/requirement-spec.json"),
        Path("specs/requirement-spec.json"),
    ):
        spec_path = workspace_root / relative_path
        if not spec_path.is_file():
            continue
        requirement_spec = load_requirement_spec_json(spec_path)
        pages = requirement_spec.get("pages", [])
        if not isinstance(pages, list):
            continue
        for page in pages:
            if not isinstance(page, dict):
                continue
            if str(page.get("id") or "") == selected_page_id:
                return dict(page)
    return None


def _debug_resume_values(
    debug_state: dict[str, Any],
    *,
    workspace: str = "",
) -> dict[str, Any]:
    """加载节点调试产物，并为显式集成测试调试初始化独立修复预算。"""

    if not debug_state or debug_state.get("enabled") is False:
        return {}

    values: dict[str, Any] = {}
    debug_resume_from = _optional_text(
        debug_state.get("resume_from") or debug_state.get("resumeFrom")
    )
    if debug_resume_from == "integration_test":
        # 显式节点调试代表新的验证循环，不能继承同一 thread 已耗尽的修复预算。
        values.update(
            {
                "repair_task_plan": {},
                "repair_tasks": [],
                "repair_iteration": 0,
                "max_repair_iterations": 3,
                "integration_next_action": "",
            }
        )
    requirement_path = _resolve_debug_json_path(
        debug_state,
        ("requirement_spec_path", "requirementSpecPath", "requirementSpecDirectory"),
        (
            "requirement-spec.json",
            ".xcodeagent/specs/requirement-spec.json",
            "specs/requirement-spec.json",
        ),
        workspace=workspace,
    )
    if requirement_path:
        values["requirement_spec_path"] = _markdown_sibling_path(requirement_path)
        values["requirement_spec_json_path"] = str(requirement_path)
        values["requirement_spec"] = load_requirement_spec_json(requirement_path)

    project_plan_path = _resolve_debug_json_path(
        debug_state,
        ("project_plan_path", "projectPlanPath", "projectPlanDirectory"),
        (
            "project-plan.json",
            ".xcodeagent/plans/project-plan.json",
            "plans/project-plan.json",
        ),
        workspace=workspace,
    )
    if project_plan_path:
        values["project_plan_path"] = _markdown_sibling_path(project_plan_path)
        values["project_plan_json_path"] = str(project_plan_path)
        values["project_plan"] = load_project_plan_json(
            project_plan_path,
            hydrate_detail_designs=True,
        )

    build_task_plan_path = _resolve_debug_json_path(
        debug_state,
        ("build_task_plan_path", "buildTaskPlanPath", "buildTaskPlanDirectory"),
        (
            "build-task-plan.json",
            ".xcodeagent/plans/build-task-plan.json",
            "plans/build-task-plan.json",
        ),
        workspace=workspace,
    )
    if build_task_plan_path:
        build_task_plan = load_build_task_plan_json(build_task_plan_path)
        values["build_task_plan_path"] = str(build_task_plan_path)
        values["build_task_plan"] = build_task_plan
        values["tasks"] = tasks_from_build_task_plan(build_task_plan)

    workspace_snapshot_path = _resolve_debug_workspace_snapshot_path(
        debug_state,
        workspace=workspace,
    )
    if workspace_snapshot_path:
        workspace_snapshot = load_workspace_snapshot_json(workspace_snapshot_path)
        values["workspace_snapshot_path"] = str(workspace_snapshot_path)
        values["workspace_snapshot_hash"] = snapshot_hash(workspace_snapshot)
        values["workspace_revision"] = str(
            workspace_snapshot.get("workspace_revision") or ""
        )
        values["workspace_snapshot_summary"] = _workspace_snapshot_summary(
            workspace_snapshot
        )

    return values


def _persisted_retry_values(
    workspace: str,
    resume_values: dict[str, Any],
) -> dict[str, Any]:
    """在重试快照不完整时恢复工作区中的 Build 与 Repair 计划。"""

    workspace_root = Path(workspace).expanduser() if workspace else None
    if workspace_root is None or not workspace_root.is_dir():
        return {}

    values: dict[str, Any] = {}
    build_plan = _load_retry_plan(
        resume_values,
        workspace_root,
        "build_task_plan",
        "build_task_plan_path",
        "build-task-plan.json",
        load_build_task_plan_json,
    )
    if build_plan:
        values["build_task_plan"] = build_plan
        values["build_task_plan_path"] = str(
            _retry_plan_path(
                resume_values.get("build_task_plan_path"),
                workspace_root,
                "build-task-plan.json",
            )
        )
        values["tasks"] = tasks_from_build_task_plan(build_plan)

    repair_plan = _load_retry_plan(
        resume_values,
        workspace_root,
        "repair_task_plan",
        "repair_task_plan_path",
        "repair-task-plan.json",
        load_repair_task_plan_json,
    )
    if repair_plan:
        values["repair_task_plan"] = repair_plan
        values["repair_task_plan_path"] = str(
            _retry_plan_path(
                resume_values.get("repair_task_plan_path"),
                workspace_root,
                "repair-task-plan.json",
            )
        )
        values["repair_tasks"] = list(repair_plan.get("tasks") or [])
    return values


def _load_retry_plan(
    resume_values: dict[str, Any],
    workspace_root: Path,
    plan_key: str,
    path_key: str,
    default_name: str,
    loader: Any,
) -> dict[str, Any]:
    """只在恢复态没有有效计划时读取对应的工作区 JSON 计划。"""

    existing_plan = resume_values.get(plan_key)
    if isinstance(existing_plan, dict) and existing_plan:
        existing_tasks = existing_plan.get("tasks") or existing_plan.get("repair_tasks")
        # repair 决策没有任务列表时仍是不完整快照，允许用落盘计划补齐；确认/终止计划不能被覆盖。
        if plan_key != "repair_task_plan" or (
            existing_plan.get("decision") != "repair" or existing_tasks
        ):
            return {}
    path = _retry_plan_path(resume_values.get(path_key), workspace_root, default_name)
    if not path.is_file():
        return {}
    try:
        loaded = loader(path)
    except (OSError, TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _retry_plan_path(raw_path: Any, workspace_root: Path, default_name: str) -> Path:
    """把快照中的计划路径解析为当前工作区内的绝对路径。"""

    candidate = (
        Path(str(raw_path)).expanduser()
        if raw_path
        else Path(".xcodeagent") / "plans" / default_name
    )
    return candidate if candidate.is_absolute() else workspace_root / candidate


def _resolve_debug_workspace_snapshot_path(
    debug_state: dict[str, Any],
    *,
    workspace: str = "",
) -> Path | None:
    raw_path = ""
    for field_name in (
        "workspace_snapshot_path",
        "workspaceSnapshotPath",
        "workspaceSnapshotDirectory",
    ):
        raw_path = _optional_text(debug_state.get(field_name))
        if raw_path:
            break
    if not raw_path:
        workspace_root = _workspace_root_path(workspace)
        if not workspace_root:
            return None
        path = workspace_root / ".xcodeagent" / "cache" / "workspace-snapshots"
        if not path.is_dir():
            path = workspace_root / "cache" / "workspace-snapshots"
            if not path.is_dir():
                return None
    else:
        path = Path(raw_path).expanduser()

    if path.is_file() and path.suffix == ".json":
        return path
    if path.is_dir():
        candidates = sorted(
            path.glob("*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None
    return None


def _workspace_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    code_graph = snapshot.get("code_graph") if isinstance(snapshot, dict) else {}
    return {
        "schema_version": snapshot.get("schema_version"),
        "workspace_revision": snapshot.get("workspace_revision"),
        "tech_stack": snapshot.get("tech_stack", []),
        "entrypoints": snapshot.get("entrypoints", []),
        "project_roots": snapshot.get("project_roots", []),
        "file_manifest": snapshot.get("file_manifest", {}),
        "code_graph": {
            "provider": (code_graph or {}).get("provider"),
            "available": bool((code_graph or {}).get("available")),
        },
    }


def _resolve_debug_json_path(
    debug_state: dict[str, Any],
    field_names: tuple[str, ...],
    default_files: tuple[str, ...],
    *,
    workspace: str = "",
) -> Path | None:
    raw_path = ""
    for field_name in field_names:
        raw_path = _optional_text(debug_state.get(field_name))
        if raw_path:
            break
    if not raw_path:
        workspace_root = _workspace_root_path(workspace)
        if not workspace_root:
            return None
        candidates = [workspace_root / default_file for default_file in default_files]
        return next((candidate for candidate in candidates if candidate.exists()), None)

    path = Path(raw_path).expanduser()
    if path.is_file():
        if path.suffix == ".json":
            return path
        json_sibling = path.with_suffix(".json")
        return json_sibling if json_sibling.exists() else None

    candidates = [path / default_file for default_file in default_files]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _workspace_root_path(workspace: str) -> Path | None:
    workspace_text = _optional_text(workspace)
    if not workspace_text:
        return None
    path = Path(workspace_text).expanduser()
    return path if path.is_dir() else None


def _markdown_sibling_path(json_path: Path) -> str:
    markdown_path = json_path.with_suffix(".md")
    return str(markdown_path)


def _merge_clarification_answers(
    *,
    request: str,
    original_request: str,
    clarification_answers: Any,
) -> str:
    answers_text = _clarification_answers_to_text(clarification_answers)
    if not answers_text:
        return request

    base_request = original_request or request
    return "\n".join(
        [
            "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
            "",
            "原始需求：",
            base_request,
            "",
            "用户补充确认：",
            answers_text,
        ]
    ).strip()


def _clarification_answers_to_text(value: Any) -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        for key, answer in value.items():
            answer_text = _answer_to_text(answer)
            if answer_text:
                lines.extend([f"- {key}", f"  回答：{answer_text}"])
        return "\n".join(lines)

    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                question = _optional_text(item.get("question")) or _optional_text(
                    item.get("header")
                )
                answer = _answer_to_text(item.get("answer"))
                if question and answer:
                    lines.append(f"- {question}: {answer}")
            else:
                answer = _answer_to_text(item)
                if answer:
                    lines.append(f"- {answer}")
        return "\n".join(lines)

    return _answer_to_text(value)


def _page_acceptance_decision(value: Any) -> str:
    """从结构化验收答案中提取允许主 Graph 使用的稳定动作。"""

    if not isinstance(value, dict):
        return ""
    decision = _optional_text(value.get("page_acceptance"))
    return decision if decision in {"accepted", "changes_requested"} else ""


def _acceptance_adjustment(value: Any) -> dict[str, str] | None:
    """从结构化验收答案读取调整类型，并在协议边界完成校验。"""

    if not isinstance(value, dict):
        return None
    raw_adjustment = value.get("acceptance_adjustment") or value.get(
        "acceptanceAdjustment"
    )
    if raw_adjustment is None:
        return None
    return normalize_acceptance_adjustment(raw_adjustment)


def _small_task_handoff_submission(value: Any) -> dict[str, str] | None:
    """从小任务确认卡提取结构化批准动作，避免依赖自然语言路由。"""

    if not isinstance(value, dict):
        return None
    answer = value.get("small_task_handoff")
    selected: list[str] = []
    if isinstance(answer, dict):
        raw_selected = answer.get("selected")
        selected = (
            [str(item).strip().casefold() for item in raw_selected]
            if isinstance(raw_selected, list)
            else [str(raw_selected).strip().casefold()]
        )
    elif isinstance(answer, list):
        selected = [str(item).strip().casefold() for item in answer]
    elif answer is not None:
        selected = [str(answer).strip().casefold()]
    selected = [item for item in selected if item]
    if any(
        item in {"是", "yes", "approved", "approve", "同意", "确认", "批准"}
        for item in selected
    ):
        return {"decision": "approved"}
    if any(
        item in {"否", "no", "rejected", "reject", "拒绝", "不同意"}
        for item in selected
    ):
        return {"decision": "rejected"}
    return None


def _answer_to_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        selected = _selected_answer_text(value.get("selected"))
        other = _optional_text(value.get("other"))
        if selected or other:
            parts = []
            if selected:
                parts.append(f"已选：{selected}")
            if other:
                parts.append(f"其他补充：{other}")
            return "；".join(parts)
        return ", ".join(
            f"{key}={answer}" for key, answer in value.items() if str(answer).strip()
        )
    return str(value).strip() if value is not None else ""


def _detail_review_submission(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    submission = value.get("detail_review")
    if not isinstance(submission, dict):
        return None
    if submission.get("review_status") != "confirmed":
        return None
    return submission


def _ui_design_action(value: Any) -> dict[str, Any] | None:
    """从结构化确认答案中提取 UI 确认节点的单页/多页动作。

    单页动作形如 ``{ui_design_action: {pageId, action, templateId?}}``，由前端
    UiDesignConfirmationPanel 在用户逐页"选模板"或"换一换"时即时提交。
    多页调整动作形如 ``{ui_design_action: {action: "adjust_pages", pageIds: [...],
    instruction: "..."}}``，由底部斜杠提及 + 调整按钮提交。
    action 接受 select_template / regenerate / adjust_pages；其余视为无动作。
    """

    if not isinstance(value, dict):
        return None
    action = value.get("ui_design_action")
    if not isinstance(action, dict):
        return None
    action_type = _optional_text(action.get("action"))
    if action_type not in {"select_template", "regenerate", "adjust_pages"}:
        return None

    # 多页调整：pageIds 数组（可为空，空时由大模型按 instruction 自行判断）+
    # instruction 字符串（非空校验）。
    if action_type == "adjust_pages":
        raw_ids = action.get("pageIds")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        page_ids = (
            [str(pid).strip() for pid in raw_ids if str(pid).strip()]
            if isinstance(raw_ids, list)
            else []
        )
        instruction = _optional_text(action.get("instruction"))
        if not instruction:
            return None
        return {"action": "adjust_pages", "pageIds": page_ids, "instruction": instruction}

    # 单页动作：pageId + action，select_template 还需 templateId。
    page_id = _optional_text(action.get("pageId")) or _optional_text(action.get("page_id"))
    if not page_id:
        return None
    result: dict[str, Any] = {"pageId": page_id, "action": action_type}
    template_id = _optional_text(action.get("templateId")) or _optional_text(action.get("template_id"))
    if action_type == "select_template":
        if not template_id:
            return None
        result["templateId"] = template_id
    return result


def _selected_answer_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(
            str(item).strip()
            for item in value
            if str(item).strip() and str(item).strip() != "__other__"
        )
    text = str(value).strip() if value is not None else ""
    return "" if text == "__other__" else text
