"""Application Planning TechnicalPlan 的事务化 Graph 节点。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_revision import RevisionTarget
from app.domain.application_planning_recovery import (
    ApplicationPlanningRecoveryBoundary,
    ApplicationPlanningOperation,
    application_planning_boundary_payload,
    application_planning_sha256,
    parse_application_planning_boundary,
)
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)
from app.services.application_planning_persistence import (
    confirm_application_planning_artifacts,
)
from app.services.application_revision_lifecycle import (
    begin_technical_plan_revision_generation,
    issue_revision_continuation,
    ensure_technical_plan_generation_lifecycle,
)
from app.workspace.plan_documents import (
    commit_technical_plan_document,
    technical_plan_json_path,
)


def _technical_boundary(state: ProjectState):
    """读取当前 Graph State 中的 TechnicalPlan boundary。"""

    boundary = parse_application_planning_boundary(
        state.get("application_planning_recovery_boundary")
    )
    if boundary is None or boundary.artifact.value != "technical_plan":
        raise ValueError(
            "TechnicalPlan 缺少可验证的 application-planning-boundary，"
            "拒绝猜测恢复位置。"
        )
    return boundary


def _technical_request(state: ProjectState) -> str:
    """从当前交互或服务端设计变更上下文读取 TechnicalPlan 请求。"""

    interaction = state.get("application_planning_interaction")
    if isinstance(interaction, dict) and interaction:
        return str(interaction.get("request") or "").strip()
    return str(
        state.get("design_change_generation_request")
        or state.get("design_change_request")
        or state.get("request")
        or ""
    ).strip()


def _ensure_technical_lifecycle(state: ProjectState):
    """读取 TechnicalPlan 所属的权威 ApplicationLifecycle。"""

    workspace = workspace_from_state(state)
    if not workspace:
        raise ValueError("TechnicalPlan 必须提供 workspaceRoot。")
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        raise ValueError("TechnicalPlan 缺少权威 ApplicationLifecycle。")
    return workspace, lifecycle


def _boundary_payload_for_state(
    state: ProjectState,
    *,
    boundary: ApplicationPlanningRecoveryBoundary,
    candidate: Any | None = None,
) -> dict[str, Any]:
    """沿用既有 operation identity 生成下一事务边界，避免重复分配操作。"""

    current = _technical_boundary(state)
    baseline = state.get("technical_plan")
    payload = application_planning_boundary_payload(
        operation_id=current.operation_id,
        operation=ApplicationPlanningOperation(current.operation.value),
        boundary=boundary,
        request=_technical_request(state),
        gate_id=current.gate_id,
        artifact_revision=current.artifact_revision,
        baseline=baseline if isinstance(baseline, dict) and baseline else None,
        candidate=candidate,
    )
    # 后续 boundary 必须沿用同一个事务的 request/baseline identity，不能因 State
    # 在 begin 或 commit 期间改变展示字段而重新计算成另一笔操作。
    payload["requestSha256"] = current.request_sha256
    if current.baseline_sha256 is None:
        payload.pop("baselineSha256", None)
    else:
        payload["baselineSha256"] = current.baseline_sha256
    return payload


async def technical_planning_begin(state: ProjectState) -> dict[str, Any]:
    """校验 TechnicalPlan 输入事务并幂等推进到模型生成阶段。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED:
        raise ValueError("TechnicalPlan begin 只能从 INPUT_COMMITTED boundary 开始。")
    workspace, _lifecycle = _ensure_technical_lifecycle(state)
    operation = boundary.operation
    request = _technical_request(state)
    if operation is ApplicationPlanningOperation.REVISE and not request:
        raise ValueError("TechnicalPlan revise 必须提供明确的修改要求。")
    if operation in {
        ApplicationPlanningOperation.REVISE,
        ApplicationPlanningOperation.REPAIR,
    } and boundary.baseline_sha256 is None:
        raise ValueError("TechnicalPlan 修订/修复缺少 baseline 摘要。")
    baseline = state.get("technical_plan")
    if (
        operation in {
            ApplicationPlanningOperation.REVISE,
            ApplicationPlanningOperation.REPAIR,
        }
        and (
            not isinstance(baseline, dict)
            or not baseline
            or application_planning_sha256(baseline) != boundary.baseline_sha256
        )
    ):
        raise ValueError("TechnicalPlan 修订/修复 baseline 与 boundary 不匹配。")

    change_id = str(state.get("change_id") or "").strip()
    if operation is ApplicationPlanningOperation.REVISE and change_id:
        target = state.get("change_target")
        if not isinstance(target, dict) or not target:
            raise ValueError("TechnicalPlan formal revision 缺少 change target。")
        target_model = RevisionTarget.model_validate(target)
        lifecycle = begin_technical_plan_revision_generation(
            workspace,
            change_id=change_id,
            interaction_id=boundary.gate_id or "",
            request=request,
            target=target_model,
            thread_id=str(state.get("active_thread_id") or ""),
            active_run_id=str(state.get("active_run_id") or ""),
        )
    else:
        lifecycle = ensure_technical_plan_generation_lifecycle(
            workspace,
            active_run_id=state.get("active_run_id"),
            thread_id=state.get("active_thread_id"),
            change_id=change_id or None,
        )
    return {
        "phase": "technical_planning_begin",
        "status": "running",
        "resume_from": "",
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            state,
            boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
        ),
        "application_planning_interaction": {},
        "technical_plan_candidate": {},
        "technical_plan_candidate_sha256": "",
        "lifecycle": application_lifecycle_payload(lifecycle),
        "timeline": ["technical_planning_begin"],
    }


def technical_planning_generate(state: ProjectState) -> dict[str, Any]:
    """唯一执行 TechnicalPlan LLM 生成与校验修复的 side-effect-free 节点。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.GENERATION_READY:
        raise ValueError("TechnicalPlan generate 只能从 GENERATION_READY boundary 开始。")
    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        raise ValueError("TechnicalPlan 必须读取已确认的 RequirementSpec。")

    # 仅在模型节点读取规划助手；这些 import 不能移动到 begin/commit，确保副作用边界清晰。
    from app.graph.nodes.planning import (
        _generate_valid_technical_plan,
        _technical_planning_requirement_spec,
    )

    technical_requirement = _technical_planning_requirement_spec(
        state,
        requirement_spec,
    )
    request = _technical_request(state)
    if request:
        technical_requirement = {
            **technical_requirement,
            "planning_adjustment_request": request,
        }
    repair_seed = state.get("technical_plan_repair_candidate")
    repair_errors = state.get("technical_plan_repair_errors")
    existing_plan = (
        deepcopy(repair_seed)
        if boundary.operation is ApplicationPlanningOperation.REPAIR
        and isinstance(repair_seed, dict)
        and repair_seed
        else deepcopy(state.get("technical_plan"))
        if isinstance(state.get("technical_plan"), dict)
        and state.get("technical_plan")
        else None
    )
    plan, errors, failed_candidate = _generate_valid_technical_plan(
        state,
        technical_requirement,
        existing_plan,
        initial_errors=(
            [str(error) for error in repair_errors]
            if boundary.operation is ApplicationPlanningOperation.REPAIR
            and isinstance(repair_errors, list)
            else None
        ),
    )
    if plan is None:
        # 生成失败不写磁盘、不改生命周期；审阅节点会把错误作为当前业务交互展示。
        return {
            "phase": "technical_planning_generate",
            "status": "requires_user_input",
            "technical_plan_repair_candidate": failed_candidate or {},
            "technical_plan_repair_errors": errors[:12],
            "application_planning_recovery_boundary": _boundary_payload_for_state(
                state,
                boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            ),
            "technical_plan_candidate": {},
            "technical_plan_candidate_sha256": "",
            "clarification": _technical_generation_error_payload(errors),
            "timeline": ["technical_planning_generate"],
        }
    candidate = deepcopy(plan)
    candidate_sha256 = application_planning_sha256(candidate)
    return {
        "phase": "technical_planning_generate",
        "status": "running",
        "technical_plan_candidate": candidate,
        "technical_plan_candidate_sha256": candidate_sha256,
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            state,
            boundary=ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED,
            candidate=candidate,
        ),
        "technical_plan_repair_candidate": {},
        "technical_plan_repair_errors": [],
        "timeline": ["technical_planning_generate"],
    }


def _technical_generation_error_payload(errors: list[str]) -> dict[str, Any]:
    """把模型生成失败转换为仅供当前 TechnicalPlan 审阅门消费的错误载荷。"""

    return {
        "mode": "technical_plan_generation_error",
        "status": "requires_user_input",
        "message": "技术规划自动修复后仍未通过校验。",
        "errors": [str(error).strip() for error in errors if str(error).strip()][:8],
        "questions": [],
    }


def technical_planning_commit(state: ProjectState) -> dict[str, Any]:
    """校验候选摘要并幂等原子提交 TechnicalPlan JSON/Markdown。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED:
        raise ValueError("TechnicalPlan commit 只能从 CANDIDATE_COMMITTED boundary 开始。")
    candidate = state.get("technical_plan_candidate")
    candidate_sha256 = str(state.get("technical_plan_candidate_sha256") or "")
    if not isinstance(candidate, dict) or not candidate:
        raise ValueError("TechnicalPlan candidate 缺失，拒绝执行 commit。")
    actual_sha256 = application_planning_sha256(candidate)
    if actual_sha256 != candidate_sha256 or actual_sha256 != boundary.candidate_sha256:
        raise ValueError("TechnicalPlan candidate SHA-256 不匹配，拒绝执行 commit。")
    workspace, lifecycle = _ensure_technical_lifecycle(state)
    if lifecycle.initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
        if lifecycle.initialization.status is not ApplicationLifecycleStatus.RUNNING:
            raise ValueError("TechnicalPlan commit 的生成 lifecycle 不在运行状态。")
    elif not (
        lifecycle.initialization.stage
        is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
        and lifecycle.initialization.status is ApplicationLifecycleStatus.AWAITING_USER
    ):
        raise ValueError("TechnicalPlan commit 的 lifecycle 不在可确认状态。")
    markdown_path, json_path = commit_technical_plan_document(
        state,
        candidate,
        expected_sha256=candidate_sha256,
    )
    if lifecycle.initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            active_run_id=state.get("active_run_id"),
        )
    from app.graph.nodes.planning import _planning_confirmation_payload

    return {
        "phase": "technical_planning",
        "status": "requires_user_input",
        "technical_plan": candidate,
        "project_plan": candidate,
        "technical_plan_path": markdown_path,
        "technical_plan_json_path": json_path,
        "project_plan_path": markdown_path,
        "project_plan_json_path": json_path,
        "clarification": _planning_confirmation_payload(state, candidate),
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            {**state, "technical_plan": candidate},
            boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
            candidate=candidate,
        ),
        "technical_plan_candidate": {},
        "technical_plan_candidate_sha256": "",
        "timeline": ["technical_planning_commit"],
        "lifecycle": application_lifecycle_payload(lifecycle),
    }


def technical_planning_confirm(state: ProjectState) -> dict[str, Any]:
    """确认 TechnicalPlan Markdown 并执行既有 continuation/template 收口逻辑。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED:
        raise ValueError("TechnicalPlan confirm 只能从 ARTIFACT_COMMITTED boundary 开始。")
    plan = state.get("technical_plan")
    if not isinstance(plan, dict) or not plan:
        raise ValueError("TechnicalPlan 正式候选缺失，无法确认。")
    from app.graph.nodes.planning import (
        _attach_technical_plan_contracts,
        _project_plan_dependency_error_payload,
        _project_plan_validation_errors,
    )
    from app.graph.application_planning_revision import cleared_design_change_context
    from app.services.project_plan import apply_project_plan_feedback
    from app.workspace.plan_documents import edited_technical_plan_markdown
    from app.agents.main.document_sync import sync_project_plan_from_markdown

    request = _technical_request(state)
    edited_markdown = edited_technical_plan_markdown(state, plan)
    synchronized = (
        sync_project_plan_from_markdown(
            plan,
            state.get("requirement_spec", {}),
            edited_markdown,
        )
        if edited_markdown is not None
        else plan
    )
    confirmed_plan = {
        **apply_project_plan_feedback(synchronized, request),
        "confirmation_status": "confirmed",
    }
    confirmed_plan = _attach_technical_plan_contracts(state, confirmed_plan)
    validation_errors = _project_plan_validation_errors(confirmed_plan, state)
    if validation_errors:
        operation_id = f"technical-plan-repair-{uuid4().hex}"
        return {
            "phase": "technical_planning_confirm",
            "status": "running",
            "technical_plan": confirmed_plan,
            "technical_plan_repair_candidate": confirmed_plan,
            "technical_plan_repair_errors": validation_errors[:12],
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id=operation_id,
                operation=ApplicationPlanningOperation.REPAIR,
                boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
                request=request,
                gate_id=boundary.gate_id,
                artifact_revision=boundary.artifact_revision,
                baseline=confirmed_plan,
            ),
            "application_planning_review_route": "technical_planning_begin",
            "clarification": _project_plan_dependency_error_payload(validation_errors),
            "timeline": ["technical_planning_confirm"],
        }

    workspace, lifecycle = _ensure_technical_lifecycle(state)
    technical_plan_path, technical_plan_json = commit_technical_plan_document(
        state,
        confirmed_plan,
    )
    confirmation = confirm_application_planning_artifacts(
        {
            **state,
            "technical_plan": confirmed_plan,
            "technical_plan_path": technical_plan_path,
            "technical_plan_json_path": technical_plan_json,
        }
    )
    revision_continuation: dict[str, Any] = {}
    active_revision = lifecycle.active_formal_revision
    if (
        active_revision is not None
        and active_revision.formal_branch.value
        in {"design_stage_revision", "workbench_plan_revision"}
    ):
        from app.graph.application_planning_workflow import _inject_revision_scaffold

        _inject_revision_scaffold(workspace, state)
        token, issued = issue_revision_continuation(
            workspace,
            change_id=active_revision.change_id,
            technical_plan_path=technical_plan_json_path(state),
        )
        lifecycle = load_application_lifecycle(workspace) or lifecycle
        revision_continuation = {
            "changeId": issued.change_id,
            "formalBranch": issued.formal_branch.value,
            "action": "continue_revision_build",
            "token": token,
            "technicalPlanSha256": issued.technical_plan_sha256,
        }
    else:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=state.get("active_run_id"),
        )
    return {
        "phase": "technical_planning_confirm",
        "status": "completed",
        "technical_plan": confirmed_plan,
        "project_plan": confirmed_plan,
        "technical_plan_path": technical_plan_path,
        "technical_plan_json_path": technical_plan_json,
        "application_planning_confirmation": confirmation,
        "revision_continuation": revision_continuation,
        "application_planning_review_route": "",
        "application_planning_interaction": {},
        "lifecycle": application_lifecycle_payload(lifecycle),
        "timeline": ["technical_planning_confirm"],
        **cleared_design_change_context(),
    }


__all__ = [
    "technical_planning_begin",
    "technical_planning_commit",
    "technical_planning_confirm",
    "technical_planning_generate",
]
