"""快速修改独立 LangGraph 的 AG-UI 协议适配器。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agents.workspace_scope import resolve_workspace_root
from app.graph.direct_modification_workflow import direct_modification_graph_for_request
from app.persistence.checkpoints import cleanup_workflow_checkpoints
from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    ProgressReporter,
    build_ag_ui_action_stream,
)
from app.protocols.direct_modification_projection import (
    DIRECT_NODE_PERCENT,
    direct_final_payload,
    direct_node_event,
    direct_node_process_step,
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


DIRECT_MODIFICATION_EVENT_NAME = "direct-modification"
DIRECT_MODIFICATION_STATE_KEY = "directModification"


class DirectModificationInput(BaseModel):
    """校验快速修改 AG-UI forwardedProps 的最小业务参数。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    selected_skill_names: list[str] = Field(
        default_factory=list,
        alias="selectedSkillNames",
        max_length=64,
    )


def direct_modification_capabilities() -> dict[str, Any]:
    """发布独立快速修改 Graph 的 AG-UI 能力元数据。"""

    return {
        "name": "direct-modification",
        "endpoint": "/direct-modification/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.directModification",
        "customEventName": DIRECT_MODIFICATION_EVENT_NAME,
        "stateSnapshotKey": DIRECT_MODIFICATION_STATE_KEY,
        "owners": ["frontend", "backend", "fullstack", "unknown"],
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
        "executionPolicy": {
            "subagentsEnabled": False,
            "todoPlanningEnabled": False,
        },
    }


def direct_modification_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 读取快速修改参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("directModification")
    return value if isinstance(value, dict) else None


def build_direct_modification_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """执行快速修改 Graph，并投射完整 AG-UI 生命周期和进度。"""

    thread_id = str(payload.get("threadId") or uuid4())
    run_id = str(payload.get("runId") or f"direct-modification-{uuid4().hex[:12]}")
    normalized_payload = {**payload, "threadId": thread_id, "runId": run_id}
    cancel_run_id = _cancel_run_id(normalized_payload)
    if cancel_run_id:
        return build_workflow_cancellation_ag_ui_stream(
            thread_id=thread_id,
            run_id=run_id,
            target_run_id=cancel_run_id,
            accept=accept,
        )
    raw_input = direct_modification_input(normalized_payload)
    if raw_input is None:
        raise ValueError("缺少 forwardedProps.directModification。")

    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """运行独立 Graph，并把节点、工具和测试进度送入 AG-UI 队列。"""

        request = DirectModificationInput.model_validate(raw_input)
        user_request = _last_user_message(normalized_payload.get("messages"))
        if not user_request:
            raise ValueError("快速修改请求必须包含一条用户消息。")
        validate_selected_user_skills(request.selected_skill_names)
        resolved_workspace = resolve_workspace_root(request.workspace_root)
        if resolved_workspace is None:
            raise ValueError("快速修改请求必须提供有效的 workspaceRoot。")
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
            "integration_contract_check_enabled": False,
            "integration_repair_enabled": False,
            "timeline": [],
        }
        try:
            lease = workspace_run_leases.acquire(
                workspace_root=workspace_root,
                project_id=None,
                execution_scope={"type": "application", "targetId": "direct-modification"},
                thread_id=thread_id,
                run_id=run_id,
            )
            config = {
                "configurable": {
                    "thread_id": f"direct-modification:{thread_id}",
                },
                "run_name": "xcodeagent-direct-modification",
                "tags": ["xcodeagent", "direct-modification"],
                "metadata": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "workspace": workspace_root,
                    "selected_skill_names": list(request.selected_skill_names),
                },
            }
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
                    )
                    continue
                if not isinstance(chunk, dict):
                    continue
                for node_name, update in chunk.items():
                    if not isinstance(update, dict):
                        continue
                    state_view.update(update)
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
            final_state = dict((await active_graph.aget_state(config)).values)
            final_payload = direct_final_payload(final_state, events=events)
            return AgUiActionResult(
                data=final_payload,
                message=str(final_payload["summary"]["message"]),
            )
        finally:
            if lease is not None:
                lease.release()
            workflow_run_registry.unregister(run_id, current_task)

    return build_ag_ui_action_stream(
        payload=normalized_payload,
        event_name=DIRECT_MODIFICATION_EVENT_NAME,
        state_key=DIRECT_MODIFICATION_STATE_KEY,
        run_id_prefix="direct-modification",
        progress_operation=operation,
        error_message_prefix="快速修改执行失败",
        error_data=lambda _exc: {
            "summary": {
                "status": "failed",
                "phase": "direct_modification",
                "message": "快速修改执行失败。",
            },
            "events": [],
            "state": {"status": "failed", "phase": "direct_modification"},
            "result": {"status": "failed"},
        },
        accept=accept,
        emit_progress_text=False,
    )


async def _report_custom_progress(
    report: ProgressReporter,
    *,
    chunk: Any,
    state: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    """把 Graph custom stream 转换为工具或测试进度。"""

    progress = chunk if isinstance(chunk, dict) else {}
    event_type = str(progress.get("type") or "")
    if event_type == "direct_modification.tool_activity":
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


def _cancel_run_id(payload: dict[str, Any]) -> str:
    """读取复用当前端点发送的 AG-UI 取消目标。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return ""
    return str(forwarded_props.get("cancelRunId") or "").strip()
