"""把主 Workflow 的稳定业务边界投射到应用生命周期文件。"""

from __future__ import annotations

from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleError,
    DevelopmentContinuationTarget,
    ExecutionResourceClaim,
    ExecutionResourceReason,
    ExecutionResourceRole,
    PendingInteractionType,
    WorkbenchExecutionStatus,
    utc_now,
)
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    application_lifecycle_payload,
    complete_workbench_execution,
    end_workbench_execution,
    load_application_lifecycle,
    persist_workbench_interaction_submission,
    start_workbench_execution,
    update_workbench_execution,
)
from app.services.application_revision_lifecycle import (
    complete_active_revision,
    register_revision_impact,
    update_active_revision_progress,
)
from app.services.development_continuation import (
    development_continuation_payload,
    issue_development_continuation,
    register_development_continuation,
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
    if workflow_inputs.get("workflow_scope") == "application_planning":
        # 创建规划有独立的 lifecycle/Graph 边界，不属于工作台执行；即使
        # 未来调用方误用本适配器，也不能登记 execution 或资源锁。
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
            allow_entity_binding_continuation=(
                workflow_inputs.get("workflow_action")
                == "continue_after_entity_binding"
            ),
        )
    revision_continuation_replaces_run_id = (
        str(resume_values.get("revision_continuation_replaces_run_id") or "").strip()
        if workflow_inputs.get("workflow_action") == "continue_revision_build"
        else ""
    )
    if revision_continuation_replaces_run_id:
        _validate_revision_continuation_replacement(
            lifecycle,
            run_id=revision_continuation_replaces_run_id,
            scope=scope_type,
            target_id=target_id,
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
        replaces_run_id=(
            revision_continuation_replaces_run_id
            or explicit_resume_run_id
            or submission_run_id
            or None
        ),
        resource_claims=resource_claims,
        development_continuation_consume=workflow_inputs.get("development_continuation_consume"),
    )
    # application_revision 仍可能停在草稿确认门，只有节点确认全部正式产物后
    # 才能把 formal revision 切成 building，避免运行登记提前改变业务事实。
    if phase != "application_revision":
        _sync_active_revision_status(workspace, "building")
        state = load_application_lifecycle(workspace) or state
    return application_lifecycle_payload(state)


def _validate_revision_continuation_replacement(
    lifecycle: ApplicationLifecycle,
    *,
    run_id: str,
    scope: str,
    target_id: str,
) -> None:
    """校验存在来源 execution 时的 continuation 原子接管边界。"""

    active_revision = lifecycle.active_formal_revision
    execution = lifecycle.active_executions.get(run_id)
    if (
        active_revision is None
        or active_revision.status != "building"
        or active_revision.continuation_consumed_at is None
        or active_revision.continuation_source_run_id != run_id
        or execution is None
        or execution.phase != "application_revision"
    ):
        raise ApplicationLifecycleConflictError("revision continuation 的规划执行绑定无效。")
    if execution.scope != scope or execution.target_id != target_id:
        raise ApplicationLifecycleConflictError("revision continuation 的开发目标与规划执行不一致。")


def _validate_resumable_execution(
    lifecycle: ApplicationLifecycle,
    *,
    run_id: str,
    thread_id: str,
    scope: str,
    target_id: str,
    allow_plan_adjustment_debug: bool = False,
    allow_entity_binding_continuation: bool = False,
) -> None:
    """只允许安全恢复同一目标上的旧执行，阶段确认可切换到新对话。"""

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
    frontend_performance_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type
        == PendingInteractionType.FRONTEND_PERFORMANCE_CONFIRMATION
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
    review_phase_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type
        == PendingInteractionType.REVIEW_PHASE_CONFIRMATION
    )
    code_review_repair_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type
        == PendingInteractionType.CODE_REVIEW_REPAIR_CONFIRMATION
    )
    acceptance_phase_confirmation = (
        execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type
        == PendingInteractionType.ACCEPTANCE_PHASE_CONFIRMATION
    )
    entity_binding_continuation = (
        allow_entity_binding_continuation
        and execution.status == WorkbenchExecutionStatus.AWAITING_USER
        and execution.pending_interaction is not None
        and execution.pending_interaction.type == PendingInteractionType.ENTITY_SOURCE_BINDING
    )
    if (
        not resumable_status
        and not debug_plan_adjustment
        and not unit_test_confirmation
        and not frontend_performance_confirmation
        and not repair_scope_confirmation
        and not test_phase_confirmation
        and not review_phase_confirmation
        and not code_review_repair_confirmation
        and not acceptance_phase_confirmation
        and not entity_binding_continuation
    ):
        raise ApplicationLifecycleConflictError("只有已停止或失败的工作台执行可以继续。")
    # 阶段确认是显式的新对话边界：只有结构化测试/审查确认允许把 execution 所有权
    # 从上一阶段 thread 原子转交给新的阶段 thread，其余恢复仍必须留在原对话。
    if (
        execution.thread_id != thread_id
        and not test_phase_confirmation
        and not review_phase_confirmation
        and not acceptance_phase_confirmation
    ):
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
        complete_workbench_execution(workspace, run_id=run_id)
        change_id = complete_active_revision(workspace)
        if change_id:
            update["application_revision_completion"] = {"changeId": change_id}
        completed = load_application_lifecycle(workspace)
        return application_lifecycle_payload(completed) if completed is not None else None

    status = str(update.get("status") or "")
    if node_name == "application_revision" and status == "discarded":
        # 丢弃正式草稿会结束本次主 Workflow；节点已经释放 active formal
        # revision，这里只收口其工作台 execution 和全部资源锁，不能再把
        # discarded 当成普通 running 快照留下孤儿执行。
        ended = end_workbench_execution(workspace, run_id=run_id, missing_ok=True)
        update["application_revision_discarded"] = {
            "changeId": str(update.get("change_id") or ""),
            "status": "discarded",
        }
        return application_lifecycle_payload(ended)
    if (
        node_name == "launch_project" or clarification_mode(update) == "page_acceptance"
    ) and status == "requires_user_input":
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
        if clarification_mode(update) == "entity_source_binding_required":
            continuation = _register_entity_binding_continuation(
                workspace,
                state=state,
                run_id=run_id,
                update=update,
            )
            update["development_continuation"] = development_continuation_payload(
                continuation
            )
            state = load_application_lifecycle(workspace) or state
        if pending_type == PendingInteractionType.IMPACT_CONFIRMATION:
            _register_revision_impact_boundary(
                workspace,
                update,
                source_thread_id=lifecycle.active_executions[run_id].thread_id,
                source_run_id=run_id,
            )
            state = load_application_lifecycle(workspace) or state
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
    if node_name == "entity_source_binding" and status == "completed":
        continuation_id = str(update.get("development_continuation_id") or "").strip()
        if continuation_id:
            update["development_continuation"] = issue_development_continuation(
                workspace,
                continuation_id=continuation_id,
            )
        completed = complete_workbench_execution(
            workspace,
            run_id=run_id,
            phase="entity_source_binding",
        )
        return application_lifecycle_payload(completed)
    # 阶段确认节点完成后 Graph 会立即进入下一阶段。生命周期提前投影真实的下一节点，
    # 避免执行已经开始时顶部步骤条仍停留在上一阶段。
    projected_phase = node_name
    if node_name == "test_phase_confirmation" and status == "completed":
        projected_phase = "integration_test"
    elif node_name == "review_phase_confirmation" and status == "completed":
        projected_phase = "code_review"
    elif node_name == "acceptance_phase_confirmation" and status == "completed":
        projected_phase = "acceptance"
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
    _sync_active_revision_status(workspace, "stopped")
    state = load_application_lifecycle(workspace) or state
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
    _sync_active_revision_status(workspace, "failed")
    state = load_application_lifecycle(workspace) or state
    return application_lifecycle_payload(state)


def _pending_interaction(
    update: dict[str, Any],
) -> tuple[PendingInteractionType, dict[str, Any]]:
    """把 Workflow clarification 模式映射为稳定生命周期交互类型。"""

    clarification = update.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    mode = str(clarification.get("mode") or "")
    interaction_type = {
        "build_task_plan_confirmation": PendingInteractionType.TASK_PLAN_CONFIRMATION,
        "repair_scope_confirmation": PendingInteractionType.REPAIR_SCOPE_CONFIRMATION,
        "unit_test_confirmation": PendingInteractionType.UNIT_TEST_CONFIRMATION,
        "frontend_performance_confirmation": (
            PendingInteractionType.FRONTEND_PERFORMANCE_CONFIRMATION
        ),
        "test_phase_confirmation": PendingInteractionType.TEST_PHASE_CONFIRMATION,
        "review_phase_confirmation": PendingInteractionType.REVIEW_PHASE_CONFIRMATION,
        "code_review_repair_confirmation": PendingInteractionType.CODE_REVIEW_REPAIR_CONFIRMATION,
        "acceptance_phase_confirmation": PendingInteractionType.ACCEPTANCE_PHASE_CONFIRMATION,
        "page_acceptance": PendingInteractionType.PAGE_ACCEPTANCE,
        "entity_source_binding": PendingInteractionType.ENTITY_SOURCE_BINDING,
        "entity_source_binding_required": PendingInteractionType.ENTITY_SOURCE_BINDING,
        "agent_approval": PendingInteractionType.AGENT_APPROVAL,
        "revision_draft_confirmation": PendingInteractionType.REVISION_DRAFT_CONFIRMATION,
        "revision_impact_confirmation": PendingInteractionType.IMPACT_CONFIRMATION,
    }.get(mode, PendingInteractionType.PLAN_ADJUSTMENT)
    return interaction_type, clarification


def clarification_mode(update: dict[str, Any]) -> str:
    """读取节点结果中的 clarification 模式，供生命周期边界选择交互类型。"""

    clarification = update.get("clarification")
    return str(clarification.get("mode") or "") if isinstance(clarification, dict) else ""


def _register_entity_binding_continuation(
    workspace: str,
    *,
    state: ApplicationLifecycle,
    run_id: str,
    update: dict[str, Any],
):
    """把开发门禁目标登记为服务端 continuation，供独立实体 execution 引用。"""

    execution = state.active_executions.get(run_id)
    clarification = update.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    raw_target = clarification.get("development_target")
    raw_target = raw_target if isinstance(raw_target, dict) else {}
    target_type = str(raw_target.get("type") or "")
    target_id = str(raw_target.get("id") or "").strip()
    if execution is None or target_type not in {"page", "endpoint"} or not target_id:
        raise ApplicationLifecycleConflictError("实体门禁缺少可续接的原开发目标。")
    target = DevelopmentContinuationTarget(
        type=target_type,
        pageId=target_id if target_type == "page" else None,
        apiContractId=(
            str(raw_target.get("api_contract_id") or "").strip()
            if target_type == "endpoint"
            else None
        ),
        endpointId=target_id if target_type == "endpoint" else None,
        label=str(raw_target.get("label") or target_id),
    )
    missing_entities = clarification.get("missing_entities")
    entity_ids = [
        str(item.get("entity_id") or "").strip()
        for item in missing_entities
        if isinstance(item, dict) and str(item.get("entity_id") or "").strip()
    ] if isinstance(missing_entities, list) else []
    return register_development_continuation(
        workspace,
        source_thread_id=execution.thread_id,
        source_run_id=execution.run_id,
        request=str(update.get("request") or "继续原开发任务。"),
        target=target,
        required_entity_ids=entity_ids,
    )


def _register_revision_impact_boundary(
    workspace: str,
    update: dict[str, Any],
    *,
    source_thread_id: str,
    source_run_id: str,
) -> None:
    """在主 Workflow SmallTask 升级时登记与 lifecycle revision 绑定的 impact。"""

    raw_impact = update.get("revision_impact")
    if not isinstance(raw_impact, dict):
        raise ValueError("revision impact confirmation 缺少结构化影响范围。")
    lifecycle = load_application_lifecycle(workspace)
    interaction_id = str(raw_impact.get("interactionId") or "")
    if (
        lifecycle is not None
        and lifecycle.pending_revision_impact is not None
        and lifecycle.pending_revision_impact.interaction_id == interaction_id
    ):
        return
    raw_target = update.get("change_target")
    raw_target = (
        raw_target
        if isinstance(raw_target, dict) and raw_target
        else {"type": "application"}
    )
    register_revision_impact(
        workspace,
        interaction_id=interaction_id,
        source_thread_id=source_thread_id,
        source_run_id=source_run_id,
        request=str(
            update.get("request")
            or raw_impact.get("reason")
            or "SmallTask 正式升级"
        ),
        target=RevisionTarget.model_validate(raw_target),
        impact=RevisionImpact.model_validate(
            {
                key: value
                for key, value in raw_impact.items()
                if key not in {"interactionId", "status"}
            }
        ),
    )


def _sync_active_revision_status(workspace: str, status: str) -> None:
    """在工作台执行开始、停止或失败时同步现有 formal revision 状态。"""

    lifecycle = load_application_lifecycle(workspace)
    active = lifecycle.active_formal_revision if lifecycle is not None else None
    if active is None or active.status == status:
        return
    if status not in {"building", "stopped", "failed"}:
        raise ValueError("不支持的 formal revision 执行状态。")
    update_active_revision_progress(
        workspace,
        change_id=active.change_id,
        status=status,
        current_artifact=active.current_artifact,
        remaining_artifacts=active.remaining_artifacts,
    )


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
