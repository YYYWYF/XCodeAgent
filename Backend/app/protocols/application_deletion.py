"""应用删除前工作区级停机与持久资源释放的 AG-UI 协议。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.graph import clear_workflow_graph_cache
from app.graph.application_planning_workflow import (
    clear_application_planning_graph_cache,
)
from app.graph.direct_modification_workflow import clear_direct_modification_graph_cache
from app.persistence.checkpoints import (
    close_workflow_checkpointer_for_workspace,
    delete_workflow_checkpoints_for_workspace,
    workflow_checkpoint_db_path,
)
from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    ProgressReporter,
    build_ag_ui_action_stream,
)
from app.protocols.workflow.run_control import workflow_run_registry
from app.protocols.workflow.runtime import clear_application_planning_resume_locks
from app.services.application_lifecycle import (
    clear_application_lifecycle_lock,
    load_application_lifecycle,
)
from app.services.authorization_bootstrap import clear_authorization_bootstrap_lock
from app.services.backend_process_registry import (
    clear_backend_process_registry_workspace,
)
from app.services.application_template_generation import (
    begin_application_template_deletion,
    clear_application_template_lock,
    end_application_template_deletion,
    wait_for_application_template_idle,
)
from app.services.project_launcher import stop_project_preview
from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.services.workspace_process_registry import workspace_process_registry
from app.workspace.run_lease import workspace_run_leases


APPLICATION_DELETION_EVENT_NAME = "application-deletion"


class ApplicationDeletionRequest(BaseModel):
    """校验应用销毁准备动作的稳定应用和工作区身份。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["prepare"]
    application_id: str = Field(alias="applicationId", min_length=1, max_length=256)
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)


def application_deletion_capabilities() -> dict[str, Any]:
    """发布应用删除前停机协议及其保留的共享资源边界。"""

    return {
        "name": "application-deletion",
        "endpoint": "/application-deletion/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.applicationDeletion",
        "actions": ["prepare"],
        "customEventName": APPLICATION_DELETION_EVENT_NAME,
        "stateSnapshotKey": "applicationDeletion",
        "preservedSharedResources": [
            "platform-database-key",
            "user-skills",
            "agent-files",
            "authentication",
            "application-settings",
            "external-databases",
            "remote-traces",
            "git-remotes",
        ],
    }


def application_deletion_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 中提取应用销毁准备参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("applicationDeletion")
    return value if isinstance(value, dict) else None


def build_application_deletion_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """冻结并清空目标应用运行资源，成功后允许 Electron 移动项目目录。"""

    raw_request = application_deletion_input(payload)
    if raw_request is None:
        raise ValueError("缺少 forwardedProps.applicationDeletion。")

    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """复用统一销毁准备逻辑并保持原有 AG-UI 成功结果。"""

        request = ApplicationDeletionRequest.model_validate(raw_request)
        report_data = await prepare_application_deletion(request, report=report)
        return AgUiActionResult(data=report_data, message="应用已停止，可以安全删除。")

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=APPLICATION_DELETION_EVENT_NAME,
        state_key="applicationDeletion",
        run_id_prefix="application-deletion",
        progress_operation=operation,
        error_message_prefix="应用删除准备失败",
        error_data=lambda _exc: {
            "action": raw_request.get("action"),
            "applicationId": raw_request.get("applicationId"),
            "readyForTrash": False,
        },
        accept=accept,
    )


async def prepare_application_deletion(
    request: ApplicationDeletionRequest,
    *,
    report: ProgressReporter | None = None,
) -> dict[str, Any]:
    """按可逆停机和不可逆持久化释放两阶段准备应用目录删除。"""

    workspace = _validated_managed_workspace(
        request.workspace_root,
        application_id=request.application_id,
    )
    workspace_text = str(workspace)
    workspace_exists = workspace.is_dir()
    lifecycle = load_application_lifecycle(workspace) if workspace_exists else None
    thread_ids = _application_thread_ids(lifecycle)
    pool = get_ui_design_generation_pool()

    begin_application_template_deletion(workspace)
    workflow_run_registry.begin_workspace_deletion(workspace_text)
    workspace_process_registry.begin_workspace_deletion(workspace)

    # 阶段 A 只处理可逆的运行资源停机；任一步失败都解除当前工作区的删除栅栏。
    try:
        if report is not None:
            await report(
                AgUiActionProgress(
                    stage="blocking_new_runs",
                    message="已封锁该应用的新运行，正在中断现有任务…",
                    percent=10,
                )
            )
        stage_results = await asyncio.gather(
            workflow_run_registry.cancel_workspace(workspace_text),
            pool.cancel_workspace(workspace_text),
            asyncio.to_thread(stop_project_preview, workspace),
            asyncio.to_thread(wait_for_application_template_idle, workspace),
            asyncio.to_thread(
                workspace_process_registry.cancel_workspace,
                workspace,
            ),
            return_exceptions=True,
        )
        # 等齐所有有限停机动作再回滚，避免后台停止线程与重新开放的工作区并发写入。
        for stage_result in stage_results:
            if isinstance(stage_result, BaseException):
                raise stage_result
        run_result = cast(dict[str, Any], stage_results[0])
        design_result = cast(dict[str, Any], stage_results[1])
        preview_result = cast(dict[str, Any], stage_results[2])
        template_idle = cast(bool, stage_results[3])
        process_result = cast(dict[str, Any], stage_results[4])
        remaining_runs = list(run_result.get("remainingRunIds") or [])
        remaining_designs = list(design_result.get("remainingPageIds") or [])
        remaining_processes = list(process_result.get("remainingProcessIds") or [])
        if (
            remaining_runs
            or remaining_designs
            or remaining_processes
            or not template_idle
        ):
            raise RuntimeError(
                "应用仍有未退出的任务："
                f"runs={remaining_runs or '[]'}, uiDesigns={remaining_designs or '[]'}, "
                f"processes={remaining_processes or '[]'}, templateIdle={template_idle}"
            )
        if preview_result.get("status") == "failed":
            raise RuntimeError(str(preview_result.get("message") or "应用预览停止失败。"))
    except BaseException:
        end_application_template_deletion(workspace)
        workflow_run_registry.end_workspace_deletion(workspace_text)
        workspace_process_registry.end_workspace_deletion(workspace)
        pool.end_workspace_deletion(workspace_text)
        raise

    # 阶段 B 已开始清除持久化状态；此后的失败不得把工作区伪装回可运行状态。
    if report is not None:
        await report(
            AgUiActionProgress(
                stage="releasing_persistence",
                message="运行已停止，正在释放 checkpoint、Graph 缓存和工作区锁…",
                percent=65,
            )
        )
    checkpoint_path = workflow_checkpoint_db_path(
        workspace=workspace_text,
        project_id=request.application_id,
    )
    if workspace_exists:
        checkpoint_result = await delete_workflow_checkpoints_for_workspace(
            workspace=workspace_text,
            project_id=request.application_id,
            thread_ids=thread_ids,
        )
        closed_local_checkpointer = await close_workflow_checkpointer_for_workspace(
            workspace=workspace_text,
            project_id=request.application_id,
        )
    else:
        checkpoint_result = {
            "databasePath": str(checkpoint_path),
            "deletedThreadCount": 0,
            "alreadyTrashed": True,
        }
        closed_local_checkpointer = False
    if closed_local_checkpointer:
        cache_key = str(checkpoint_path)
        clear_workflow_graph_cache(cache_key=cache_key)
        clear_application_planning_graph_cache(cache_key=cache_key)
        clear_direct_modification_graph_cache(cache_key=cache_key)

    released_leases = workspace_run_leases.release_workspace(
        workspace_root=workspace_text,
        project_id=request.application_id,
    )
    released_resume_locks = clear_application_planning_resume_locks(thread_ids)
    cleared_lifecycle_lock = clear_application_lifecycle_lock(workspace)
    cleared_template_lock = clear_application_template_lock(workspace)
    cleared_authorization_lock = clear_authorization_bootstrap_lock(workspace)
    cleared_backend_process_cache = clear_backend_process_registry_workspace(workspace)
    report_data = {
        "action": request.action,
        "applicationId": request.application_id,
        "workspaceRoot": workspace_text,
        "readyForTrash": True,
        "runs": run_result,
        "uiDesigns": design_result,
        "preview": preview_result,
        "processes": process_result,
        "checkpoints": {
            **checkpoint_result,
            "localConnectionClosed": closed_local_checkpointer,
        },
        "released": {
            "workspaceLeases": released_leases,
            "planningResumeLocks": released_resume_locks,
            "lifecycleLock": cleared_lifecycle_lock,
            "templateLock": cleared_template_lock,
            "authorizationBootstrapLock": cleared_authorization_lock,
            "backendProcessCache": cleared_backend_process_cache,
            "templateOperationsIdle": template_idle,
        },
    }
    if report is not None:
        await report(
            AgUiActionProgress(
                stage="ready_for_trash",
                message="应用运行资源已全部释放，可以安全移入系统回收站。",
                percent=100,
                data={"readyForTrash": True},
            )
        )
    return report_data


def _validated_managed_workspace(workspace_root: str, *, application_id: str) -> Path:
    """校验受管工作区；已完成停机的删除重试允许目录已经进入回收站。"""

    candidate = Path(workspace_root).expanduser()
    if candidate.is_symlink():
        raise ValueError("不能通过符号链接删除应用工作区。")
    workspace = candidate.resolve(strict=False)
    if not workspace.exists():
        if workflow_run_registry.is_workspace_deleting(str(workspace)):
            return workspace
        raise ValueError("应用工作区不存在，且没有可恢复的删除事务。")
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("只能删除真实存在且不是符号链接的应用工作区。")
    marker = workspace / ".xcodeagent" / "application.json"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("该目录不是由 XCodeAgent 管理的项目，不能执行应用删除。")
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is not None and lifecycle.application.id != application_id:
        raise ValueError("应用标识与工作区生命周期不匹配，已拒绝删除。")
    return workspace


def _application_thread_ids(lifecycle: Any) -> set[str]:
    """收集应用初始化和全部工作台 execution 使用过的线程标识。"""

    if lifecycle is None:
        return set()
    thread_ids = {str(lifecycle.initialization.thread_id or "").strip()}
    thread_ids.update(
        str(execution.thread_id or "").strip()
        for execution in lifecycle.active_executions.values()
    )
    return {thread_id for thread_id in thread_ids if thread_id}
