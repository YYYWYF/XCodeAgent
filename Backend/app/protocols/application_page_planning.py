from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.application_revision import (
    FormalRevisionBranch,
    StartRevisionRequest,
)
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.application_lifecycle import application_lifecycle_input
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    load_application_lifecycle,
)
from app.services.requirement_spec import (
    SaveRequirementSpecDraftRequest,
    save_requirement_spec_draft,
)
from app.services.template_reconcile.runtime_v2 import load_current_attempt


REQUIREMENT_SPEC_DRAFT_EVENT_NAME = "requirement-spec-draft"
logger = logging.getLogger("uvicorn.error")


class ApplicationPlanningRecoveryRequest(BaseModel):
    """校验只读恢复动作需要的工作区和应用定位字段。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["get"]
    workspaceRoot: str = Field(min_length=1)
    applicationId: str | None = None



def application_page_planning_capabilities() -> dict[str, Any]:
    """发布设计阶段、计划阶段及其显式入口门禁的 AG-UI 能力。"""

    return {
        "name": "application-page-planning",
        "endpoint": "/application-page-planning/run",
        "transport": "ag-ui-sse",
        "eventProtocol": "devagentstudio.workflow.event.v1",
        "stateSnapshotKey": "workflow",
        "technicalPlanComparison": {
            "baselineField": "technical_plan_baseline",
            "documentReferenceField": "state.artifactRefs.technical_plan_baseline",
            "baselineSource": "current-operation-input",
            "artifactReadEndpoint": "/workflow-artifacts/run",
            "readOnly": True,
        },
        "customEventName": "workflow-run",
        "recoveryActionField": "forwardedProps.applicationPlanningRecovery",
        "stageHandoff": {
            "requestField": "forwardedProps.planningStageHandoff", "customEventName": "planning-stage-handoff",
            "stateSnapshotKey": "planningStageHandoff", "distinctThreadRequired": True,
            "currentThreadField": "initialization.threadId", "iterationField": "initialization.iterationId",
            "sourceVersionRequired": True, "preservesConfirmationGates": True,
        },
        "requirementAuthorization": {
            "requestField": "forwardedProps.requirementAuthorization",
            "action": "preview", "customEventName": "requirement-authorization",
            "stateSnapshotKey": "requirementAuthorization", "readOnly": True,
        },
        "workflowActions": ["retry_template_reconcile"],
        "phases": [
            "requirements",
            "product_planning",
            "ui_confirmation",
            "planning_stage_entry",
            "technical_planning",
            "template_reconcile",
        ],
        "designChange": {
            "requestField": "forwardedProps.applicationPlanningInteraction",
            "conversationTargetField": "conversationTarget",
            "conversationTargets": ["requirements", "ui_confirmation"],
            "targetStartsExecution": True,
            "intentNode": "design_intent_analysis",
            "intents": [
                "chat",
                "read_only",
                "requirement_change",
                "ui_change",
                "clarification",
                "out_of_scope",
            ],
            "changeLevels": ["requirement", "product_behavior", "ui", "none"],
            "targets": ["requirements", "product_planning", "ui_confirmation"],
            "nonMutatingIntents": [
                "chat",
                "read_only",
                "clarification",
                "out_of_scope",
            ],
            "suggestedPhases": ["planning", "development", "test", "none"],
            "stageThreads": "independent",
            "incrementalArtifacts": True,
            "existingArtifactsStateField": "design_change_existing_artifacts",
            "conversationResultStateField": "productConversationResult",
            "nonMutatingArtifactPresentation": "preserve",
            "resumePrimitive": "langgraph-interrupt-command",
            "formalRevisionAction": "start_design_revision",
            "historicalStagesReadOnly": True,
            "completedApplicationChangeEntry": "development",
            "technicalRevisionAction": "start_technical_revision",
            "clientNodeSelectionAllowed": False,
        },
        "confirmationArtifacts": [
            "requirement_spec",
            "product_plan",
            "ui_designs",
            "technical_plan",
        ],
        "clarificationModes": {
            "planningStageEntry": "planning_stage_entry_confirmation",
            "technicalPlanConfirmation": "technical_plan_confirmation",
            "technicalPlanGenerationError": "technical_plan_generation_error",
        },
        "artifactSchemas": {
            "product_plan": "product-plan.v5",
            "ui_designs": "ui-manifest.v3",
            "technical_plan": "technical-plan",
        },
        "uiDesignActions": ["select_template", "regenerate", "adjust_pages", "skip"],
        "editableArtifacts": {
            "requirement_spec": {
                "requestField": "forwardedProps.editedRequirementSpec",
                "saveActionField": "forwardedProps.requirementSpecDraft",
                "actions": ["save"],
                "writes": [
                    "drafts/specs/requirement-spec.md",
                    "drafts/specs/requirement-spec.json",
                ],
                "promotesTo": [
                    "specs/requirement-spec.md",
                    "specs/requirement-spec.json",
                ],
            }
        },
        "draftArtifacts": {
            "product_plan": {
                "writes": [
                    "drafts/plans/product-plan.md",
                    "drafts/plans/product-plan.json",
                ],
                "promotesTo": [
                    "plans/product-plan.md",
                    "plans/product-plan.json",
                ],
            }
        },
        "writesApplicationJsonAfterConfirmation": False,
        "artifactDirectories": [
            ".devagentstudio/drafts/specs",
            ".devagentstudio/drafts/plans",
            ".devagentstudio/specs",
            ".devagentstudio/plans",
        ],
        "workspaceGate": "planning-artifacts",
        "mainWorkflowIndependent": True,
        "technicalPlanningConversation": {
            "requestField": "forwardedProps.technicalPlanningConversation",
            "actions": ["classify", "prepare_revision", "revise", "revise_design"],
            "conversationTargetField": "conversationTarget",
            "conversationTargets": ["technical_planning", "requirements", "ui_confirmation"],
            "targetStartsExecution": True,
            "conversationPhases": ["product", "planning"],
            "modelThreadSource": "threadId",
            "upstreamRevisionRequiresStageHandoff": True,
            "readOnlyClassification": True,
            "revisionRequiresExistingPlan": True,
            "explicitTechnicalTargetAllowsInitialGeneration": True,
            "revisionRequiresCurrentSourceRunAndCheckpoint": True,
            "generationNode": "technical_planning_generate",
        },
    }


def build_application_page_planning_ag_ui_stream(
    *,
    graph: Callable[..., Any],
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """使用主 Workflow 的稳定 AG-UI 投射运行独立两节点 Graph。"""

    # 专用端点的范围由服务端固定，确保重启或恢复时仍从创建规划节点续跑。
    normalized_payload = {
        **payload,
        "workflowScope": "application_planning",
    }
    stage_handoff = (payload.get("forwardedProps") or {}).get("planningStageHandoff")
    if stage_handoff is not None:
        from app.protocols.planning_stage_handoff import build_planning_stage_handoff_stream

        return build_planning_stage_handoff_stream(graph=graph, payload=normalized_payload, raw=stage_handoff, accept=accept)
    authorization_preview = (payload.get("forwardedProps") or {}).get("requirementAuthorization")
    if authorization_preview is not None:
        from app.protocols.requirement_authorization import build_requirement_authorization_stream

        return build_requirement_authorization_stream(payload=normalized_payload, raw=authorization_preview, accept=accept)
    technical_message = (payload.get("forwardedProps") or {}).get("technicalPlanningConversation")
    if technical_message is not None:
        from app.protocols.technical_planning_conversation import build_technical_planning_conversation_stream

        return build_technical_planning_conversation_stream(
            graph=graph, payload=normalized_payload, raw=technical_message, accept=accept,
        )
    forwarded = normalized_payload.get("forwardedProps") or {}
    if forwarded.get("workflowAction") == "product_stage_conversation":
        async def reject_historical_conversation() -> AgUiActionResult:
            """历史需求阶段仅可查看，正式修改必须由开发会话的影响确认进入。"""

            raise ValueError("需求阶段仅供查看历史会话。如需新增或调整需求，请在开发阶段提出。")

        return build_ag_ui_action_stream(payload=normalized_payload, event_name="workflow-run", state_key="workflow",
            run_id_prefix="historical-planning", operation=reject_historical_conversation, accept=accept,
            error_message_prefix="历史阶段不可发起对话", register_workspace_run=False)
    start_design_revision = _start_design_revision_input(normalized_payload)
    if start_design_revision is not None:
        try:
            normalized_payload = _prepare_start_design_revision_payload(
                normalized_payload,
                start_design_revision,
            )
        except Exception as exc:
            return _build_start_design_revision_error_stream(
                payload=normalized_payload,
                error=exc,
                action=str(
                    (normalized_payload.get("forwardedProps") or {}).get(
                        "workflowAction"
                    )
                    or "start_design_revision"
                ),
                accept=accept,
            )
    try:
        retry_template_reconcile = _retry_template_reconcile_input(normalized_payload)
    except Exception as exc:
        return _build_start_design_revision_error_stream(
            payload=normalized_payload,
            error=exc,
            action="retry_template_reconcile",
            accept=accept,
        )
    if retry_template_reconcile:
        try:
            normalized_payload = _prepare_retry_template_reconcile_payload(
                normalized_payload,
            )
        except Exception as exc:
            return _build_start_design_revision_error_stream(
                payload=normalized_payload,
                error=exc,
                action="retry_template_reconcile",
                accept=accept,
            )
    draft_input = _requirement_spec_draft_input(normalized_payload)
    if draft_input is not None:
        return _build_requirement_spec_draft_ag_ui_stream(
            payload=normalized_payload,
            draft_input=draft_input,
            accept=accept,
        )
    recovery_input = _application_planning_recovery_input(normalized_payload)
    if recovery_input is not None:
        return _build_application_planning_recovery_ag_ui_stream(
            graph=graph,
            payload=normalized_payload,
            recovery_input=recovery_input,
            accept=accept,
        )
    if application_lifecycle_input(normalized_payload) is not None:
        return _build_unsupported_lifecycle_ag_ui_stream(
            payload=normalized_payload,
            accept=accept,
        )
    return build_workflow_ag_ui_stream(
        graph=graph,
        payload=normalized_payload,
        accept=accept,
    )


def _build_application_planning_recovery_ag_ui_stream(
    *,
    graph: Callable[..., Any],
    payload: dict[str, Any],
    recovery_input: dict[str, Any],
    accept: str | None,
) -> AsyncIterator[str]:
    """只读投影同一线程 checkpoint，恢复确认卡但不执行 Graph。"""

    async def operation() -> AgUiActionResult:
        """读取 checkpoint 与权威 lifecycle，并返回标准 Workflow 快照。"""

        from app.services.recovery_snapshot import project_recovery_snapshot
        request = ApplicationPlanningRecoveryRequest.model_validate(recovery_input)
        thread_id = str(payload.get("threadId") or "").strip()
        if not thread_id:
            raise ValueError("恢复应用规划必须提供 threadId。")
        snapshot = await project_recovery_snapshot(workspace=request.workspaceRoot,
            application_id=request.applicationId, thread_id=thread_id, planning=True, graph=graph)
        lifecycle = load_application_lifecycle(request.workspaceRoot)
        return AgUiActionResult(data={"recoverySnapshot": snapshot,
            "lifecycle": application_lifecycle_payload(lifecycle) if lifecycle else None},
            message=snapshot["workflow"]["summary"].get("message", "已同步当前规划。") if snapshot["workflow"] else "当前没有执行存档。")

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="workflow-run",
        state_key="workflow",
        run_id_prefix="application-planning-recovery",
        register_workspace_run=False,
        operation=operation,
        error_message_prefix="恢复应用规划失败",
        error_data=lambda exc: {
            "action": "get",
            "code": getattr(
                exc,
                "code",
                "application_planning_recovery_failed",
            ),
        },
        accept=accept,
    )



def _build_requirement_spec_draft_ag_ui_stream(
    *,
    payload: dict[str, Any],
    draft_input: dict[str, Any],
    accept: str | None,
) -> AsyncIterator[str]:
    """把需求草稿保存投射为不续跑 Graph 的完整 AG-UI 生命周期。"""

    async def operation() -> AgUiActionResult:
        """校验草稿、同步正式文档并返回新的前端展示状态。"""

        request = SaveRequirementSpecDraftRequest.model_validate(draft_input)
        result = save_requirement_spec_draft(request)
        return AgUiActionResult(
            data={"action": "save", **result},
            message="需求文档修改已保存。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=REQUIREMENT_SPEC_DRAFT_EVENT_NAME,
        state_key="requirementSpecDraft",
        run_id_prefix="requirement-spec-draft",
        operation=operation,
        error_message_prefix="需求文档保存失败",
        error_data=lambda _exc: {"action": "save"},
        accept=accept,
    )


def _build_unsupported_lifecycle_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None,
) -> AsyncIterator[str]:
    """用完整 AG-UI 失败生命周期拒绝旧端点上的 lifecycle 动作。"""

    async def operation() -> AgUiActionResult:
        """阻止 lifecycle 载荷误触发应用规划 Graph。"""

        raise ValueError(
            "applicationLifecycle 动作仅支持 /application-lifecycle/run。"
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="workflow-run",
        state_key="workflow",
        run_id_prefix="application-page-planning",
        operation=operation,
        error_message_prefix="应用规划请求失败",
        error_data=lambda _exc: {"action": "unsupported_application_lifecycle"},
        accept=accept,
    )


def _requirement_spec_draft_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 读取可选的需求草稿保存请求。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("requirementSpecDraft")
    return value if isinstance(value, dict) else None


def _application_planning_recovery_input(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 读取可选的只读 checkpoint 恢复动作。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("applicationPlanningRecovery")
    return value if isinstance(value, dict) else None


def _start_design_revision_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """读取设计或技术规划回退 action 的 revisionRequest，不接受客户端节点字段。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    action = str(forwarded_props.get("workflowAction") or "").strip()
    if action not in {"start_design_revision", "start_technical_revision"}:
        return None
    for key in ("resumeFrom", "resume_from", "node"):
        if key in forwarded_props or key in payload:
            raise ValueError("revision action 不接受客户端节点或 resume_from。")
    value = forwarded_props.get("revisionRequest")
    if not isinstance(value, dict):
        raise ValueError("revision action 必须提供 revisionRequest。")
    return value


def _retry_template_reconcile_input(payload: dict[str, Any]) -> bool:
    """识别模板更新重试意图，并拒绝客户端选择 Graph 节点。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return False
    if str(forwarded_props.get("workflowAction") or "").strip() != "retry_template_reconcile":
        return False
    for key in ("resumeFrom", "resume_from", "node"):
        if key in forwarded_props or key in payload:
            raise ValueError("retry_template_reconcile 不接受客户端节点或 resume_from。")
    return True


def _prepare_retry_template_reconcile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """按权威 lifecycle 重建失败模板更新的唯一线程、请求和受控恢复入口。"""

    forwarded_props = dict(payload.get("forwardedProps") or {})
    workspace = str(forwarded_props.get("workspaceRoot") or "").strip()
    if not workspace:
        application = forwarded_props.get("application")
        workspace = str(application.get("workspaceRoot") or "").strip() if isinstance(application, dict) else ""
    if not workspace:
        raise ValueError("retry_template_reconcile 必须提供 workspaceRoot。")
    lifecycle = load_application_lifecycle(workspace)
    active = lifecycle.active_formal_revision if lifecycle is not None else None
    if active is None or active.status != "template_reconcile_failed":
        raise ValueError("当前没有可重试的 Template Reconcile 失败记录。")
    plan_path = Path(workspace) / WORKSPACE_ARTIFACT_DIR / "plans" / "technical-plan.json"
    try:
        technical_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("已确认的 TechnicalPlan 不可读取，不能重试模板更新。") from exc
    if not isinstance(technical_plan, dict) or technical_plan.get("artifact_type") != "technical-plan" or technical_plan.get("confirmation_status") != "confirmed":
        raise ValueError("TechnicalPlan 尚未确认，不能重试模板更新。")
    attempt = load_current_attempt(workspace)
    if attempt is None or attempt.status != "FAILED" or attempt.phase != "FAILED":
        raise ValueError("当前没有可重试的失败 Template Reconcile Attempt。")
    return {
        **payload,
        "threadId": active.planning_thread_id,
        "request": active.request,
        "forwardedProps": {
            **forwarded_props,
            "workspaceRoot": workspace,
            "workflowAction": "retry_template_reconcile",
        },
    }


def _prepare_start_design_revision_payload(
    payload: dict[str, Any],
    raw_request: dict[str, Any],
) -> dict[str, Any]:
    """校验 impact 绑定并从 lifecycle 选择原 planning thread 和固定意图入口。"""

    request = StartRevisionRequest.model_validate(raw_request)
    action = str((payload.get("forwardedProps") or {}).get("workflowAction") or "").strip()
    expected_branch = (
        FormalRevisionBranch.DESIGN_STAGE_REVISION
        if action == "start_design_revision"
        else FormalRevisionBranch.WORKBENCH_PLAN_REVISION
    )
    if request.formal_branch != expected_branch:
        raise ValueError(f"{action} 与 formal revision branch 不匹配。")
    forwarded_props = dict(payload.get("forwardedProps") or {})
    workspace = str(forwarded_props.get("workspaceRoot") or "").strip()
    if not workspace:
        application = forwarded_props.get("application")
        application = application if isinstance(application, dict) else {}
        workspace = str(application.get("workspaceRoot") or "").strip()
    lifecycle = load_application_lifecycle(workspace)
    pending = lifecycle.pending_revision_impact if lifecycle is not None else None
    if pending is None:
        raise ValueError("没有可消费的 revision impact confirmation。")
    if pending.request != request.request:
        raise ValueError("revisionRequest 不能覆盖 impact 绑定的原始请求。")
    if pending.target != request.target:
        raise ValueError("revisionRequest target 与 impact 绑定目标不匹配。")
    if pending.impact.formal_branch != request.formal_branch:
        raise ValueError("revisionRequest branch 与 impact 绑定分支不匹配。")
    if pending.interaction_id != request.confirmed_impact.interaction_id:
        raise ValueError("revisionRequest interactionId 与 impact 绑定不匹配。")
    next_forwarded = {
        **forwarded_props,
        "workflowAction": None,
        "revisionRequest": None,
    }
    if action == "start_design_revision":
        # 完成态产品对话标记只约束单次只回复轮次，正式修订恢复时必须消费。
        planning_thread_id = str(lifecycle.initialization.thread_id or "").strip()
        if not planning_thread_id:
            raise ValueError("设计修订缺少原 application planning thread。")
        # 只冻结批准凭据；影响消费必须在 Graph durable input 之后，避免批准成功却没有可恢复 Run。
        next_forwarded["_designRevisionIntent"] = {
            "changeId": pending.change_id,
            "interactionId": pending.interaction_id,
        }
        return {
            **payload,
            "threadId": planning_thread_id,
            "request": pending.request,
            "resumeFrom": "design_intent_analysis",
            "forwardedProps": next_forwarded,
        }
    if action == "start_technical_revision":
        # TechnicalPlan 二次修改只把 admission credential 交给 begin 节点，
        # approval 和 lifecycle rewind 必须在 Graph 的 durable input 之后发生。
        planning_thread_id = str(lifecycle.initialization.thread_id or "").strip()
        if not planning_thread_id:
            raise ValueError("技术规划修订缺少原 application planning thread。")
        next_forwarded["_technicalRevisionIntent"] = {
            "changeId": pending.change_id,
            "interactionId": request.confirmed_impact.interaction_id,
        }
        logger.info(
            "technical_plan_revision_started source=workbench_plan_revision "
            "baseline_present=true request_length=%s",
            len(pending.request),
        )
    return {
        **payload,
        "threadId": (
            planning_thread_id
            if action == "start_technical_revision"
            else str(lifecycle.initialization.thread_id or "").strip()
        ),
        "request": pending.request,
        "resumeFrom": (
            "design_intent_analysis"
            if action == "start_design_revision"
            else "technical_planning_begin"
        ),
        "forwardedProps": next_forwarded,
    }


def _build_start_design_revision_error_stream(
    *,
    payload: dict[str, Any],
    error: Exception,
    action: str,
    accept: str | None,
) -> AsyncIterator[str]:
    """把可预期的 design handoff 拒绝包装为完整 AG-UI 失败生命周期。"""

    async def operation() -> AgUiActionResult:
        """在标准 action stream 内重新抛出已校验的业务错误。"""

        raise error

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="workflow-run",
        state_key="workflow",
        run_id_prefix="start-design-revision",
        operation=operation,
        error_message_prefix=(
            "返回技术计划阶段失败"
            if action == "start_technical_revision"
            else "返回设计阶段失败"
        ),
        # 此端点由 Workflow 客户端消费，入场失败同样提供可识别的终态投影。
        error_data=lambda exc: {
            "action": action,
            "summary": {
                "status": "failed",
                "phase": "design_intent_analysis" if action == "start_design_revision" else "technical_planning",
                "message": f"{type(exc).__name__}: {exc}",
            },
            "events": [],
            "state": {"workflow_scope": "application_planning"},
        },
        accept=accept,
    )
