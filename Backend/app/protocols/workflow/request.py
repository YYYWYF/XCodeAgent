"""将受支持的 HTTP 和 AG-UI 请求结构归一化为主工作流输入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.acceptance_adjustment import (
    acceptance_adjustment_resume_node,
    normalize_acceptance_adjustment,
)
from app.services.entity_design import normalize_entity_design_action
from app.domain.application_planning_interaction import ApplicationPlanningInteraction
from app.services.execution_resource_scope import resolve_execution_resource_claims
from app.services.frontend_page_tree import project_plan_page_records
from app.services.page_implementation_contract import materialize_technical_plan_runtime
from app.services.project_plan import TECHNICAL_PLAN_ARTIFACT_TYPE

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
APPLICATION_PLANNING_SCOPES = {
    "application_planning",
}

class InvalidSelectedSkillsError(ValueError):
    """表示 Workflow 请求中的技能名称集合格式无效。"""

    code = "invalid_selected_skills"


class SelectedSkillConflictError(ValueError):
    """表示恢复请求试图替换原 Workflow 的技能集合。"""

    code = "selected_skill_conflict"


# 创建规划权限澄清使用稳定问题 ID；这里仅把协议 ID 映射成模型可读的业务标签，
# 不根据用户业务文本做关键词推断。
_CLARIFICATION_QUESTION_LABELS = {
    "authorization_page_business": "受控页面业务含义",
    "authorization_operation_business": "受控操作业务含义",
    "authorization_data_scope_business": "数据范围业务含义",
    "authorization_business_review": "权限业务梳理",
}


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
    build_task_plan_confirmation = _build_task_plan_confirmation(
        clarification_answers
    )
    test_phase_confirmation = _test_phase_confirmation_submission(
        clarification_answers
    )
    review_phase_confirmation = _review_phase_confirmation_submission(
        clarification_answers
    )
    acceptance_phase_confirmation = _acceptance_phase_confirmation_submission(
        clarification_answers
    )
    code_review_repair_confirmation = _code_review_repair_confirmation_submission(
        clarification_answers
    )
    unit_test_decision = _unit_test_decision(clarification_answers)
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
    original_request = (
        _optional_text(payload.get("originalRequest"))
        or _optional_text(forwarded_props.get("originalRequest"))
    )
    request = _merge_clarification_answers(
        request=request,
        original_request=original_request,
        clarification_answers=clarification_answers,
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
    application_planning_interaction = _application_planning_interaction(
        payload,
        forwarded_props=forwarded_props,
        fallback_request=request,
        original_request=original_request,
    )
    if application_planning_interaction and workflow_scope != "application_planning":
        raise ValueError("创建规划交互只能提交到 application_planning Graph。")
    ui_design_action = _ui_design_action(clarification_answers)
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
    # UI 卡片的结构化动作是 ui_confirmation 的直接调用，不属于自由输入设计变更。
    # 即使恢复快照或错误客户端参数带有意图入口痕迹，也必须由该动作覆盖。
    if application_planning_interaction:
        # 原生 interrupt 由同一 thread 的 checkpoint 精确定位挂起点，不再推断恢复节点。
        ui_design_action = None
        resume_from = ""
    elif ui_design_action:
        if workflow_scope == "application_planning":
            raise ValueError(
                "创建规划 UI 动作必须通过 applicationPlanningInteraction 提交。"
            )
    if workflow_action == "retry_failed_tasks":
        if workflow_scope in APPLICATION_PLANNING_SCOPES:
            raise ValueError("retry_failed_tasks 只适用于主工作流的 Build 阶段。")
        # Build 门禁失败通常表示当前范围还没有可执行 DAG；此时必须回到任务生成，
        # 否则会反复读取旧范围的 build-task-plan.json 并形成不可恢复的失败循环。
        resume_from = _retry_failed_execution_node(
            resume_state,
            resume_values_from_state,
        )
    elif small_task_handoff_submission and workflow_scope not in APPLICATION_PLANNING_SCOPES:
        # 单测修复使用独立节点；恢复快照中的 repairReturnNode 是当前契约里
        # 唯一可靠的来源，不能让通用 SmallTask 节点吞掉开发阶段修复计数。
        resume_from = (
            "unit_test_repair"
            if str(resume_values_from_state.get("repair_return_node") or "")
            == "unit_test"
            else "small_task_repair"
        )
    elif build_task_plan_confirmation and workflow_scope != "application_planning":
        # DAG 确认必须回到同一 prepare 节点，不能被通用开发入口回退规则截走。
        resume_from = "prepare_build_tasks"
    elif test_phase_confirmation and workflow_scope != "application_planning":
        # 开发完成确认必须恢复同一确认节点，确认成功后由 Graph 放行测试节点。
        resume_from = "test_phase_confirmation"
    elif review_phase_confirmation and workflow_scope != "application_planning":
        # 审查阶段确认允许从测试 thread 原子转交到新的审查 thread。
        resume_from = "review_phase_confirmation"
    elif acceptance_phase_confirmation and workflow_scope != "application_planning":
        # 验收阶段确认允许从审查 thread 原子转交到新的验收 thread。
        resume_from = "acceptance_phase_confirmation"
    elif code_review_repair_confirmation and workflow_scope != "application_planning":
        # 一键修复在当前审查 thread 内恢复代码审查子图，不创建新的生命周期交接。
        if not _has_code_review_issue_snapshot(resume_values_from_state):
            raise ValueError(
                "code_review_repair_confirmation 缺少有效的代码审查问题快照，不能恢复修复。"
            )
        resume_from = "code_review"
    elif unit_test_decision and workflow_scope != "application_planning":
        # 单元测试确认属于开发阶段门禁；提交后必须回到 unit_test，不能误入集成测试。
        resume_from = "unit_test"
    if (
        resume_from == "review_phase_confirmation"
        and not review_phase_confirmation
        and workflow_scope != "application_planning"
    ):
        raise ValueError(
            "review_phase_confirmation 只能通过 clarificationAnswers 提交 confirm 动作。"
        )
    if (
        resume_from == "acceptance_phase_confirmation"
        and not acceptance_phase_confirmation
        and workflow_scope != "application_planning"
    ):
        raise ValueError(
            "acceptance_phase_confirmation 只能通过 clarificationAnswers 提交 confirm 动作。"
        )
    if (
        resume_from == "code_review"
        and not code_review_repair_confirmation
        and workflow_scope != "application_planning"
        and _clarification_mode(resume_state) == "code_review_repair_confirmation"
    ):
        raise ValueError(
            "code_review_repair_confirmation 只能通过 clarificationAnswers 提交 repair_all 动作。"
        )
    if not resume_from and _clarification_answers_to_text(clarification_answers):
        if workflow_scope in APPLICATION_PLANNING_SCOPES:
            raise ValueError(
                "创建规划确认必须通过 applicationPlanningInteraction 恢复原生中断。"
            )
        resume_from = "development_readiness_gate"
    if not request and resume_from:
        request = f"从 {resume_from} 节点继续执行 workflow 调试。"
    entity_source_binding_submission = _entity_source_binding_submission(clarification_answers)
    entity_design_action = _entity_design_action(clarification_answers)
    if entity_source_binding_submission or entity_design_action:
        resume_from = "entity_source_binding"
    acceptance_decision = _page_acceptance_decision(clarification_answers)
    acceptance_adjustment = _acceptance_adjustment(clarification_answers)
    frontend_performance_decision = _frontend_performance_decision(
        clarification_answers
    )
    if acceptance_adjustment:
        resume_from = acceptance_adjustment_resume_node(acceptance_adjustment)
    elif acceptance_decision:
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
    selected_entity_id = (
        _optional_text(payload.get("selectedEntityId"))
        or _optional_text(payload.get("selected_entity_id"))
        or _optional_text(forwarded_props.get("selectedEntityId"))
        or _optional_text(forwarded_props.get("selected_entity_id"))
        or _optional_text(resume_values_from_state.get("selected_entity_id"))
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
    # 页面与接口开发目标互斥；本次明确选择接口时，不允许恢复态里的旧页面 ID 回流。
    if detail_target_type == "endpoint" or selected_endpoint_id:
        selectedPageId = ""
    # 实体数据源绑定与页面/API开发互斥；本次明确选择实体时，不允许旧开发目标回流。
    if detail_target_type == "entity" or selected_entity_id:
        selectedPageId = ""
        selected_endpoint_id = ""
        selected_api_contract_id = ""
    workspace = (
        _optional_text(payload.get("workspace"))
        or _optional_text(payload.get("workspaceRoot"))
        or _optional_text(forwarded_props.get("workspaceRoot"))
        or _optional_text(application.get("workspaceRoot"))
    )
    if workflow_action == "retry_failed_tasks" and resume_from == "build":
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
        if workflow_scope not in APPLICATION_PLANNING_SCOPES
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
        project_plan=project_plan_start_values.get("project_plan"),
    )
    # endpoint scope 是正式 handoff 的权威目标；即使客户端只发送 scope，也要补回门禁所需的显式 ID。
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
        if workflow_scope not in APPLICATION_PLANNING_SCOPES
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
        "retry_failed_tasks": (
            workflow_action == "retry_failed_tasks" and resume_from == "build"
        ),
        **(
            {"build_task_plan_confirmation": build_task_plan_confirmation}
            if build_task_plan_confirmation
            else {}
        ),
        **(
            {"test_phase_confirmation": test_phase_confirmation}
            if test_phase_confirmation
            else {}
        ),
        **(
            {"review_phase_confirmation": review_phase_confirmation}
            if review_phase_confirmation
            else {}
        ),
        **(
            {"acceptance_phase_confirmation": acceptance_phase_confirmation}
            if acceptance_phase_confirmation
            else {}
        ),
        **(
            {"code_review_repair_confirmation": code_review_repair_confirmation}
            if code_review_repair_confirmation
            else {}
        ),
        "selected_skill_names": list(selected_skill_names),
        **(
            {"entity_source_binding_submission": entity_source_binding_submission}
            if entity_source_binding_submission
            else {}
        ),
        **({"entity_design_action": entity_design_action} if entity_design_action else {}),
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
        **(
            {"unit_test_decision": unit_test_decision}
            if unit_test_decision
            else {}
        ),
        **(
            {"frontend_performance_decision": frontend_performance_decision}
            if frontend_performance_decision
            else {}
        ),
        **({"selectedPageId": selectedPageId} if selectedPageId else {}),
        **({"selected_api_contract_id": selected_api_contract_id} if selected_api_contract_id else {}),
        **({"selected_endpoint_id": selected_endpoint_id} if selected_endpoint_id else {}),
        **({"selected_entity_id": selected_entity_id} if selected_entity_id else {}),
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
            if edited_requirement_spec and workflow_scope in APPLICATION_PLANNING_SCOPES
            else {}
        ),
        **(
            {"requirement_spec_feedback": requirement_spec_feedback}
            if workflow_scope in APPLICATION_PLANNING_SCOPES
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
        "application_planning_interaction": application_planning_interaction,
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
    project_plan: dict[str, Any] | None = None,
) -> dict[str, str]:
    """标准化 AG-UI 构建范围，并为页面或 endpoint 详情入口推导局部 scope。"""

    explicit_scope = (
        _optional_dict(payload.get("buildExecutionScope"))
        or _optional_dict(payload.get("build_execution_scope"))
        or _optional_dict(forwarded_props.get("buildExecutionScope"))
        or _optional_dict(forwarded_props.get("build_execution_scope"))
    )
    if selected_endpoint_id and not explicit_scope:
        inferred_api_contract_id = (
            selected_api_contract_id
            or _infer_endpoint_api_contract_id(project_plan, selected_endpoint_id)
        )
        if not inferred_api_contract_id:
            raise ValueError(
                "endpoint 构建必须提供 selectedApiContractId，且无法从 ProjectPlan 唯一推导。"
            )
        return {
            "type": "endpoint",
            "targetId": selected_endpoint_id,
            "apiContractId": inferred_api_contract_id,
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
        api_contract_id = (
            api_contract_id
            or selected_api_contract_id
            or _infer_endpoint_api_contract_id(project_plan, target_id)
        )
        if not api_contract_id:
            raise ValueError("endpoint 构建必须提供 buildExecutionScope.apiContractId。")
        return {"type": "endpoint", "targetId": target_id, "apiContractId": api_contract_id}
    return {"type": target_type, "targetId": target_id}


def _infer_endpoint_api_contract_id(
    project_plan: dict[str, Any] | None,
    endpoint_id: str,
) -> str:
    """从当前确认的 ProjectPlan 唯一反查 endpoint 所属 API Contract。"""

    if not isinstance(project_plan, dict) or not endpoint_id:
        return ""
    matches: list[str] = []
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_id = _optional_text(contract.get("id"))
        if not contract_id:
            continue
        for endpoint in _dict_items(contract.get("endpoints")):
            if _optional_text(endpoint.get("id")) != endpoint_id:
                continue
            if contract_id not in matches:
                matches.append(contract_id)
    return matches[0] if len(matches) == 1 else ""


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
    return list(
        dict.fromkeys(
            str(page.get("pageId") or page.get("id") or "").strip()
            for page in project_plan_page_records(plan)
            if str(page.get("pageId") or page.get("id") or "").strip()
        )
    )


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
    """校验详细设计目标类型；页面、endpoint 与实体三种目标均可选。"""

    return value if value in {"page", "endpoint", "entity"} else ""


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


def _retry_failed_execution_node(
    resume_state: dict[str, Any] | None,
    resume_values: dict[str, Any],
) -> str:
    """让 Build 门禁或 DAG 生成失败回到任务生成，其余失败沿用 Build 重试。"""

    current_scope = resume_values.get("build_execution_scope")
    build_task_plan = resume_values.get("build_task_plan")
    planned_scope = (
        build_task_plan.get("build_execution_scope")
        if isinstance(build_task_plan, dict)
        else None
    )
    if (
        isinstance(current_scope, dict)
        and current_scope
        and isinstance(planned_scope, dict)
        and planned_scope
        and planned_scope != current_scope
    ):
        return "prepare_build_tasks"

    build_summary = resume_values.get("build_summary")
    gate_errors = (
        build_summary.get("gate_errors")
        if isinstance(build_summary, dict)
        else []
    )
    if isinstance(gate_errors, list) and any(str(error).strip() for error in gate_errors):
        return "prepare_build_tasks"

    events = resume_state.get("events") if isinstance(resume_state, dict) else None
    if isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict) or event.get("status") != "failed":
                continue
            node_name = _optional_text(event.get("nodeName"))
            node = _optional_dict(event.get("node"))
            node_name = node_name or (_optional_text(node.get("id")) if node else "")
            if node_name == "prepare_build_tasks":
                return "prepare_build_tasks"
            if node_name == "build":
                return "build"
    return "build"


def _supported_resume_node(node_name: str, *, workflow_scope: str = "") -> str:
    """限制独立规划 Graph 与主 Graph 各自可恢复的节点集合。"""

    if workflow_scope == "application_planning":
        supported = {
            "design_intent_analysis",
            "requirements",
            "product_planning",
            "ui_confirmation",
            "technical_planning",
        }
    else:
        if node_name == "inspect_database_context":
            return "prepare_build_tasks"
        supported = {
            "development_readiness_gate",
            "entity_source_binding",
            "project_planning",
            "inspect_workspace",
            "prepare_build_tasks",
            "build",
            "unit_test",
            "unit_test_repair",
            "test_phase_confirmation",
            "integration_test",
            "small_task_repair",
            "review_phase_confirmation",
            "code_review",
            "acceptance_phase_confirmation",
            "acceptance",
            "finalize_project",
        }
    return node_name if node_name in supported else ""


def _resume_values(value: dict[str, Any] | None) -> dict[str, Any]:
    """从前端 Workflow 快照恢复主流程允许公开往返的紧凑状态。"""

    if not value:
        return {}

    state = _optional_dict(value.get("state")) or {}
    result = _optional_dict(value.get("result")) or {}
    merged = {**state, **result}
    allowed_keys = {
        "product_plan",
        "product_plan_path",
        "product_plan_json_path",
        "technical_plan",
        "technical_plan_path",
        "technical_plan_json_path",
        "project_plan",
        "pages",
        "frontend_pages",
        "pending_project_plan",
        "project_plan_path",
        "project_plan_json_path",
        "detail_selection",
        "selectedPageId",
        "selected_api_contract_id",
        "selected_endpoint_id",
        "selected_entity_id",
        "detail_target_type",
        "page_spec_draft",
        "data_source_spec_draft",
        "detail_plans",
        "entity_source_binding_submission",
        "workspace_snapshot_summary",
        "workspace_snapshot_path",
        "workspace_snapshot_hash",
        "workspace_revision",
        "requirement_spec",
        "requirement_spec_path",
        "requirement_spec_json_path",
        "build_task_plan",
        "build_task_plan_path",
        "build_task_plan_confirmation",
        "test_phase_confirmation",
        "review_phase_confirmation",
        "acceptance_phase_confirmation",
        "code_review_result",
        "code_review_report_path",
        "code_review_repair_confirmation",
        "code_review_repair_status",
        "code_review_repair_result",
        "code_review_build_results",
        "code_review_repair_iteration",
        "code_review_max_repair_iterations",
        "quality_gate_passed",
        "test_report",
        "test_report_path",
        "build_execution_scope",
        "last_persisted_build_execution_scope",
        "build_task_plan_persisted",
        "build_context",
        "execution_resource_claims",
        "tasks",
        "build_results",
        "build_summary",
        "code_changes",
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
        "repair_return_node",
        "integration_next_action",
        "unit_test_decision",
        "unit_test_gate_passed",
        "unit_test_build_code_changes",
        "unit_test_build_code_change_sets",
        "unit_test_build_diff_captured",
        "unit_test_results",
        "unit_test_report",
        "unit_test_report_path",
        "unit_test_quality_gate_passed",
        "unit_test_next_action",
        "unit_test_generation",
        "unit_test_mapping_path",
        "unit_test_code_change_sets",
        "unit_test_generation_code_change_sets",
        "unit_test_repair_enabled",
        "unit_test_repair_task_plan",
        "unit_test_repair_task_plan_path",
        "unit_test_repair_iteration",
        "unit_test_max_repair_iterations",
        "frontend_performance_decision",
        "frontend_performance_test_enabled",
        "integration_build_checks_completed",
        "integration_build_results",
        "unit_test_generation_context",
        "unit_test_affected_layers",
        "clarification",
        "selected_skill_names",
        "workflow_scope",
        "acceptance_adjustment",
        "preview_url",
        "launch_result",
        "acceptance_request",
        "acceptance_decision",
        "accepted",
        "ui_designs",
        "conversation_response",
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
        "build_execution_scope": "buildExecutionScope",
        "last_persisted_build_execution_scope": "lastPersistedBuildExecutionScope",
        "build_task_plan_persisted": "buildTaskPlanPersisted",
        "build_task_plan_confirmation": "buildTaskPlanConfirmation",
        "test_phase_confirmation": "testPhaseConfirmation",
        "review_phase_confirmation": "reviewPhaseConfirmation",
        "acceptance_phase_confirmation": "acceptancePhaseConfirmation",
        "preview_url": "previewUrl",
        "launch_result": "launchResult",
        "acceptance_request": "acceptanceRequest",
        "acceptance_decision": "acceptanceDecision",
        "code_review_result": "codeReviewResult",
        "code_review_report_path": "codeReviewReportPath",
        "code_review_repair_result": "codeReviewRepair",
        "code_review_build_results": "codeReviewBuildResults",
        "code_review_repair_status": "codeReviewRepairStatus",
        "code_review_repair_iteration": "codeReviewRepairIteration",
        "code_review_max_repair_iterations": "codeReviewMaxRepairIterations",
        "code_review_repair_confirmation": "codeReviewRepairConfirmation",
        "quality_gate_passed": "qualityGatePassed",
        "test_report": "testReport",
        "test_report_path": "testReportPath",
        "build_results": "buildResults",
        "build_summary": "buildSummary",
        "code_changes": "codeChanges",
        "repair_task_plan": "repairTaskPlan",
        "repair_tasks": "repairTasks",
        "repair_return_node": "repairReturnNode",
        "integration_next_action": "integrationNextAction",
        "unit_test_decision": "unitTestDecision",
        "unit_test_gate_passed": "unitTestGatePassed",
        "unit_test_build_code_changes": "unitTestBuildCodeChanges",
        "unit_test_build_code_change_sets": "unitTestBuildCodeChangeSets",
        "unit_test_build_diff_captured": "unitTestBuildDiffCaptured",
        "unit_test_results": "unitTestResults",
        "unit_test_report": "unitTestReport",
        "unit_test_report_path": "unitTestReportPath",
        "unit_test_quality_gate_passed": "unitTestQualityGatePassed",
        "unit_test_next_action": "unitTestNextAction",
        "unit_test_generation": "unitTestGeneration",
        "unit_test_mapping_path": "unitTestMappingPath",
        "unit_test_code_change_sets": "unitTestCodeChangeSets",
        "unit_test_generation_code_change_sets": "unitTestGenerationCodeChangeSets",
        "unit_test_repair_enabled": "unitTestRepairEnabled",
        "unit_test_repair_task_plan": "unitTestRepairTaskPlan",
        "unit_test_repair_task_plan_path": "unitTestRepairTaskPlanPath",
        "unit_test_repair_iteration": "unitTestRepairIteration",
        "unit_test_max_repair_iterations": "unitTestMaxRepairIterations",
        "frontend_performance_decision": "frontendPerformanceDecision",
        "frontend_performance_test_enabled": "frontendPerformanceTestEnabled",
        "integration_build_checks_completed": "integrationBuildChecksCompleted",
        "integration_build_results": "integrationBuildResults",
        "unit_test_generation_context": "unitTestGenerationContext",
        "unit_test_affected_layers": "unitTestAffectedLayers",
    }
    for snake_key, camel_key in camel_aliases.items():
        if snake_key not in resumed_values and merged.get(camel_key) is not None:
            resumed_values[snake_key] = merged[camel_key]
    normalized_test_report_path = str(
        resumed_values.get("test_report_path") or ""
    ).replace("\\", "/").strip()
    if normalized_test_report_path == ".xcodeagent/reports/test-report.md":
        resumed_values["test_report_path"] = normalized_test_report_path
    else:
        resumed_values.pop("test_report_path", None)
    review_result = resumed_values.get("code_review_result")
    if (
        "code_review_report_path" not in resumed_values
        and isinstance(review_result, dict)
        and review_result.get("reportPath")
    ):
        resumed_values["code_review_report_path"] = review_result["reportPath"]
    test_report_result = merged.get("testReportResult")
    test_report_path = (
        str(test_report_result.get("reportPath") or "").replace("\\", "/").strip()
        if isinstance(test_report_result, dict)
        else ""
    )
    if (
        "test_report_path" not in resumed_values
        and test_report_path == ".xcodeagent/reports/test-report.md"
    ):
        resumed_values["test_report_path"] = test_report_path
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
    selected_entity_id = _optional_text(
        merged.get("selected_entity_id") or merged.get("selectedEntityId")
    )
    detail_target_type = _supported_detail_target_type(
        _optional_text(merged.get("detail_target_type") or merged.get("detailTargetType"))
    )
    if selected_api_contract_id:
        resumed_values["selected_api_contract_id"] = selected_api_contract_id
    if selected_endpoint_id:
        resumed_values["selected_endpoint_id"] = selected_endpoint_id
    if selected_entity_id:
        resumed_values["selected_entity_id"] = selected_entity_id
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
    project_plan_path = workspace_root / ".xcodeagent" / "plans" / "technical-plan.json"
    if not project_plan_path.is_file():
        return {}
    technical_plan = load_project_plan_json(
        project_plan_path,
        hydrate_detail_designs=True,
    )
    if not isinstance(technical_plan, dict):
        raise ValueError("technical-plan.json 的根结构必须是 JSON 对象。")
    if technical_plan.get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE:
        raise ValueError("只接受当前 TechnicalPlan。")
    requirement_spec = load_requirement_spec_json(
        workspace_root / ".xcodeagent" / "specs" / "requirement-spec.json"
    )
    product_plan = load_project_plan_json(
        workspace_root / ".xcodeagent" / "plans" / "product-plan.json"
    )
    ui_designs = load_ui_designs_json(
        workspace_root / ".xcodeagent" / "specs" / "ui-designs.json"
    )
    if not product_plan or not ui_designs:
        raise ValueError("当前 TechnicalPlan 运行时需要已确认的 ProductPlan 和 UiManifest。")
    project_plan = materialize_technical_plan_runtime(
        technical_plan,
        requirement_spec,
        product_plan,
        ui_designs,
    )
    plan_pages = project_plan.get("pages", [])
    normalized_pages = [
        dict(page)
        for page in plan_pages
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
        project_plan = {
            **project_plan,
            "pages": [*normalized_pages, selected_page],
        }
    return {
        "technical_plan": technical_plan,
        "project_plan": project_plan,
        "pages": normalized_pages,
        "project_plan_path": _markdown_sibling_path(project_plan_path),
        "project_plan_json_path": str(project_plan_path),
        "technical_plan_path": _markdown_sibling_path(project_plan_path),
        "technical_plan_json_path": str(project_plan_path),
        "ui_designs": ui_designs,
        "requirement_spec": requirement_spec,
        "product_plan": product_plan,
    }


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
                question_label = _CLARIFICATION_QUESTION_LABELS.get(
                    str(key),
                    str(key),
                )
                lines.extend([f"- {question_label}", f"  回答：{answer_text}"])
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


def _build_task_plan_confirmation(value: Any) -> dict[str, Any]:
    """提取并限制 DAG 确认动作，保持其与正式文档确认协议隔离。"""

    if not isinstance(value, dict):
        return {}
    raw = value.get("build_task_plan_confirmation")
    if not isinstance(raw, dict):
        return {}
    action = _optional_text(raw.get("action")).lower()
    if action not in {"confirm", "patch", "regenerate"}:
        return {}
    patches = raw.get("patches")
    return {
        "mode": "build_task_plan_confirmation",
        "action": action,
        "patches": [dict(item) for item in patches if isinstance(item, dict)]
        if isinstance(patches, list)
        else [],
    }


def _test_phase_confirmation_submission(value: Any) -> dict[str, str]:
    """提取测试阶段进入确认动作，拒绝自然语言或未知动作。"""

    if not isinstance(value, dict):
        return {}
    if "test_phase_confirmation" not in value:
        return {}
    answer = value.get("test_phase_confirmation")
    if not isinstance(answer, dict):
        raise ValueError("test_phase_confirmation 必须是结构化对象。")
    action = _optional_text(answer.get("action")).lower()
    if action != "confirm":
        raise ValueError("test_phase_confirmation.action 只支持 confirm。")
    return {"mode": "test_phase_confirmation", "action": "confirm"}


def _review_phase_confirmation_submission(value: Any) -> dict[str, str]:
    """提取审查阶段进入确认动作，拒绝自然语言或未知动作。"""

    if not isinstance(value, dict):
        return {}
    if "review_phase_confirmation" not in value:
        return {}
    answer = value.get("review_phase_confirmation")
    if not isinstance(answer, dict):
        raise ValueError("review_phase_confirmation 必须是结构化对象。")
    action = _optional_text(answer.get("action")).lower()
    if action != "confirm":
        raise ValueError("review_phase_confirmation.action 只支持 confirm。")
    return {"mode": "review_phase_confirmation", "action": "confirm"}


def _acceptance_phase_confirmation_submission(value: Any) -> dict[str, str]:
    """提取验收阶段进入确认动作，拒绝自然语言和未知动作。"""

    if not isinstance(value, dict):
        return {}
    if "acceptance_phase_confirmation" not in value:
        return {}
    answer = value.get("acceptance_phase_confirmation")
    if not isinstance(answer, dict):
        raise ValueError("acceptance_phase_confirmation 必须是结构化对象。")
    action = _optional_text(answer.get("action")).lower()
    if action != "confirm":
        raise ValueError("acceptance_phase_confirmation.action 只支持 confirm。")
    return {"mode": "acceptance_phase_confirmation", "action": "confirm"}


def _code_review_repair_confirmation_submission(value: Any) -> dict[str, str]:
    """提取代码审查一键修复动作，拒绝自然语言和未知动作。"""

    if not isinstance(value, dict):
        return {}
    if "code_review_repair_confirmation" not in value:
        return {}
    answer = value.get("code_review_repair_confirmation")
    if not isinstance(answer, dict):
        raise ValueError("code_review_repair_confirmation 必须是结构化对象。")
    action = _optional_text(answer.get("action")).lower()
    if action != "repair_all":
        raise ValueError("code_review_repair_confirmation.action 只支持 repair_all。")
    return {"mode": "code_review_repair_confirmation", "action": "repair_all"}


def _clarification_mode(value: dict[str, Any] | None) -> str:
    """读取恢复快照中的 clarification 模式，用于识别过期的修复交互。"""

    if not isinstance(value, dict):
        return ""
    state = _optional_dict(value.get("state")) or {}
    result = _optional_dict(value.get("result")) or {}
    clarification = _optional_dict(state.get("clarification")) or _optional_dict(
        result.get("clarification")
    ) or {}
    return _optional_text(clarification.get("mode"))


def _has_code_review_issue_snapshot(value: dict[str, Any] | None) -> bool:
    """确认一键修复恢复请求携带了至少一个带稳定 ID 的审查问题。"""

    if not isinstance(value, dict):
        return False
    result = value.get("code_review_result")
    if not isinstance(result, dict):
        return False
    issues = result.get("issues")
    if not isinstance(issues, list) or not issues or len(issues) > 100:
        return False
    return all(
        isinstance(issue, dict) and bool(str(issue.get("id") or "").strip())
        for issue in issues
    )


def _unit_test_decision(value: Any) -> str:
    """从结构化确认答案提取单元测试的 skip/run 决策。"""

    if not isinstance(value, dict):
        return ""
    answer = value.get("unit_test_confirmation")
    if isinstance(answer, dict) and "selected" in answer:
        selected = answer.get("selected")
        answer = selected[0] if isinstance(selected, list) and selected else selected
    normalized = str(answer or "").strip().casefold()
    if normalized in {"skip", "是", "yes", "true", "跳过"}:
        return "skip"
    if normalized in {"run", "否", "no", "false", "继续"}:
        return "run"
    return ""


def _frontend_performance_decision(value: Any) -> str:
    """从结构化确认答案提取前端性能测试的 skip/run 决策。"""

    if not isinstance(value, dict):
        return ""
    answer = value.get("frontend_performance_confirmation")
    if isinstance(answer, dict) and "selected" in answer:
        selected = answer.get("selected")
        answer = selected[0] if isinstance(selected, list) and selected else selected
    normalized = str(answer or "").strip().casefold()
    if normalized in {"skip", "是", "yes", "true", "跳过"}:
        return "skip"
    if normalized in {"run", "否", "no", "false", "继续"}:
        return "run"
    return ""


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


def _entity_source_binding_submission(value: Any) -> dict[str, Any] | None:
    """读取独立 EntitySourceBinding 的确认提交。"""

    if not isinstance(value, dict):
        return None
    submission = value.get("entity_source_binding")
    if not isinstance(submission, dict):
        return None
    if submission.get("review_status") != "confirmed":
        return None
    return submission


def _entity_design_action(value: Any) -> dict[str, Any] | None:
    """从结构化确认答案中提取实体设计动作。

    EntitySourceBinding 是独立交互：前端在数据源选择、
    外部 API 信息补充、静态数据构建、手动绑定与表生成审批等步骤提交
    ``{entity_design: {action, entity_id, ...}}``，由本函数规整后进入
    resume_values 供 entity_source_binding 节点消费。
    """

    if not isinstance(value, dict):
        return None
    action = value.get("entity_design")
    if not isinstance(action, dict):
        return None
    return normalize_entity_design_action(action)


def _application_planning_interaction(
    payload: dict[str, Any],
    *,
    forwarded_props: dict[str, Any],
    fallback_request: str,
    original_request: str,
) -> dict[str, Any] | None:
    """只从当前 AG-UI 字段读取并校验创建规划原生中断恢复载荷。"""

    value = (
        _optional_dict(payload.get("applicationPlanningInteraction"))
        or _optional_dict(payload.get("application_planning_interaction"))
        or _optional_dict(forwarded_props.get("applicationPlanningInteraction"))
        or _optional_dict(forwarded_props.get("application_planning_interaction"))
    )
    if value is None:
        return None
    interaction = ApplicationPlanningInteraction.model_validate(value).model_dump(
        by_alias=False,
        exclude_none=True,
    )
    interaction_request = _optional_text(interaction.get("request"))
    if not interaction_request or interaction_request == _optional_text(fallback_request):
        # 澄清答案提交轮的 message 只是占位文本，真实需求在 originalRequest 里；
        # 即使客户端把占位文本放进 interaction.request，也不能覆盖真实原始需求。
        original_request = (
            _optional_text(payload.get("originalRequest"))
            or _optional_text(payload.get("original_request"))
            or _optional_text(forwarded_props.get("originalRequest"))
            or _optional_text(forwarded_props.get("original_request"))
        )
        interaction["request"] = _merge_clarification_answers(
            request=fallback_request,
            original_request=original_request,
            clarification_answers=interaction.get("answers"),
        )
    if interaction.get("action") == "ui_action":
        normalized_ui_action = _ui_design_action(
            {"ui_design_action": interaction.get("ui_action")}
        )
        if normalized_ui_action is None:
            raise ValueError("applicationPlanningInteraction 包含无效的 uiAction。")
        interaction["ui_action"] = normalized_ui_action
    return interaction


def _ui_design_action(value: Any) -> dict[str, Any] | None:
    """从结构化确认答案中提取 UI 确认节点的单页/多页动作。

    单页动作形如 ``{ui_design_action: {pageId, action, templateId?}}``，由前端
    UiDesignConfirmationPanel 在用户逐页"选模板"或"换一换"时即时提交。
    多页调整动作形如 ``{ui_design_action: {action: "adjust_pages", pageIds: [...],
    instruction: "..."}}``，由底部斜杠提及 + 调整按钮提交。
    多页并发动作形如 ``{ui_design_action: {action: "multi", actions: [{pageId,
    action, templateId?}, ...]}}``，由前端把连续点击的多页"换一换/选模板"攒成
    一个 run 提交，后端在一个 run 内并发处理（最多 3 个）。
    action 接受 select_template / regenerate / adjust_pages / skip / multi；其余视为无动作。
    """

    if not isinstance(value, dict):
        return None
    action = value.get("ui_design_action")
    if not isinstance(action, dict):
        return None
    action_type = _optional_text(action.get("action"))
    if action_type not in {
        "select_template",
        "regenerate",
        "adjust_pages",
        "skip",
        "multi",
    }:
        return None

    # 跳过 UI 设计不绑定具体页面，直接把当前创建规划推进到技术规划。
    if action_type == "skip":
        return {"action": "skip"}

    # 多页并发：前端攒多页"换一换/选模板"成一个 run 提交。每项是合法单页动作，
    # 同 pageId 去重（保留最后一个）。截断上限 12（覆盖绝大多数应用的页面数），
    # 并发仍由 ui_confirmation 节点的 Semaphore(3) 限流——超出 3 个的页面在信号量
    # 前排队等前面的释放，不会压垮模型服务。原硬编码 3 会导致 7 页应用一次"全部
    # 生成"只处理前 3 个、第 4 个起被丢弃，用户须多次点击。
    if action_type == "multi":
        raw_actions = action.get("actions")
        if not isinstance(raw_actions, list):
            return None
        normalized: list[dict[str, Any]] = []
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            sub_type = _optional_text(item.get("action"))
            if sub_type not in {"select_template", "regenerate"}:
                continue
            page_id = _optional_text(item.get("pageId")) or _optional_text(
                item.get("page_id")
            )
            if not page_id:
                continue
            sub: dict[str, Any] = {"pageId": page_id, "action": sub_type}
            if sub_type == "select_template":
                template_id = _optional_text(item.get("templateId")) or _optional_text(
                    item.get("template_id")
                )
                if not template_id:
                    continue
                sub["templateId"] = template_id
            # 同 pageId 去重：移除已有同 pageId 项，追加新项（保留最后一个）。
            normalized = [n for n in normalized if n.get("pageId") != page_id]
            normalized.append(sub)
            if len(normalized) >= 12:
                break
        if not normalized:
            return None
        return {"action": "multi", "actions": normalized}

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
