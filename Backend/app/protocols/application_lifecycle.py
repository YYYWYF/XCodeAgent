"""应用生命周期独立 AG-UI 动作协议。"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.protocols.ag_ui_action_stream import (
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleMissingError,
    application_lifecycle_payload,
    completed_development_artifacts,
    cleanup_session_failed_executions,
    best_effort_delete_planning_recovery,
    end_workbench_execution,
    ensure_application_lifecycle,
    load_application_lifecycle,
    retry_application_template_generation,
)
from app.persistence.checkpoints import delete_workflow_checkpoints_for_threads
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.build_task_plan_lifecycle import (
    release_session_owned_pending_build_task_plan,
)
from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.services.execution_recovery_scanner import reconcile_workspace_recovery
from app.services.execution_recovery_projection import resolve_execution_recovery_projection
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.services.workspace_bootstrap.service import WorkspaceBootstrapService

APPLICATION_LIFECYCLE_EVENT_NAME = "application-lifecycle"
logger = logging.getLogger("uvicorn.error")


class ApplicationLifecycleApplication(BaseModel):
    """校验创建 lifecycle 所需的最小应用身份。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    app_name: str = Field(alias="appName", min_length=1, max_length=512)


class ApplicationLifecycleAction(BaseModel):
    """校验生命周期创建、读取、模板生成和 Session 收口动作。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal[
        "create",
        "get",
        "bootstrap_template_generation",
        "retry_bootstrap_template_generation",
        "workspace_attach",
        "release_session_pending",
        "cleanup_session_failed_executions",
        "prepare_session_deletion",
    ]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    application: ApplicationLifecycleApplication | None = None
    session_id: str | None = Field(default=None, alias="sessionId", max_length=256)
    session_thread_id: str | None = Field(default=None, alias="sessionThreadId", max_length=256)
    # 发起新迭代时由调用方带入上一版本的产物进度，服务端只继承其中的 completed 事实。
    inherited_development_artifacts: dict[str, Any] | None = Field(
        default=None, alias="inheritedDevelopmentArtifacts"
    )

    @model_validator(mode="after")
    def validate_release_session_id(self) -> "ApplicationLifecycleAction":
        """要求 Session Pending 收口动作携带合法的非空 sessionId。"""

        if self.action in {"release_session_pending", "cleanup_session_failed_executions", "prepare_session_deletion"}:
            if not str(self.session_id or "").strip():
                raise ValueError(f"{self.action} 必须提供合法非空的 sessionId。")
        if self.action == "prepare_session_deletion" and not str(self.session_thread_id or "").strip():
            raise ValueError("prepare_session_deletion 必须提供 sessionThreadId。")
        return self


def application_lifecycle_capabilities() -> dict[str, Any]:
    """发布独立应用生命周期 AG-UI 动作能力。"""

    return {
        "name": "application-lifecycle",
        "endpoint": "/application-lifecycle/run",
        "transport": "ag-ui-sse",
        "stateFile": ".devagentstudio/application-lifecycle.json",
        "actionField": "forwardedProps.applicationLifecycle",
        "actions": [
            "create",
            "get",
            "bootstrap_template_generation",
            "retry_bootstrap_template_generation",
            "workspace_attach",
            "release_session_pending",
            "cleanup_session_failed_executions",
            "prepare_session_deletion",
        ],
        "customEventName": APPLICATION_LIFECYCLE_EVENT_NAME,
        "stateSnapshotKey": "applicationLifecycle",
        "workflowIndependent": True,
        "developmentArtifacts": {
            "targets": ["page", "endpoint"],
            "statuses": ["pending", "in_progress", "completed"],
            "completionBoundary": "test_phase_confirmation",
            "gateField": "testEntryGate",
            "secondaryModificationResetsCompletion": False,
        },
    }


def application_lifecycle_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 forwardedProps 读取可选的生命周期 AG-UI 动作。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("applicationLifecycle")
    return value if isinstance(value, dict) else None


async def _prepare_session_deletion(request: ApplicationLifecycleAction) -> Any:
    """停掉目标会话运行，收口其生命周期，再删除独占的 Graph checkpoint。"""

    workspace = request.workspace_root
    session_id = request.session_id or ""
    state = load_application_lifecycle(workspace)
    if state is None:
        raise ApplicationLifecycleMissingError("application-lifecycle.json 不存在。")
    owned = {
        run_id: execution
        for run_id, execution in state.active_executions.items()
        if execution.owner_session_id == session_id
    }
    thread_ids = {request.session_thread_id or ""} | {
        execution.thread_id for execution in owned.values()
    }
    for execution in state.active_executions.values():
        if execution.owner_session_id != session_id and execution.thread_id in thread_ids:
            raise RuntimeError("该 Graph 线程仍由其它会话使用，不能删除 checkpoint。")
    for thread_id in thread_ids:
        workflow_run_registry.begin_thread_deletion(workspace, thread_id)
    try:
        for thread_id in thread_ids:
            result = await workflow_run_registry.cancel_thread(workspace, thread_id)
            if result["remainingRunIds"] or result["remainingProcessIds"]:
                raise RuntimeError(f"会话运行或子进程仍未退出：{result}")
        await asyncio.to_thread(
            release_session_owned_pending_build_task_plan,
            {"workspace": workspace},
            owner_session_id=session_id,
        )
        for run_id in owned:
            await asyncio.to_thread(
                end_workbench_execution, workspace, run_id=run_id, missing_ok=True
            )
            await asyncio.to_thread(best_effort_delete_planning_recovery, workspace, run_id)
        # run 已退出且 Pending/lifecycle 已收口，此后不再允许其 checkpoint 被写回。
        await delete_workflow_checkpoints_for_threads(
            workspace=workspace, thread_ids=thread_ids
        )
        refreshed = load_application_lifecycle(workspace)
        if refreshed is None:
            raise ApplicationLifecycleMissingError("会话删除后缺少 lifecycle。")
        return refreshed
    except BaseException:
        for thread_id in thread_ids:
            workflow_run_registry.end_thread_deletion(workspace, thread_id)
        raise


def build_application_lifecycle_ag_ui_stream(
    *,
    payload: dict[str, Any],
    lifecycle_input: dict[str, Any] | None = None,
    accept: str | None = None,
    bootstrap_service: WorkspaceBootstrapService | None = None,
) -> AsyncIterator[str]:
    """执行独立生命周期动作并投射完整 AG-UI 生命周期。"""

    resolved_input = lifecycle_input or application_lifecycle_input(payload)
    if resolved_input is None:
        raise ValueError("缺少 forwardedProps.applicationLifecycle。")

    async def operation() -> AgUiActionResult:
        """校验动作并返回唯一权威 lifecycle 快照。"""

        request = ApplicationLifecycleAction.model_validate(resolved_input)
        if request.action == "create":
            application = request.application
            if application is None:
                raise ValueError(
                    "create 必须提供有效的 application.id 和 application.appName。"
                )
            existing = load_application_lifecycle(request.workspace_root)
            if existing is not None and existing.application.id != application.id:
                raise ValueError(
                    "当前工作区已属于另一个应用，请为新应用选择独立的项目目录。"
                )
            state = ensure_application_lifecycle(
                request.workspace_root,
                application_id=application.id,
                application_name=application.app_name,
                initialization_thread_id=str(payload.get("threadId") or "") or None,
                active_run_id=str(payload.get("runId") or "") or None,
                inherited_artifacts=completed_development_artifacts(
                    request.inherited_development_artifacts
                ),
            )
            message = "应用生命周期已创建。"
        elif request.action == "get":
            from app.services.development_artifacts import refresh_development_artifacts

            # 重新打开已知 workspace 时惰性识别旧 Backend 留下的中断执行；扫描器自身
            # fail-open，不能把恢复基础设施故障升级为 lifecycle get 失败。
            await reconcile_workspace_recovery(request.workspace_root)
            state = refresh_development_artifacts(request.workspace_root)
            message = "已读取应用生命周期。"
        elif request.action in {
            "bootstrap_template_generation",
            "retry_bootstrap_template_generation",
        }:
            if bootstrap_service is None:
                raise RuntimeError("Workspace Bootstrap 服务尚未初始化。")
            if request.action == "retry_bootstrap_template_generation":
                await asyncio.to_thread(
                    retry_application_template_generation,
                    request.workspace_root,
                    active_run_id=str(payload.get("runId") or "") or None,
                )
            task = await bootstrap_service.trigger(request.workspace_root)
            result = await asyncio.shield(task)
            state = load_application_lifecycle(request.workspace_root)
            if state is None:
                raise RuntimeError("Bootstrap 完成后缺少 lifecycle。")
            message = "Workspace Bootstrap 已完成，可以进入工作台。"
            data = {
                "action": request.action,
                **result,
                "lifecycle": application_lifecycle_payload(state),
            }
            return AgUiActionResult(data=data, message=message)
        elif request.action == "workspace_attach":
            attached = await asyncio.to_thread(
                template_mutation_coordinator.attach_workspace,
                request.workspace_root,
            )
            state = load_application_lifecycle(request.workspace_root)
            if state is None:
                raise ApplicationLifecycleMissingError("application-lifecycle.json 不存在。")
            message = "Workspace Attach 已完成。"
        elif request.action == "release_session_pending":
            released = await asyncio.to_thread(
                release_session_owned_pending_build_task_plan,
                {"workspace": request.workspace_root},
                owner_session_id=request.session_id or "",
            )
            state = load_application_lifecycle(request.workspace_root)
            if state is None:
                raise ApplicationLifecycleMissingError("application-lifecycle.json 不存在。")
            message = "已收口当前 Session 拥有的 Pending Build DAG。"
        elif request.action == "cleanup_session_failed_executions":
            state = await asyncio.to_thread(
                cleanup_session_failed_executions,
                request.workspace_root,
                request.session_id or "",
            )
            message = "已收口当前 Session 已删除后遗留的失败 Workflow execution。"
        elif request.action == "prepare_session_deletion":
            state = await _prepare_session_deletion(request)
            message = "会话运行和 checkpoint 已清理，可以删除本地记录。"
        data = {
            "action": request.action,
            "lifecycle": application_lifecycle_payload(state),
        }
        if request.action == "workspace_attach":
            data["workspaceAttach"] = {
                "action": attached.action,
                "cleaned": attached.cleaned,
                "lifecycleChanged": attached.lifecycle_changed,
            }
        if request.action in {
            "get",
            "release_session_pending",
            "cleanup_session_failed_executions",
            "prepare_session_deletion",
        }:
            # 恢复或收口后的投影仅附加到当前响应，不能伪装成可跨重启持久化的生命周期事实。
            data["lifecycle"]["extensions"] = {
                **dict(data["lifecycle"].get("extensions") or {}),
                "planningRefresh": resolve_planning_refresh_state(request.workspace_root),
            }
        if request.action == "get":
            # Recovery 仅在读取时投射到响应；扫描后的异常执行不写回 lifecycle 文件。
            try:
                recovery_projection = await resolve_execution_recovery_projection(
                    request.workspace_root
                )
            except Exception as exc:
                # 投影失败不能阻断基础 lifecycle GET，但必须明确返回空候选列表。
                logger.warning(
                    "recovery.projection.failed workspace=%s error=%s",
                    request.workspace_root,
                    exc,
                    exc_info=True,
                )
                recovery_projection = None
            data["lifecycle"]["extensions"]["executionRecovery"] = (
                recovery_projection.model_dump(mode="json", by_alias=True)
                if recovery_projection is not None
                else {
                    "schemaVersion": "execution-recovery.v1",
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                    "candidates": [],
                }
            )
        if request.action == "release_session_pending":
            data["sessionPendingReleased"] = released
        return AgUiActionResult(data=data, message=message)

    action = str(resolved_input.get("action") or "")
    # 删除准备必须能停止目标会话的预览维护运行，不能先被维护互斥挡住。
    guarded_workspace = (
        None
        if action in {"get", "workspace_attach", "prepare_session_deletion"}
        else str(resolved_input.get("workspaceRoot") or "") or None
    )
    return build_ag_ui_action_stream(
        payload=payload,
        event_name=APPLICATION_LIFECYCLE_EVENT_NAME,
        state_key="applicationLifecycle",
        run_id_prefix="application-lifecycle",
        operation=operation,
        error_message_prefix="应用生命周期操作失败",
        error_data=lambda _exc: {"action": resolved_input.get("action")},
        accept=accept,
        workspace_root=guarded_workspace,
        register_workspace_run=action not in {"get", "workspace_attach", "prepare_session_deletion"},
    )
