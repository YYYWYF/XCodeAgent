"""自由对话独立 LangGraph 的 AG-UI 协议适配器。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agents.workspace_scope import resolve_workspace_root
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
from app.services.user_skill_runtime import validate_selected_user_skills
from app.workspace.run_lease import WorkspaceRunLease, workspace_run_leases


CONVERSATION_EVENT_NAME = "conversation"
CONVERSATION_STATE_KEY = "conversation"


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
            "workspace_change",
            "formal_workflow",
            "needs_clarification",
        ],
        "owners": ["frontend", "backend", "fullstack", "workspace", "none", "unknown"],
        "statuses": [
            "in_progress",
            "completed",
            "requires_user_input",
            "requires_planning",
            "failed",
        ],
        "workflowIndependent": True,
        "targetRequired": False,
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
        },
        "scan": {
            "node": "scan_workspace_code",
            "label": "扫描工作区代码",
            "progressEvent": "workspace_inspection.progress",
            "fallback": "workspace_search",
        },
    }


def conversation_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 读取自由对话参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("conversation")
    return value if isinstance(value, dict) else None


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
        # 恢复澄清时同时保留上一轮原始需求和本轮最新回答；不能让 originalRequest 覆盖 AG-UI 最新用户消息。
        user_request = _conversation_request(
            original_request=request.original_request,
            latest_user_request=_last_user_message(normalized_payload.get("messages")),
        )
        if not user_request:
            raise ValueError("自由对话请求必须包含一条用户消息。")
        validate_selected_user_skills(request.selected_skill_names)
        resolved_workspace = resolve_workspace_root(request.workspace_root)
        if resolved_workspace is None:
            raise ValueError("自由对话请求必须提供有效的 workspaceRoot。")
        workspace_root = str(resolved_workspace)
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
            "direct_modification_approved_paths": _safe_approved_paths(request.approved_paths),
            "direct_modification_handoff_decision": str(request.handoff_decision or ""),
            "integration_repair_enabled": False,
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
        try:
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
            await _report_direct_node_started(
                report,
                node_name="scan_workspace_code",
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
                    if (
                        node_name == "classify_intent"
                        and state_view.get("conversation_intent") == "workspace_change"
                        and lease is None
                    ):
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
