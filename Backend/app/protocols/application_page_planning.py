from __future__ import annotations

import inspect
from typing import Any, AsyncIterator, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.application_revision import (
    FormalRevisionBranch,
    StartRevisionRequest,
)
from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.application_planning_interrupt import (
    project_application_planning_interrupt,
)
from app.protocols.application_lifecycle import application_lifecycle_input
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload
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


class ApplicationPlanningRecoveryRequest(BaseModel):
    """校验只读恢复动作需要的工作区和应用定位字段。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["get"]
    workspaceRoot: str = Field(min_length=1)
    applicationId: str | None = None


def application_page_planning_capabilities() -> dict[str, Any]:
    """发布设计阶段、规划阶段及其显式入口门禁的 AG-UI 能力。"""

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
            "intentNode": "design_intent_analysis",
            "targets": ["requirements", "product_planning", "ui_confirmation"],
            "usesOriginalThread": True,
            "incrementalArtifacts": True,
            "existingArtifactsStateField": "design_change_existing_artifacts",
            "resumePrimitive": "langgraph-interrupt-command",
            "formalRevisionAction": "start_design_revision",
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
            "product_plan": "product-plan.v6",
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
        snapshot = await active_graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        result = project_application_planning_interrupt(dict(snapshot.values), snapshot)
        if not result:
            raise ValueError("没有找到可恢复的应用规划 checkpoint。")
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
        error_data=lambda _exc: {"action": "get"},
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
    if action == "start_technical_revision":
        # TechnicalPlan 二次修改恢复原 planning checkpoint，由原节点重新调用模型。
        restart_application_planning_lifecycle(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        )
        next_forwarded["resumeState"] = {
            "state": {
                "technical_plan": {},
                "technical_plan_path": "",
                "technical_plan_json_path": "",
            }
        }
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
            "返回技术规划阶段失败"
            if action == "start_technical_revision"
            else "返回设计阶段失败"
        ),
        error_data=lambda _exc: {"action": action},
        accept=accept,
    )
