"""通过既有应用规划 AG-UI 端点承载智能体配置修订及页面载体草稿。"""

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.agent_settings_revision import execute_agent_settings_revision, parse_agent_settings_revision_request
from app.services.agent_surface_selection import (
    GetAgentSurfaceSelectionRequest,
    SaveAgentSurfaceSelectionRequest,
    get_agent_surface_selection_draft,
    save_agent_surface_selection_draft,
)
from app.services.application_lifecycle import _application_lifecycle_lock, application_lifecycle_path
from app.services.preview_runtime_guard import maintenance_lock, require_no_maintenance
from app.workspace.task_documents import build_task_plan_lifecycle_lock


def build_agent_planning_action_stream(payload: dict[str, Any], *, raw: Any,
                                      kind: str, accept: str | None) -> AsyncIterator[str]:
    """校验动作并串行写入，业务失败也发送完整的 AG-UI 生命周期。"""

    def execute() -> AgUiActionResult:
        """在同一工作区 lifecycle 锁内完成草稿、确认和资源状态的检查与提交。"""

        if kind == "agentSettingsRevision":
            request = parse_agent_settings_revision_request(raw)
            with maintenance_lock, build_task_plan_lifecycle_lock(request.workspace_root), _application_lifecycle_lock(application_lifecycle_path(request.workspace_root)):
                require_no_maintenance(request.workspace_root)
                data, message = execute_agent_settings_revision(
                    request, source_thread_id=str(payload.get("threadId") or uuid4()),
                    source_run_id=str(payload.get("runId") or uuid4()),
                )
            return AgUiActionResult(data=data, message=message)
        if isinstance(raw, dict) and raw.get("action") == "get":
            read_request = GetAgentSurfaceSelectionRequest.model_validate(raw)
            with _application_lifecycle_lock(application_lifecycle_path(read_request.workspace_root)):
                data = get_agent_surface_selection_draft(read_request)
            return AgUiActionResult(data=data, message="已读取当前智能体浮窗选择草稿。")
        request = SaveAgentSurfaceSelectionRequest.model_validate(raw)
        with maintenance_lock, _application_lifecycle_lock(application_lifecycle_path(request.workspace_root)):
            require_no_maintenance(request.workspace_root)
            data = save_agent_surface_selection_draft(request)
        return AgUiActionResult(data=data, message="智能体浮窗选择已保存，仍需联合确认需求文档。")

    async def operation() -> AgUiActionResult:
        """把同步文件事务放在线程中执行，保留共享动作流的失败及取消处理。"""

        return await asyncio.to_thread(execute)

    return build_ag_ui_action_stream(
        payload=payload, event_name="agent-settings-revision" if kind == "agentSettingsRevision" else "agent-surface-selection-draft",
        state_key=kind, run_id_prefix="agent-planning-action", operation=operation,
        error_message_prefix="智能体配置操作失败", accept=accept,
        register_workspace_run=not (isinstance(raw, dict) and raw.get("action") in {"get", "get_agent_settings_revision"}),
    )
