"""在原规划 AG-UI 端点提供问答、技术修改与上游设计回退。"""

import asyncio
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.technical_planning_conversation import classify_technical_planning_conversation
from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.persistence.execution_recovery import get_execution
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.application_planning_run_lock import application_planning_run_lock
from app.services.application_lifecycle import APPLICATION_PLANNING_ACTIVE_STAGES, load_application_lifecycle
from app.services.execution_recovery_projection import recovery_failure_diagnostic
from app.services.technical_planning_retry import (
    TechnicalPlanningMessageRevision, prepare_technical_planning_retry,
)


class TechnicalPlanningConversationRequest(BaseModel):
    """验证自由输入的动作和原规划身份，禁止客户端选择任意节点。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["classify", "revise", "revise_design"]
    workspaceRoot: str = Field(min_length=1)
    applicationId: str = Field(min_length=1)
    planningThreadId: str = Field(min_length=1)
    request: str = Field(min_length=1, max_length=16000)
    sourceRunId: str | None = None
    checkpointId: str | None = None


async def _current_context(graph, request: TechnicalPlanningConversationRequest):
    """只读原规划 checkpoint 与失败证据，不改变原 Run、确认门或恢复资格。"""

    lifecycle = load_application_lifecycle(request.workspaceRoot)
    if (lifecycle is None or lifecycle.application.id != request.applicationId
            or lifecycle.initialization.thread_id != request.planningThreadId):
        raise ValueError("技术规划对话与当前应用或规划线程不匹配。")
    if lifecycle.initialization.stage not in {
        # 计划对话回退上游后仍使用同一线程；不能把当前产物阶段当成对话入口阶段。
        *APPLICATION_PLANNING_ACTIVE_STAGES,
        # 模板失败仍属于原计划会话，允许提问和发起新的规划修改。
        ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
    }:
        raise ValueError("当前不在技术规划阶段，请使用对应阶段的输入入口。")
    active_graph = await graph(workspace=request.workspaceRoot, project_id=request.applicationId) if callable(graph) else graph
    async with application_planning_run_lock(request.planningThreadId):
        snapshot = await active_graph.aget_state({"configurable": {"thread_id": request.planningThreadId}})
    values = dict(snapshot.values)
    if not values:
        raise ValueError("技术规划状态不可用，请先同步或使用上方重试入口。")
    source = await get_execution(request.workspaceRoot, str(values.get("active_run_id") or ""))
    return snapshot, values, lifecycle, source


def build_technical_planning_conversation_stream(*, graph, payload: dict[str, Any], raw: dict, accept=None) -> AsyncIterator[str]:
    """分类返回 AG-UI 结果，修改沿原线程进入技术生成或设计意图流程。"""

    async def classify() -> AgUiActionResult:
        """用权威规划事实回答问题，并返回仅绑定当前版本的修改意图。"""
        request = TechnicalPlanningConversationRequest.model_validate(raw)
        snapshot, values, lifecycle, source = await _current_context(graph, request)
        # 问答允许没有计划；分类不得把失败候选当成已有技术规划。
        prepared = prepare_technical_planning_retry(values)
        context = {
            "technical_plan": prepared["technical_plan"],
            "requirement_spec": values.get("requirement_spec"),
            "product_plan": values.get("product_plan"),
            "last_generation_request": prepared["request"],
            "execution_status": source.status.value if source else None,
            "failure": recovery_failure_diagnostic(source) if source else None,
            "lifecycle_error": lifecycle.error.model_dump(mode="json") if lifecycle.error else None,
        }
        decision = await asyncio.to_thread(classify_technical_planning_conversation, request.request, context)
        intent, message = decision.intent, decision.message
        if intent == "revise" and not prepared["technical_plan"]:
            intent, message = "reply", "当前尚无技术规划，请使用上方重试入口重新生成；生成后可以在这里提出修改要求。"
        return AgUiActionResult(message=message, data={
            "intent": intent, "message": message,
            "planningThreadId": request.planningThreadId,
            "sourceRunId": values.get("active_run_id"),
            "checkpointId": snapshot.config["configurable"]["checkpoint_id"],
        })

    async def revise() -> AsyncIterator[str]:
        """重新验证分类时的版本后复用原流程；只有技术修改要求已有计划。"""
        from app.protocols.workflow.runtime import build_workflow_ag_ui_stream

        try:
            request = TechnicalPlanningConversationRequest.model_validate(raw)
            snapshot, values, _, _ = await _current_context(graph, request)
            checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
            if (not request.sourceRunId or request.sourceRunId != values.get("active_run_id")
                    or not request.checkpointId or request.checkpointId != checkpoint_id):
                raise ValueError("技术规划状态已变化，请同步最新状态后重新提交修改要求。")
            target_node = "design_intent_analysis" if request.action == "revise_design" else "technical_planning"
            if target_node == "technical_planning":
                prepare_technical_planning_retry(values, new_request=request.request)
        except Exception as exc:
            error = exc

            async def rejected() -> AgUiActionResult:
                """通过完整 AG-UI 生命周期返回修改前置校验失败。"""
                raise error

            async for frame in build_ag_ui_action_stream(
                payload=payload, event_name="technical-planning-conversation",
                state_key="technicalPlanningConversation", run_id_prefix="technical-planning-message",
                operation=rejected, error_message_prefix="技术规划修改失败", accept=accept,
                register_workspace_run=False,
            ):
                yield frame
            return
        normalized = {
            "threadId": request.planningThreadId, "runId": payload.get("runId"),
            "request": request.request, "resumeFrom": target_node,
            "forwardedProps": {"workspaceRoot": request.workspaceRoot, "workflowScope": "application_planning"},
        }
        async for frame in build_workflow_ag_ui_stream(
            graph=graph, payload=normalized, accept=accept,
            technical_planning_revision=TechnicalPlanningMessageRevision(request.request, checkpoint_id, target_node),
        ):
            yield frame

    if isinstance(raw, dict) and raw.get("action") in {"revise", "revise_design"}:
        return revise()
    return build_ag_ui_action_stream(
        payload=payload, event_name="technical-planning-conversation",
        state_key="technicalPlanningConversation", run_id_prefix="technical-planning-message",
        operation=classify, error_message_prefix="技术规划对话失败", accept=accept,
        register_workspace_run=False,
    )
