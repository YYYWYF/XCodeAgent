"""把主 Workflow 的稳定业务边界投射到应用生命周期文件。"""

from __future__ import annotations

from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleError,
    ExecutionResourceClaim,
    ExecutionResourceReason,
    ExecutionResourceRole,
    PendingInteractionType,
    WorkbenchExecutionStatus,
    utc_now,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    application_lifecycle_payload,
    complete_workbench_execution,
    load_application_lifecycle,
    persist_workbench_interaction_submission,
    start_workbench_execution,
    update_workbench_execution,
)


def begin_workflow_lifecycle(
    workflow_inputs: dict[str, Any],
    *,
    thread_id: str,
    run_id: str,
    phase: str,
) -> dict[str, Any] | None:
    """在主 Workflow 获得工作区租约后登记活动计划执行。"""

    workspace = str(workflow_inputs.get("workspace") or "").strip()
    lifecycle = load_application_lifecycle(workspace) if workspace else None
    if lifecycle is None:
        return None
    resume_values = workflow_inputs.get("resume_values")
    resume_values = resume_values if isinstance(resume_values, dict) else {}
    submission = resume_values.get("lifecycle_interaction_submission")
    approved_repair_claims: list[ExecutionResourceClaim] = []
    if isinstance(submission, dict):
        submission_run_id = str(submission.get("runId") or "")
        if submission_run_id:
            submitted_execution = lifecycle.active_executions.get(submission_run_id)
            pending = submitted_execution.pending_interaction if submitted_execution else None
            if (
                pending is not None
                and pending.type == PendingInteractionType.REPAIR_SCOPE_CONFIRMATION
                and _repair_scope_approved(str(workflow_inputs.get("request") or ""))
            ):
                approved_repair_claims = _repair_resource_claims(pending.payload)
            persist_workbench_interaction_submission(
                workspace,
                run_id=submission_run_id,
                interaction_id=str(submission.get("id") or ""),
                based_on_revision=int(submission.get("basedOnRevision") or 0),
            )
    scope = resume_values.get("build_execution_scope")
    scope = scope if isinstance(scope, dict) else {}
    page_id = str(resume_values.get("selectedPageId") or "").strip() or None
    scope_type = str(scope.get("type") or ("page" if page_id else "application"))
    target_id = str(scope.get("targetId") or page_id or "application")
    raw_claims = resume_values.get("execution_resource_claims")
    resource_claims = [
        ExecutionResourceClaim.model_validate(item)
        for item in raw_claims
        if isinstance(item, dict)
    ] if isinstance(raw_claims, list) else None
    if approved_repair_claims:
        resource_claims = [*(resource_claims or []), *approved_repair_claims]
    explicit_resume_run_id = str(
        resume_values.get("resume_execution_run_id") or ""
    ).strip()
    if explicit_resume_run_id:
        _validate_resumable_execution(
            lifecycle,
            run_id=explicit_resume_run_id,
            thread_id=thread_id,
            scope=scope_type,
            target_id=target_id,
            allow_plan_adjustment_debug=bool(
                workflow_inputs.get("workflow_debug_enabled")
            ),
        )
    submission_run_id = (
        str(submission.get("runId") or "")
        if isinstance(submission, dict)
        else ""
    )
    state = start_workbench_execution(
        workspace,
        scope=scope_type,
        target_id=target_id,
        page_id=page_id,
        thread_id=thread_id,
        run_id=run_id,
        phase=phase,
        replaces_run_id=explicit_resume_run_id or submission_run_id or None,
        resource_claims=resource_claims,
    )
    return application_lifecycle_payload(state)


def _validate_resumable_execution(
    lifecycle: ApplicationLifecycle,
    *,
    run_id: str,
    thread_id: str,
    scope: str,
    target_id: str,
    allow_plan_adjustment_debug: bool = False,
) -> None:
    """只允许安全恢复同一目标上的旧执行，测试阶段确认可切换到新对话。"""

    execution = lifecycle.active_executions.get(run_id)
    if execution is None:
        raise ApplicationLifecycleConflictError("要继续的工作台执行已不存在，请刷新后重试。")
    resumable_status = execution.status in {
        WorkbenchExecutionStatus.STOPPED,
        WorkbenchExecutionStatus.FAILED,
    }
    # DAG 生成失败会把运行置为 awaiting_user/plan_adjustment；调试面板已经是
    # 用户明确选择的重新起点，此时应允许它原子接管旧执行，但不能绕过其他确认类型。
    debug_plan_adjustment = (
        allow_plan_adjustment_debug
        and execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type == PendingInteractionType.PLAN_ADJUSTMENT
    )
    # 单元测试、修复范围和测试阶段确认都是明确的人工门；其余待交互仍必须遵守
    # stopped/failed 或显式调试恢复规则，避免绕过人工门禁。
    unit_test_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type == PendingInteractionType.UNIT_TEST_CONFIRMATION
    )
    repair_scope_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type
        == PendingInteractionType.REPAIR_SCOPE_CONFIRMATION
    )
    test_phase_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type == PendingInteractionType.TEST_PHASE_CONFIRMATION
    )
    if (
        not resumable_status
        and not debug_plan_adjustment
        and not unit_test_confirmation
        and not repair_scope_confirmation
        and not test_phase_confirmation
    ):
        raise ApplicationLifecycleConflictError("只有已停止或失败的工作台执行可以继续。")
    # 测试阶段是显式的新对话边界：只有结构化测试确认允许把 execution 所有权
    # 从开发 thread 原子转交给新的测试 thread，其余恢复仍必须留在原对话。
    if execution.thread_id != thread_id and not test_phase_confirmation:
        raise ApplicationLifecycleConflictError("不能从其他对话接替工作台执行。")
    if execution.scope != scope or execution.target_id != target_id:
        raise ApplicationLifecycleConflictError("恢复目标与原工作台执行不一致。")


def project_workflow_lifecycle_boundary(
    workspace: str | None,
    *,
    run_id: str,
    node_name: str,
    update: dict[str, Any],
) -> dict[str, Any] | None:
    """在节点完成、阻断、失败和验收边界更新应用生命周期。"""

    lifecycle = load_application_lifecycle(workspace) if workspace else None
    if lifecycle is None or run_id not in lifecycle.active_executions:
        return None
    if node_name == "finalize_project" and update.get("status") == "completed":
        return application_lifecycle_payload(
            complete_workbench_execution(workspace, run_id=run_id)
        )

    status = str(update.get("status") or "")
    if node_name == "launch_project" and status == "requires_user_input":
        state = update_workbench_execution(
            workspace,
            run_id=run_id,
            phase=node_name,
            status=WorkbenchExecutionStatus.AWAITING_USER,
            pending_type=PendingInteractionType.PAGE_ACCEPTANCE,
            pending_payload=_acceptance_payload(update),
        )
        return application_lifecycle_payload(state)
    if status == "requires_user_input":
        pending_type, pending_payload = _pending_interaction(update)
        state = update_workbench_execution(
            workspace,
            run_id=run_id,
            phase=node_name,
            status=WorkbenchExecutionStatus.AWAITING_USER,
            pending_type=pending_type,
            pending_payload=pending_payload,
        )
        return application_lifecycle_payload(state)
    if status == "failed" or node_name == "handle_failure":
        message = str(update.get("error") or update.get("message") or "计划执行失败。")
        state = update_workbench_execution(
            workspace,
            run_id=run_id,
            phase=node_name,
            status=WorkbenchExecutionStatus.FAILED,
            error=ApplicationLifecycleError(
                code="workbench_execution_failed",
                message=message[:2048],
                recoverable=True,
                occurredAt=utc_now(),
                details={"phase": node_name},
            ),
        )
        return application_lifecycle_payload(state)
    # 确认节点完成后 Graph 会立即进入集成测试。生命周期提前投影真实的下一节点，
    # 避免测试正在执行时顶部步骤条仍停留在开发阶段。
    projected_phase = (
        "integration_test"
        if node_name == "test_phase_confirmation" and status == "completed"
        else node_name
    )
    state = update_workbench_execution(
        workspace,
        run_id=run_id,
        phase=projected_phase,
        status=WorkbenchExecutionStatus.RUNNING,
    )
    return application_lifecycle_payload(state)


def stop_workflow_lifecycle(
    workspace: str | None,
    *,
    run_id: str,
    phase: str,
) -> dict[str, Any] | None:
    """在消费端停止运行时持久化可恢复的停止状态。"""

    lifecycle = load_application_lifecycle(workspace) if workspace else None
    if lifecycle is None or run_id not in lifecycle.active_executions:
        return None
    state = update_workbench_execution(
        workspace,
        run_id=run_id,
        phase=phase,
        status=WorkbenchExecutionStatus.STOPPED,
    )
    return application_lifecycle_payload(state)


def fail_workflow_lifecycle(
    workspace: str | None,
    *,
    run_id: str,
    phase: str,
    error: Exception,
) -> dict[str, Any] | None:
    """在协议或运行时异常时持久化可恢复失败摘要。"""

    lifecycle = load_application_lifecycle(workspace) if workspace else None
    if lifecycle is None or run_id not in lifecycle.active_executions:
        return None
    state = update_workbench_execution(
        workspace,
        run_id=run_id,
        phase=phase,
        status=WorkbenchExecutionStatus.FAILED,
        error=ApplicationLifecycleError(
            code=str(getattr(error, "code", None) or "workflow_run_failed"),
            message=str(error)[:2048] or "Workflow 运行失败。",
            recoverable=True,
            occurredAt=utc_now(),
            details={"phase": phase, "type": type(error).__name__},
        ),
    )
    return application_lifecycle_payload(state)


def _pending_interaction(
    update: dict[str, Any],
) -> tuple[PendingInteractionType, dict[str, Any]]:
    """把 Workflow clarification 模式映射为稳定生命周期交互类型。"""

    clarification = update.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    mode = str(clarification.get("mode") or "")
    interaction_type = {
        "repair_scope_confirmation": PendingInteractionType.REPAIR_SCOPE_CONFIRMATION,
        "unit_test_confirmation": PendingInteractionType.UNIT_TEST_CONFIRMATION,
        "test_phase_confirmation": PendingInteractionType.TEST_PHASE_CONFIRMATION,
        "entity_source_binding": PendingInteractionType.ENTITY_SOURCE_BINDING,
        "entity_source_binding_required": PendingInteractionType.ENTITY_SOURCE_BINDING,
        "agent_approval": PendingInteractionType.AGENT_APPROVAL,
    }.get(mode, PendingInteractionType.PLAN_ADJUSTMENT)
    return interaction_type, clarification


def _acceptance_payload(update: dict[str, Any]) -> dict[str, Any]:
    """裁剪最终验收所需的预览地址、测试摘要和提示文案。"""

    request = update.get("acceptance_request")
    request = request if isinstance(request, dict) else {}
    return {
        "mode": "page_acceptance",
        "message": str(request.get("message") or "页面已准备好，请完成最终预览验收。"),
        "previewUrl": request.get("preview_url") or update.get("preview_url"),
        "testSummary": update.get("test_report", {}),
    }


def _repair_scope_approved(request: str) -> bool:
    """只在底部结构化操作提交明确批准文本时应用修复扩展资源。"""

    compact = request.replace(" ", "")
    return "批准修复范围" in compact and "拒绝" not in compact


def _repair_resource_claims(payload: dict[str, Any]) -> list[ExecutionResourceClaim]:
    """把待确认载荷中的稳定资源转换为修复扩展声明。"""

    resources = payload.get("requestedResources")
    if not isinstance(resources, list):
        return []
    claims: list[ExecutionResourceClaim] = []
    for item in resources:
        if not isinstance(item, dict):
            continue
        try:
            claim = ExecutionResourceClaim.model_validate(item)
            claims.append(
                claim.model_copy(
                    update={
                        "role": ExecutionResourceRole.DEPENDENCY,
                        "reason": ExecutionResourceReason.REPAIR_EXPANSION,
                    }
                )
            )
        except ValueError:
            continue
    return claims
