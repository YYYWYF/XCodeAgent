"""自由对话独立 LangGraph 的 AG-UI 协议适配器。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.workspace_scope import resolve_workspace_root
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.graph.direct_modification_workflow import (
    direct_modification_graph_for_request,
    direct_next_node_name,
)
from app.persistence.checkpoints import cleanup_workflow_checkpoints
from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    ProgressReporter,
    TextDeltaReporter,
    build_ag_ui_action_stream,
)
from app.protocols.direct_modification_projection import (
    DIRECT_NODE_PERCENT,
    direct_final_payload,
    direct_node_event,
    direct_node_process_step,
    direct_node_running_process_step,
    direct_node_started_event,
    direct_progress_payload,
)
from app.protocols.workflow.run_control import (
    build_workflow_cancellation_ag_ui_stream,
    workflow_run_registry,
)
from app.protocols.workflow.stream_events import (
    integration_test_check_summary,
    integration_test_checks,
)
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    load_application_lifecycle,
)
from app.services.application_revision_lifecycle import (
    register_revision_impact,
    submit_revision_impact,
)
from app.services.user_skill_runtime import validate_selected_user_skills
from app.workspace.run_lease import WorkspaceRunLease, workspace_run_leases


CONVERSATION_EVENT_NAME = "conversation"
CONVERSATION_STATE_KEY = "conversation"

_DIRECT_CONFIRMATION_MODES = {
    "implementation_fix_confirmation",
    "small_task_scope_confirmation",
}

_DIRECT_CONTINUATION_STATE_KEYS = (
    "conversation_intent",
    "conversation_response",
    "change_impact_enabled",
    "change_impact_analysis",
    "change_impact_context",
    "change_impact_code_scan_required",
    "change_impact_code_scan",
    "direct_modification_owner",
    "direct_modification_scope",
    "direct_modification_confidence",
    "direct_modification_reason",
    "direct_modification_summary",
    "direct_modification_target_paths",
    "direct_modification_approved_paths",
    "direct_modification_result",
    "direct_stage_results",
    "direct_code_change_sets",
    "backend_handoff",
    "repair_iteration",
    "max_repair_iterations",
    "repair_task_plan",
    "repair_tasks",
    "small_task_tasks",
    "small_task_results",
    "small_task_code_change_sets",
    "small_task_handoff",
    "small_task_handoff_submission",
    "small_task_route",
    "test_results",
    "test_report",
    "test_report_path",
    "test_report_json_path",
    "quality_gate_passed",
    "integration_next_action",
    "workspace_snapshot_summary",
    "workspace_snapshot_path",
    "workspace_snapshot_hash",
    "workspace_revision",
    "code_graph_index",
)


class DirectModificationTarget(BaseModel):
    """校验自由协作当前页面或接口目标，保留无目标对话能力。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: Literal["page", "endpoint"]
    page_id: str | None = Field(default=None, alias="pageId", max_length=512)
    api_contract_id: str | None = Field(
        default=None,
        alias="apiContractId",
        max_length=512,
    )
    endpoint_id: str | None = Field(default=None, alias="endpointId", max_length=512)

    @model_validator(mode="after")
    def validate_target_identifiers(self) -> "DirectModificationTarget":
        """要求页面和接口目标分别携带完整且非空的稳定标识。"""

        if self.type == "page" and not str(self.page_id or "").strip():
            raise ValueError("页面目标必须提供 pageId。")
        if self.type == "endpoint" and (
            not str(self.api_contract_id or "").strip()
            or not str(self.endpoint_id or "").strip()
        ):
            raise ValueError("接口目标必须提供 apiContractId 和 endpointId。")
        return self


class DirectModificationInput(BaseModel):
    """校验快速修改 AG-UI forwardedProps 的最小业务参数。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    selected_skill_names: list[str] = Field(
        default_factory=list,
        alias="selectedSkillNames",
        max_length=64,
    )
    original_request: str | None = Field(
        default=None,
        alias="originalRequest",
        max_length=16_000,
    )
    target: DirectModificationTarget | None = None
    change_id: str | None = Field(default=None, alias="changeId", max_length=256)
    approved_paths: list[str] = Field(
        default_factory=list,
        alias="approvedPaths",
        max_length=100,
    )
    handoff_decision: str | None = Field(
        default=None,
        alias="handoffDecision",
        max_length=32,
    )
    impact_interaction_id: str | None = Field(
        default=None,
        alias="impactInteractionId",
        max_length=256,
    )


def conversation_capabilities() -> dict[str, Any]:
    """发布自由对话 Graph 的 AG-UI 能力元数据。"""

    return {
        "name": "conversation",
        "endpoint": "/conversation/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.conversation",
        "customEventName": CONVERSATION_EVENT_NAME,
        "stateSnapshotKey": CONVERSATION_STATE_KEY,
        "intents": [
            "casual_chat",
            "workspace_question",
            "clarification",
            "implementation_fix",
            "formal_revision",
        ],
        "owners": ["frontend", "backend", "fullstack", "workspace", "none", "unknown"],
        "statuses": [
            "in_progress",
            "completed",
            "requires_user_input",
            "failed",
        ],
        "formalRevision": {
            "impactConfirmationMode": "revision_impact_confirmation",
            "branches": ["design_stage_revision", "workbench_plan_revision"],
            "clientNodeSelectionAllowed": False,
            "additionalModelImpactAnalysis": False,
            "userVisibleExplanation": "reason-only",
        },
        "workflowIndependent": True,
        "targetRequired": False,
        "target": {
            "optional": True,
            "types": {
                "page": ["pageId"],
                "endpoint": ["apiContractId", "endpointId"],
            },
        },
        "changeIdSupported": True,
        "conversationSummaryMaxChars": 4_000,
        "automaticRepair": {
            "enabled": True,
            "node": "direct_modification_repair",
            "maxIterations": 3,
            "retryAfter": "integration_test",
        },
        "executionPolicy": {
            "subagentsEnabled": False,
            "todoPlanningEnabled": False,
            "implementationFixConfirmation": {
                "requiredOwners": ["frontend", "backend", "fullstack"],
                "workspaceOwnerBypasses": True,
                "mode": "implementation_fix_confirmation",
            },
            "confirmationContinuation": {
                "source": "server-checkpoint",
                "modes": [
                    "implementation_fix_confirmation",
                    "small_task_scope_confirmation",
                ],
                "skips": ["scan_workspace_code", "classify_intent"],
            },
        },
    }


def conversation_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 读取自由对话参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("conversation")
    return value if isinstance(value, dict) else None


def _direct_confirmation_continuation(
    *,
    request: DirectModificationInput,
    thread_id: str,
    checkpoint_values: Any,
) -> dict[str, Any] | None:
    """校验确认动作并从同一会话 checkpoint 恢复服务端权威的修改上下文。"""

    decision = str(request.handoff_decision or "").strip().lower()
    if decision not in {"approved", "rejected"}:
        return None
    values = checkpoint_values if isinstance(checkpoint_values, dict) else {}
    clarification = values.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    mode = str(clarification.get("mode") or "").strip()
    # 正式 revision 的 rejected 也复用 handoffDecision，但它必须由 impact
    # interaction 回写逻辑处理，不能被误当成实现修复续跑。
    if mode not in _DIRECT_CONFIRMATION_MODES:
        if decision == "rejected" and str(request.impact_interaction_id or "").strip():
            return None
        raise ValueError("确认续跑找不到匹配的实现修改确认 checkpoint。")
    if values.get("status") != "requires_user_input":
        raise ValueError("实现修改确认已过期，不能继续写入。")
    if values.get("conversation_intent") != "implementation_fix":
        raise ValueError("确认 checkpoint 的修改意图已变化，不能继续写入。")
    owner = str(values.get("direct_modification_owner") or "").strip()
    if owner not in {"frontend", "backend", "fullstack"}:
        raise ValueError("确认 checkpoint 缺少有效的代码修改归属。")
    saved_thread_id = str(values.get("active_thread_id") or "").strip()
    if saved_thread_id and saved_thread_id != thread_id:
        raise ValueError("确认动作与原始会话不匹配。")

    saved_request = str(values.get("request") or "").strip()
    supplied_original = str(request.original_request or "").strip()
    if not saved_request:
        raise ValueError("确认 checkpoint 缺少原始修改请求。")
    if supplied_original and supplied_original != saved_request:
        raise ValueError("确认动作与原始修改请求不匹配。")

    saved_target = values.get("change_target")
    saved_target = saved_target if isinstance(saved_target, dict) else {}
    supplied_target = (
        request.target.model_dump(by_alias=True, exclude_none=True)
        if request.target is not None
        else {}
    )
    if saved_target and supplied_target and saved_target != supplied_target:
        raise ValueError("确认动作与原始页面或接口目标不匹配。")
    target = saved_target or supplied_target

    submitted_paths = _safe_approved_paths(request.approved_paths)
    checkpoint_paths = _safe_approved_paths(values.get("direct_modification_approved_paths"))
    requested_paths = _safe_approved_paths(clarification.get("requestedPaths"))
    allowed_paths = set(requested_paths) | set(checkpoint_paths)
    if any(path not in allowed_paths for path in submitted_paths):
        raise ValueError("确认动作包含未被上一轮确认卡授权的文件路径。")
    approved_paths = list(dict.fromkeys([*checkpoint_paths, *submitted_paths]))[:100]

    continuation: dict[str, Any] = {
        key: values[key]
        for key in _DIRECT_CONTINUATION_STATE_KEYS
        if key in values
    }
    continuation.update(
        {
            "request": saved_request,
            "active_thread_id": thread_id,
            "change_target": target,
            "direct_modification_handoff_decision": decision,
            "direct_modification_approved_paths": approved_paths,
            "clarification": {},
            "timeline": [],
        }
    )
    if decision == "rejected":
        continuation.update(
            {
                "status": "failed",
                "phase": "finalize_direct_modification",
                "message": "用户已取消本次修改确认，本次工作区不会继续写入。",
                "direct_modification_resume_node": "finalize_direct_modification",
            }
        )
        return continuation

    owner_node = {
        "frontend": "execute_frontend",
        "backend": "execute_backend",
        "fullstack": "execute_backend",
    }[owner]
    resume_node = owner_node
    if (
        mode == "implementation_fix_confirmation"
        and values.get("change_impact_code_scan_required") is True
    ):
        resume_node = "scan_change_impact_code"
    continuation.update(
        {
            "status": "in_progress",
            "phase": resume_node,
            "message": "用户已确认实现修改范围，继续执行原修改。",
            "direct_modification_resume_node": resume_node,
        }
    )
    return continuation


def build_conversation_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """执行自由对话 Graph，并投射完整 AG-UI 生命周期和进度。"""

    thread_id = str(payload.get("threadId") or uuid4())
    run_id = str(payload.get("runId") or f"conversation-{uuid4().hex[:12]}")
    normalized_payload = {**payload, "threadId": thread_id, "runId": run_id}
    cancel_run_id = _cancel_run_id(normalized_payload)
    if cancel_run_id:
        return build_workflow_cancellation_ag_ui_stream(
            thread_id=thread_id,
            run_id=run_id,
            target_run_id=cancel_run_id,
            accept=accept,
        )
    raw_input = conversation_input(normalized_payload)
    if raw_input is None:
        raise ValueError("缺少 forwardedProps.conversation。")

    streamed_text = False

    async def operation(report: ProgressReporter, report_text: TextDeltaReporter) -> AgUiActionResult:
        """运行独立 Graph，并把节点、工具和测试进度送入 AG-UI 队列。"""

        async def forward_text_delta(delta: str) -> None:
            """转发已经从模型响应 JSON 中提取出的助手正文增量。"""

            nonlocal streamed_text
            if not delta:
                return
            streamed_text = True
            await report_text(delta)

        request = DirectModificationInput.model_validate(raw_input)
        latest_user_request = _last_user_message(normalized_payload.get("messages"))
        # 普通澄清需要合并原始需求和本轮回答；确认续跑稍后会用服务端
        # checkpoint 中的原始请求覆盖确认按钮文案，避免再次触发自然语言分类。
        user_request = _conversation_request(
            original_request=request.original_request,
            latest_user_request=latest_user_request,
        )
        if not user_request:
            raise ValueError("自由对话请求必须包含一条用户消息。")
        validate_selected_user_skills(request.selected_skill_names)
        resolved_workspace = resolve_workspace_root(request.workspace_root)
        if resolved_workspace is None:
            raise ValueError("自由对话请求必须提供有效的 workspaceRoot。")
        workspace_root = str(resolved_workspace)

        formal_revision_rejected = (
            request.handoff_decision == "rejected"
            and bool(str(request.impact_interaction_id or "").strip())
        )
        # 正式 revision 拒绝需要回写 impact；实现修复和 SmallTask 范围拒绝由 Graph 收口。
        if formal_revision_rejected:
            submit_revision_impact(
                workspace_root,
                interaction_id=str(request.impact_interaction_id),
                decision="rejected",
            )
        await cleanup_workflow_checkpoints(workspace=workspace_root)
        active_graph = await direct_modification_graph_for_request(
            workspace=workspace_root
        )
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("快速修改必须运行在异步任务中。")
        workflow_run_registry.register(run_id, current_task)
        lease: WorkspaceRunLease | None = None
        events: list[dict[str, Any]] = []
        state_view: dict[str, Any] = {
            "request": user_request,
            "workspace": workspace_root,
            "selected_skill_names": list(request.selected_skill_names),
            "active_thread_id": thread_id,
            "active_run_id": run_id,
            "change_id": str(request.change_id or ""),
            "change_target": (
                request.target.model_dump(by_alias=True, exclude_none=True)
                if request.target is not None
                else {}
            ),
            # 二次修改由一次分类 JSON 决定路由，不再追加 Contract Evidence 模型分析。
            "change_impact_enabled": False,
            "change_impact_analysis": {},
            "change_impact_code_scan_required": False,
            "change_impact_code_scan": {},
            "direct_modification_approved_paths": _safe_approved_paths(request.approved_paths),
            "direct_modification_handoff_decision": str(request.handoff_decision or ""),
            "direct_modification_resume_node": "",
            "integration_repair_enabled": False,
            "frontend_performance_test_enabled": False,
            "repair_iteration": 0,
            "max_repair_iterations": 3,
            "repair_task_plan": {},
            "repair_tasks": [],
            "small_task_tasks": [],
            "small_task_results": [],
            "small_task_code_change_sets": [],
            "small_task_handoff": {},
            "small_task_handoff_submission": {},
            "small_task_route": "",
            "timeline": [],
        }
        if formal_revision_rejected:
            # 影响范围卡的取消是成功的用户决策：直接从收口节点结束，
            # 既不重扫工作区/重做意图识别，也不把取消投影为运行失败。
            state_view.update(
                {
                    "request": str(request.original_request or user_request).strip(),
                    "phase": "finalize_direct_modification",
                    "status": "completed",
                    "message": "已取消本次正式修改，当前正式产物保持不变。",
                    "conversation_intent": "formal_revision",
                    "direct_modification_owner": "none",
                    "direct_modification_scope": "formal",
                    "direct_modification_resume_node": "finalize_direct_modification",
                    "clarification": {},
                }
            )
        persisted_revision_impact_ids: set[str] = set()
        config = {
            "configurable": {
                "thread_id": f"conversation:{thread_id}",
            },
            "run_name": "xcodeagent-conversation",
            "tags": ["xcodeagent", "conversation"],
            "metadata": {
                "run_id": run_id,
                "thread_id": thread_id,
                "workspace": workspace_root,
                "selected_skill_names": list(request.selected_skill_names),
            },
        }
        try:
            if (
                request.handoff_decision in {"approved", "rejected"}
                and not formal_revision_rejected
            ):
                checkpoint = await active_graph.aget_state(config)
                continuation = _direct_confirmation_continuation(
                    request=request,
                    thread_id=thread_id,
                    checkpoint_values=getattr(checkpoint, "values", {}),
                )
                if continuation is not None:
                    state_view.update(continuation)
                    user_request = str(continuation.get("request") or user_request)
                    state_view["request"] = user_request

            initial_node_name = str(
                state_view.get("direct_modification_resume_node")
                or "scan_workspace_code"
            )
            if (
                initial_node_name in {"execute_frontend", "execute_backend"}
                and state_view.get("direct_modification_handoff_decision") == "approved"
            ):
                # 小任务范围确认可能已经完成过 code.scan；此时首节点就是写入
                # Agent，必须在 Graph 执行前取得租约，不能等节点完成后再加锁。
                lease = workspace_run_leases.acquire(
                    workspace_root=workspace_root,
                    project_id=None,
                    execution_scope={"type": "application", "targetId": "conversation"},
                    thread_id=thread_id,
                    run_id=run_id,
                )
            if not formal_revision_rejected:
                await _report_direct_node_started(
                    report,
                    node_name=initial_node_name,
                    state=state_view,
                    events=events,
                    run_id=run_id,
                    thread_id=thread_id,
                    percent=0,
                )
            async for stream_mode, chunk in active_graph.astream(
                state_view,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                if stream_mode == "custom":
                    await _report_custom_progress(
                        report,
                        chunk=chunk,
                        state=state_view,
                        events=events,
                        report_text=forward_text_delta,
                    )
                    continue
                if not isinstance(chunk, dict):
                    continue
                for node_name, update in chunk.items():
                    if not isinstance(update, dict):
                        continue
                    state_view.update(update)
                    if formal_revision_rejected:
                        # 取消分支只借收口节点清理同一 thread 的 checkpoint，
                        # 不对用户展示任何 Graph 执行步骤。
                        continue
                    revision_impact = state_view.get("revision_impact")
                    impact_interaction_id = (
                        str(revision_impact.get("interactionId") or "")
                        if isinstance(revision_impact, dict)
                        else ""
                    )
                    # 前后端实现修复在确认前不得占用写租约；workspace 直改或确认后的续跑才可加锁。
                    if (
                        state_view.get("conversation_intent") == "formal_revision"
                        and impact_interaction_id
                        and impact_interaction_id not in persisted_revision_impact_ids
                    ):
                        state_view["lifecycle"] = _persist_revision_impact(
                            workspace_root=workspace_root,
                            source_thread_id=thread_id,
                            source_run_id=run_id,
                            request=user_request,
                            state=state_view,
                        )
                        persisted_revision_impact_ids.add(impact_interaction_id)
                    code_scan_findings = (
                        state_view.get("change_impact_code_scan", {}).get("findings", [])
                        if isinstance(state_view.get("change_impact_code_scan"), dict)
                        else []
                    )
                    acquire_after_contract_gate = (
                        node_name == "classify_intent"
                        and state_view.get("conversation_intent") == "implementation_fix"
                        and state_view.get("status") == "in_progress"
                        and state_view.get("change_impact_code_scan_required") is not True
                    )
                    acquire_after_code_scan = (
                        node_name == "scan_change_impact_code"
                        and state_view.get("status") == "in_progress"
                        and bool(code_scan_findings)
                    )
                    acquire_after_confirmation_entry = (
                        node_name in {"execute_frontend", "execute_backend"}
                        and state_view.get("status") == "in_progress"
                        and state_view.get("direct_modification_handoff_decision") == "approved"
                    )
                    if (
                        acquire_after_contract_gate
                        or acquire_after_code_scan
                        or acquire_after_confirmation_entry
                    ) and lease is None:
                        # 实现修复只有在契约门通过且（如需要）取得真实代码发现后
                        # 才登记写租约；纯契约分析/无发现的澄清阶段不占用资源。
                        lease = workspace_run_leases.acquire(
                            workspace_root=workspace_root,
                            project_id=None,
                            execution_scope={"type": "application", "targetId": "conversation"},
                            thread_id=thread_id,
                            run_id=run_id,
                        )
                    event = direct_node_event(
                        node_name,
                        update=update,
                        run_id=run_id,
                        thread_id=thread_id,
                    )
                    events.append(event)
                    await report(
                        AgUiActionProgress(
                            stage=node_name,
                            message=event["message"],
                            detail=str(update.get("message") or ""),
                            percent=DIRECT_NODE_PERCENT.get(node_name, 0),
                            data=direct_progress_payload(
                                state_view,
                                events=events,
                                process_step=direct_node_process_step(node_name, update),
                            ),
                        )
                    )
                    next_node_name = direct_next_node_name(node_name, state_view)
                    if next_node_name:
                        await _report_direct_node_started(
                            report,
                            node_name=next_node_name,
                            state=state_view,
                            events=events,
                            run_id=run_id,
                            thread_id=thread_id,
                            percent=DIRECT_NODE_PERCENT.get(node_name, 0),
                        )
            final_state = dict((await active_graph.aget_state(config)).values)
            final_payload = direct_final_payload(final_state, events=events)
            return AgUiActionResult(
                data=final_payload,
                # 已经通过 TEXT_MESSAGE_CONTENT 增量送出的回复不能再次作为最终 delta 发送，避免正文重复。
                message="" if streamed_text else str(final_payload["summary"]["message"]),
            )
        finally:
            if lease is not None:
                lease.release()
            workflow_run_registry.unregister(run_id, current_task)

    return build_ag_ui_action_stream(
        payload=normalized_payload,
        event_name=CONVERSATION_EVENT_NAME,
        state_key=CONVERSATION_STATE_KEY,
        run_id_prefix="conversation",
        streaming_operation=operation,
        error_message_prefix="自由对话执行失败",
        error_data=lambda _exc: {
            "summary": {
                "status": "failed",
                "phase": "conversation",
                "message": "自由对话执行失败。",
            },
            "events": [],
            "state": {"status": "failed", "phase": "conversation"},
            "result": {"status": "failed"},
        },
        accept=accept,
        emit_progress_text=False,
    )


async def _report_direct_node_started(
    report: ProgressReporter,
    *,
    node_name: str,
    state: dict[str, Any],
    events: list[dict[str, Any]],
    run_id: str,
    thread_id: str,
    percent: int,
) -> None:
    """发送节点开始事件和同 ID 的运行中步骤，供完成事件随后原位结算。"""

    event = direct_node_started_event(
        node_name,
        run_id=run_id,
        thread_id=thread_id,
    )
    events.append(event)
    await report(
        AgUiActionProgress(
            stage=node_name,
            message=event["message"],
            detail=event["message"],
            percent=percent,
            data=direct_progress_payload(
                state,
                events=events,
                process_step=direct_node_running_process_step(node_name),
            ),
        )
    )


async def _report_custom_progress(
    report: ProgressReporter,
    *,
    chunk: Any,
    state: dict[str, Any],
    events: list[dict[str, Any]],
    report_text: TextDeltaReporter | None = None,
) -> None:
    """把 Graph custom stream 转换为工具或测试进度。"""

    progress = chunk if isinstance(chunk, dict) else {}
    event_type = str(progress.get("type") or "")
    if event_type == "workspace_inspection.progress":
        detail = (
            progress.get("detail")
            if isinstance(progress.get("detail"), dict)
            else {}
        )
        state["workspace_scan_progress"] = detail
        node_name = str(progress.get("node_name") or "scan_workspace_code")
        await report(
            AgUiActionProgress(
                stage=node_name,
                message=str(progress.get("message") or "正在扫描用户工作区代码…"),
                detail=str(progress.get("message") or ""),
                percent=DIRECT_NODE_PERCENT.get(node_name, 20),
                data=direct_progress_payload(
                    state,
                    events=events,
                    process_step={
                        "id": f"direct:{node_name}",
                        "kind": "workflow",
                        "status": "running",
                        "title": "正在执行 扫描工作区代码",
                        "detail": str(
                            progress.get("message") or "正在扫描用户工作区代码…"
                        ),
                        "sequence": DIRECT_NODE_PERCENT.get(node_name, 20),
                        "nodeName": node_name,
                        "workspaceInspectionProgress": detail,
                    },
                ),
            )
        )
        return
    if event_type == "conversation.text_delta":
        delta = str(progress.get("delta") or "")
        if report_text is not None and delta:
            await report_text(delta)
        return
    if event_type in {"direct_modification.tool_activity", "conversation.tool_activity"}:
        activity = progress.get("activity") if isinstance(progress.get("activity"), dict) else {}
        call_id = str(activity.get("callId") or uuid4().hex)
        status = str(activity.get("status") or "running")
        await report(
            AgUiActionProgress(
                stage=str(progress.get("node_name") or "agent"),
                message=str(activity.get("message") or "Agent 正在使用工作区工具。"),
                detail=str(activity.get("path") or activity.get("message") or ""),
                percent=50,
                data=direct_progress_payload(
                    state,
                    events=events,
                    process_step={
                        "id": f"direct-tool:{call_id}",
                        "kind": "tool",
                        "status": status if status in {"running", "completed", "failed"} else "running",
                        "title": str(activity.get("tool") or "工作区工具"),
                        "detail": str(activity.get("message") or ""),
                        "sequence": len(events) + 100,
                        "nodeName": str(progress.get("node_name") or "agent"),
                    },
                ),
            )
        )
        return
    if event_type == "integration_test.checks":
        checks = integration_test_checks(progress)
        if not checks:
            return
        await report(
            AgUiActionProgress(
                stage="integration_test",
                message="正在验证快速修改。",
                detail=integration_test_check_summary(checks),
                percent=80,
                data=direct_progress_payload(
                    state,
                    events=events,
                    process_step={
                        "id": "direct:integration_test",
                        "kind": "workflow",
                        "status": "running",
                        "title": "正在执行 验证项目",
                        "detail": integration_test_check_summary(checks),
                        "sequence": 800,
                        "nodeName": "integration_test",
                        "checks": checks,
                    },
                ),
            )
        )


def _last_user_message(messages: Any) -> str:
    """从标准 AG-UI messages 中读取最后一条用户文本。"""

    for item in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _conversation_request(*, original_request: str | None, latest_user_request: str) -> str:
    """合并自由对话恢复时的原始需求和本轮回答，确保分类器看到最新用户输入。"""

    original = str(original_request or "").strip()
    latest = str(latest_user_request or "").strip()
    if not latest:
        return original
    if not original or original == latest:
        return latest
    return "\n".join(
        [
            "原始用户请求：",
            original,
            "",
            "本轮用户补充：",
            latest,
        ]
    ).strip()


def _persist_revision_impact(
    *,
    workspace_root: str,
    source_thread_id: str,
    source_run_id: str,
    request: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """把只读 impact 卡绑定到 lifecycle，但在批准前不创建 change、draft 或 lease。"""

    raw_impact = state.get("revision_impact")
    if not isinstance(raw_impact, dict):
        raise ValueError("formal_revision 缺少 revision impact。")
    raw_target = state.get("change_target")
    if not isinstance(raw_target, dict) or not raw_target:
        raw_target = {"type": "application"}
    register_revision_impact(
        workspace_root,
        interaction_id=str(raw_impact.get("interactionId") or ""),
        source_thread_id=source_thread_id,
        source_run_id=source_run_id,
        request=request,
        target=RevisionTarget.model_validate(raw_target),
        impact=RevisionImpact.model_validate(
            {
                key: value
                for key, value in raw_impact.items()
                if key not in {"interactionId", "status"}
            }
        ),
    )
    lifecycle = load_application_lifecycle(workspace_root)
    if lifecycle is None:
        raise ValueError("application lifecycle 尚未初始化。")
    return application_lifecycle_payload(lifecycle)


def _cancel_run_id(payload: dict[str, Any]) -> str:
    """读取复用当前端点发送的 AG-UI 取消目标。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return ""
    return str(forwarded_props.get("cancelRunId") or "").strip()


def _safe_approved_paths(values: Any) -> list[str]:
    """裁剪自由对话确认的追加路径，阻止绝对路径、越界路径和敏感文件进入任务包。"""

    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values[:100]:
        path = str(value or "").strip().replace("\\", "/").lstrip("/")
        parts = [part for part in path.split("/") if part]
        if (
            not path
            or ".." in parts
            or any(part.casefold() in {".env", ".xcodeagent"} for part in parts)
        ):
            continue
        if path not in result:
            result.append(path[:1_000])
    return result
