"""在既有 AG-UI 端点交接阶段线程，只承接服务端状态，不复制旧会话执行历史。"""

from copy import deepcopy
import inspect
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.application_lifecycle import utc_now
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot, project_application_planning_interrupt
from app.protocols.application_planning_run_lock import application_planning_run_lock
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload
from app.services.application_lifecycle import application_lifecycle_payload, load_application_lifecycle, write_application_lifecycle


class PlanningStageHandoffRequest(BaseModel):
    """目标线程由 AG-UI 信封提供，客户端只能声明来源和预期版本。"""

    model_config = ConfigDict(extra="forbid")
    workspaceRoot: str = Field(min_length=1)
    applicationId: str = Field(min_length=1)
    sourceThreadId: str = Field(min_length=1, max_length=512)
    sourceRunId: str = Field(min_length=1, max_length=512)
    lifecycleRevision: int = Field(ge=1)
    targetPhase: Literal["product", "planning"]


def assert_current_planning_thread(workspace: str, thread_id: str) -> None:
    """已交接线程只保留历史，不能再从旧确认门推进同一应用。"""

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle and lifecycle.initialization.thread_id != thread_id:
        raise ValueError("该规划会话已交接，请同步当前阶段后重新提交。")


async def handoff_planning_stage(graph: Any, request: PlanningStageHandoffRequest, target_thread: str) -> dict[str, Any]:
    """校验唯一当前线程，将产物和审阅门交给空白目标线程，最后用 CAS 发布归属。"""

    if not target_thread or target_thread == request.sourceThreadId:
        raise ValueError("阶段交接必须使用不同的 threadId。")
    active_graph = graph(workspace=request.workspaceRoot, project_id=request.applicationId) if callable(graph) else graph
    if inspect.isawaitable(active_graph):
        active_graph = await active_graph
    # 所有交接先锁工作区，再锁来源与目标；普通 writer 只锁其线程，不会形成反向等待。
    async with application_planning_run_lock(f"stage-handoff:{Path(request.workspaceRoot).resolve()}"):
        async with application_planning_run_lock(request.sourceThreadId), application_planning_run_lock(target_thread):
            current = load_application_lifecycle(request.workspaceRoot)
            if current is None or current.application.id != request.applicationId:
                raise ValueError("阶段交接与当前应用不匹配。")
            target_config = {"configurable": {"thread_id": target_thread}}
            existing = await active_graph.aget_state(target_config)
            receipt = dict(existing.values).get("planning_stage_handoff", {})
            identity = request.model_dump()
            if current.initialization.thread_id == target_thread and receipt == identity:
                return _handoff_workflow(existing, current, target_thread)
            if current.initialization.thread_id != request.sourceThreadId or current.revision != request.lifecycleRevision:
                raise ValueError("规划状态已变化，请同步后重新进行阶段交接。")
            pending_impact = current.pending_revision_impact
            if pending_impact and pending_impact.based_on_lifecycle_revision != current.revision:
                raise ValueError("正式修改影响确认已过期，不能通过会话交接恢复旧确认。")
            source = await active_graph.aget_state({"configurable": {"thread_id": request.sourceThreadId}})
            values = dict(source.values)
            if not values or values.get("active_run_id") != request.sourceRunId:
                raise ValueError("阶段交接来源已过期，请同步最新规划状态。")
            from app.persistence.execution_recovery import get_execution
            from app.services.checkpoint_execution import execution_is_live
            execution = await get_execution(request.workspaceRoot, request.sourceRunId)
            pending = application_planning_interrupt_from_snapshot(source)
            if (execution and execution_is_live(execution)) or (
                not pending and current.initialization.status.value in {"running", "stopping"}
                and (not execution or execution.status.value not in {"failed", "interrupted", "stopped", "completed", "cancelled"})
            ):
                raise ValueError("规划仍在执行，请停止或等待本轮结束后再切换会话。")
            if existing.values and receipt != identity:
                raise ValueError("目标 threadId 已有其他执行，不能覆盖历史会话。")
            if request.targetPhase == "planning" and pending and pending.get("phase") != "planning_stage_entry":
                raise ValueError("请先完成需求和 UI 确认，再进入技术规划。")
            # 新线程没有旧模型消息、事件、失败身份或 continuation；正式业务产物由服务端完整传递。
            imported = deepcopy(values)
            for key in ("timeline", "build_events", "test_events", "code_review_events", "code_change_sets", "observability", "lifecycle", "application_planning_interrupt"):
                imported.pop(key, None)
            imported.update(active_thread_id=target_thread, active_run_id=f"stage-{uuid4().hex}",
                            planning_stage_handoff=identity, application_planning_interaction={},
                            resume_from="", resume_execution_run_id="", retry_source_run_id="",
                            workflow_action="", revision_continuation={}, application_planning_confirmation={},
                            design_revision_intent={}, product_conversation_result={}, conversation_response="",
                            product_stage_conversation=False, error=None, technical_plan_repair_source_run_id="")
            if not existing.values:
                conversation_review = (not pending and request.targetPhase == "product"
                    and current.initialization.stage.value != "ready_for_workbench")
                if conversation_review:
                    # 失败执行没有可消费的旧确认；新门只允许自由对话进入意图分析，不能伪造产物确认。
                    imported["clarification"] = {"status": "requires_user_input", "mode": "planning_conversation", "questions": []}
                if pending or conversation_review:
                    # 只运行原生审阅节点来重建 interrupt；不运行任何产物生成或确认逻辑。
                    review = ("technical_planning_review",) if conversation_review else tuple(source.next)
                    if len(review) != 1 or review[0] not in {
                        "requirements_review", "requirement_document_review", "ui_confirmation_review",
                        "planning_stage_entry", "technical_planning_review",
                    }:
                        raise ValueError("阶段交接缺少稳定的原生审阅门。")
                    imported["resume_from"] = review[0]
                    await active_graph.ainvoke(imported, target_config)
                else:
                    # 已结束流程仅保存新的阶段输入，下一次显式修订才会进入真实业务节点。
                    await active_graph.aupdate_state(target_config, imported, as_node="template_reconcile")
            target = await active_graph.aget_state(target_config)
            if pending:
                transferred = application_planning_interrupt_from_snapshot(target)
                if not transferred or any(transferred.get(key) != pending.get(key) for key in ("gateId", "artifact", "artifactRevision")):
                    raise ValueError("新阶段的确认门与来源不一致，交接未提交。")
            revision = current.active_formal_revision
            updated = current.model_copy(update={
                "initialization": current.initialization.model_copy(update={"thread_id": target_thread}),
                "active_run_id": dict(target.values)["active_run_id"],
                "active_formal_revision": revision.model_copy(update={"planning_thread_id": target_thread}) if revision else None,
                # 本次 CAS 只变更执行归属；保持同一待批准影响身份与内容，更新其 lifecycle 基线。
                "pending_revision_impact": pending_impact.model_copy(update={"based_on_lifecycle_revision": current.revision + 1}) if pending_impact else None,
                "revision": current.revision + 1, "updated_at": utc_now(),
            })
            updated = write_application_lifecycle(request.workspaceRoot, updated, expected_revision=current.revision)
            return _handoff_workflow(target, updated, target_thread)


def _handoff_workflow(snapshot: Any, lifecycle: Any, thread_id: str) -> dict[str, Any]:
    """返回与目标原生 checkpoint 一致的 Workflow 和 lifecycle，供冷恢复与后续确认使用。"""

    result = project_application_planning_interrupt(dict(snapshot.values), snapshot)
    result["lifecycle"] = application_lifecycle_payload(lifecycle)
    return _workflow_visual_payload(run_id=result["active_run_id"], thread_id=thread_id,
                                    summary=_workflow_summary(result, []), events=[], result=result)


def build_planning_stage_handoff_stream(*, graph: Any, payload: dict, raw: Any, accept: str | None = None):
    """使用标准 AG-UI 完整生命周期交接，业务失败不伪装成网络断连。"""

    async def operation() -> AgUiActionResult:
        """校验交接输入并返回目标线程的当前确认状态。"""

        request = PlanningStageHandoffRequest.model_validate(raw)
        workflow = await handoff_planning_stage(graph, request, str(payload.get("threadId") or ""))
        return AgUiActionResult(data={"workflow": workflow}, message="阶段会话已交接，原有产物仍按确认门推进。")

    return build_ag_ui_action_stream(payload=payload, event_name="planning-stage-handoff", state_key="planningStageHandoff",
        run_id_prefix="planning-stage-handoff", operation=operation, accept=accept,
        error_message_prefix="阶段会话交接失败", register_workspace_run=False)
