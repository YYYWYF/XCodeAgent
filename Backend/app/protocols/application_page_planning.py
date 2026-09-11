from __future__ import annotations

import inspect
import logging
from typing import Any, AsyncIterator, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.application_revision import (
    FormalRevisionBranch,
    StartRevisionRequest,
)
from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.graph.application_planning_revision import (
    technical_plan_revision_reset_state,
)
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.application_planning_interrupt import (
    project_application_planning_interrupt,
)
from app.protocols.application_planning_run_lock import application_planning_run_lock
from app.protocols.application_lifecycle import application_lifecycle_input
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload
from app.services.ui_design_manifest import present_ui_pages
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    load_application_lifecycle,
    restart_application_planning_lifecycle,
)
from app.services.application_revision_lifecycle import submit_revision_impact
from app.services.requirement_spec import (
    SaveRequirementSpecDraftRequest,
    save_requirement_spec_draft,
)


REQUIREMENT_SPEC_DRAFT_EVENT_NAME = "requirement-spec-draft"
logger = logging.getLogger("uvicorn.error")


class ApplicationPlanningCheckpointNotFoundError(RuntimeError):
    """标识只读恢复没有找到目标 application planning checkpoint。"""

    code = "application_planning_checkpoint_not_found"


class ApplicationPlanningRecoveryRequest(BaseModel):
    """校验只读恢复动作需要的工作区和应用定位字段。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["get"]
    workspaceRoot: str = Field(min_length=1)
    applicationId: str | None = None


class ProductStageConversationRequest(BaseModel):
    """校验已完成应用回到产品阶段后的受限 Coordinator 请求。"""

    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=16_000)


def application_page_planning_capabilities() -> dict[str, Any]:
    """发布设计阶段、计划阶段及其显式入口门禁的 AG-UI 能力。"""

    return {
        "name": "application-page-planning",
        "endpoint": "/application-page-planning/run",
        "transport": "ag-ui-sse",
        "eventProtocol": "xcodeagent.workflow.event.v1",
        "stateSnapshotKey": "workflow",
        "customEventName": "workflow-run",
        "recoveryActionField": "forwardedProps.applicationPlanningRecovery",
        "phases": [
            "requirements",
            "product_planning",
            "ui_confirmation",
            "planning_stage_entry",
            "technical_planning",
        ],
        "designChange": {
            "requestField": "forwardedProps.applicationPlanningInteraction",
            "completedProductRequestField": "forwardedProps.productStageConversation",
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
            "usesOriginalThread": True,
            "incrementalArtifacts": True,
            "existingArtifactsStateField": "design_change_existing_artifacts",
            "conversationResultStateField": "productConversationResult",
            "nonMutatingArtifactPresentation": "preserve",
            "resumePrimitive": "langgraph-interrupt-command",
            "formalRevisionAction": "start_design_revision",
            "completedProductConversationAction": "product_stage_conversation",
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
            ".xcodeagent/drafts/specs",
            ".xcodeagent/drafts/plans",
            ".xcodeagent/specs",
            ".xcodeagent/plans",
        ],
        "workspaceGate": "planning-artifacts",
        "mainWorkflowIndependent": True,
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
    product_stage_input = _product_stage_conversation_input(normalized_payload)
    if product_stage_input is not None:
        try:
            normalized_payload = _prepare_product_stage_conversation_payload(
                normalized_payload,
                product_stage_input,
            )
        except Exception as exc:
            return _build_product_stage_conversation_error_stream(
                payload=normalized_payload,
                error=exc,
                accept=accept,
            )
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

        request = ApplicationPlanningRecoveryRequest.model_validate(recovery_input)
        thread_id = str(payload.get("threadId") or "").strip()
        if not thread_id:
            raise ValueError("恢复应用规划必须提供 threadId。")
        active_graph = (
            graph(
                workspace=request.workspaceRoot,
                project_id=request.applicationId,
            )
            if callable(graph)
            else graph
        )
        if inspect.isawaitable(active_graph):
            active_graph = await active_graph
        lock = application_planning_run_lock(thread_id)
        # 与同 thread writer 共用屏障，确保读取发生在在途 Graph 写运行释放锁之后。
        async with lock:
            snapshot = await active_graph.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
            result = project_application_planning_interrupt(
                dict(snapshot.values), snapshot
            )
            if not result:
                raise ApplicationPlanningCheckpointNotFoundError(
                    "没有找到可恢复的应用规划 checkpoint。"
                )
            # UI 确认阶段：后台生成池把最新 page status/code 写进 ui-designs.json，
            # 但 checkpoint 里的 ui_designs 仍停留在入队时的 queued/generating（池不写
            # checkpoint）。recovery 只读 checkpoint 不跑 Graph，若不回填 manifest，
            # 返回的快照里 pages 无 code 且 status 过时，右侧渲染区永远显示「生成中」。
            if str(result.get("phase") or "") == "ui_confirmation":
                manifest = load_ui_designs_json(
                    ui_designs_json_path(dict(snapshot.values))
                )
                if isinstance(manifest, dict) and manifest.get("pages"):
                    result["ui_designs"] = manifest
                    clarification = result.get("clarification")
                    if isinstance(clarification, dict):
                        product_plan = result.get("product_plan")
                        product_plan = (
                            product_plan if isinstance(product_plan, dict) else {}
                        )
                        clarification["pages"] = present_ui_pages(
                            manifest, product_plan
                        )
            lifecycle = load_application_lifecycle(request.workspaceRoot)
        if lifecycle is not None:
            result["lifecycle"] = application_lifecycle_payload(lifecycle)
        recovery_run_id = str(result.get("active_run_id") or f"recovery:{thread_id}")
        visual_payload = _workflow_visual_payload(
            run_id=recovery_run_id,
            thread_id=thread_id,
            summary=_workflow_summary(result, []),
            events=[],
            result=result,
        )
        return AgUiActionResult(
            data=visual_payload,
            message="已恢复待确认的应用规划状态。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="workflow-run",
        state_key="workflow",
        run_id_prefix="application-planning-recovery",
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


def _product_stage_conversation_input(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """读取已完成应用在产品阶段提交的 Coordinator 请求。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    if str(forwarded_props.get("workflowAction") or "").strip() != (
        "product_stage_conversation"
    ):
        return None
    for key in ("resumeFrom", "resume_from", "node"):
        if key in forwarded_props or key in payload:
            raise ValueError("产品阶段对话不接受客户端节点或 resume_from。")
    value = forwarded_props.get("productStageConversation")
    if not isinstance(value, dict):
        raise ValueError("产品阶段对话必须提供 productStageConversation。")
    return value


def _prepare_product_stage_conversation_payload(
    payload: dict[str, Any],
    raw_request: dict[str, Any],
) -> dict[str, Any]:
    """固定复用原 planning thread，并把完成态产品输入限制为只回复轮次。"""

    request = ProductStageConversationRequest.model_validate(raw_request)
    forwarded_props = dict(payload.get("forwardedProps") or {})
    workspace = str(forwarded_props.get("workspaceRoot") or "").strip()
    if not workspace:
        application = forwarded_props.get("application")
        application = application if isinstance(application, dict) else {}
        workspace = str(application.get("workspaceRoot") or "").strip()
    lifecycle = load_application_lifecycle(workspace)
    if (
        lifecycle is None
        or lifecycle.initialization.stage
        != ApplicationLifecycleStage.READY_FOR_WORKBENCH
    ):
        raise ValueError("只有已 ready_for_workbench 的应用可使用完成态产品对话。")
    planning_thread_id = str(lifecycle.initialization.thread_id or "").strip()
    if not planning_thread_id:
        raise ValueError("已完成应用缺少原 application planning thread。")
    next_forwarded = {
        **forwarded_props,
        "workflowAction": None,
        "productStageConversation": None,
        "applicationPlanningInteraction": None,
        "resumeState": {"state": {"product_stage_conversation": True}},
    }
    return {
        **payload,
        "threadId": planning_thread_id,
        "request": request.request,
        "resumeFrom": "design_intent_analysis",
        "forwardedProps": next_forwarded,
    }


def _build_product_stage_conversation_error_stream(
    *,
    payload: dict[str, Any],
    error: Exception,
    accept: str | None,
) -> AsyncIterator[str]:
    """把完成态产品对话边界错误投射为完整 AG-UI 失败生命周期。"""

    async def operation() -> AgUiActionResult:
        """在标准 action stream 内重新抛出已校验的业务错误。"""

        raise error

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="workflow-run",
        state_key="workflow",
        run_id_prefix="product-stage-conversation",
        operation=operation,
        error_message_prefix="产品阶段对话失败",
        error_data=lambda _exc: {"action": "product_stage_conversation"},
        accept=accept,
    )


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
    active = submit_revision_impact(
        workspace,
        interaction_id=request.confirmed_impact.interaction_id,
        decision="approved",
    )
    if active is None:
        raise ValueError("revision impact 未批准。")
    next_forwarded = {
        **forwarded_props,
        "workflowAction": None,
        "revisionRequest": None,
    }
    if action == "start_design_revision":
        # 完成态产品对话标记只约束单次只回复轮次，正式修订恢复时必须消费。
        next_forwarded["resumeState"] = {
            "state": {"product_stage_conversation": False}
        }
    if action == "start_technical_revision":
        # TechnicalPlan 二次修改恢复原 planning checkpoint，由原节点重新调用模型。
        restart_application_planning_lifecycle(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        )
        next_forwarded["resumeState"] = {
            "state": technical_plan_revision_reset_state()
        }
        logger.info(
            "technical_plan_revision_started source=workbench_plan_revision "
            "baseline_present=true request_length=%s",
            len(active.request),
        )
    return {
        **payload,
        "threadId": active.planning_thread_id,
        "request": active.request,
        "resumeFrom": (
            "design_intent_analysis"
            if action == "start_design_revision"
            else "technical_planning"
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
        error_data=lambda _exc: {"action": action},
        accept=accept,
    )
