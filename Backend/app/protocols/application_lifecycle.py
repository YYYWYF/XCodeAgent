"""应用生命周期独立 AG-UI 动作协议。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    begin_application_template_generation,
    complete_application_template_generation,
    ensure_application_lifecycle,
    load_application_lifecycle,
)
from app.services.application_template_generation import (
    prepare_application_template_generation,
)
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.services.workspace_bootstrap.service import WorkspaceBootstrapService


APPLICATION_LIFECYCLE_EVENT_NAME = "application-lifecycle"


class ApplicationLifecycleApplication(BaseModel):
    """校验创建 lifecycle 所需的最小应用身份。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    app_name: str = Field(alias="appName", min_length=1, max_length=512)


class TemplateDownloadTarget(BaseModel):
    """校验单个模板下载目标的结构化执行结果。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: Literal["pending", "succeeded", "failed"]
    path: str = Field(min_length=1, max_length=4096)
    attempt: int = Field(ge=0, le=3)
    error: str | None = Field(default=None, max_length=8192)
    repository_url: str | None = Field(default=None, alias="repositoryUrl", max_length=4096)
    branch: Literal["main", "auth"] | None = None
    commit_sha: str | None = Field(default=None, alias="commitSha", min_length=7, max_length=128)


class TemplateDownloadResult(BaseModel):
    """校验 Renderer 提交的前后端模板下载汇总。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ok: bool
    status: Literal["succeeded", "failed"]
    failed_targets: list[Literal["frontend", "backend"]] = Field(alias="failedTargets")
    targets: dict[Literal["frontend", "backend"], TemplateDownloadTarget]


class ApplicationLifecycleAction(BaseModel):
    """校验生命周期创建、读取和应用模板文件生成结果动作。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal[
        "create",
        "get",
        "prepare_template_generation",
        "complete_template_generation",
        "bootstrap_template_generation",
        "workspace_attach",
    ]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    application: ApplicationLifecycleApplication | None = None
    succeeded: bool | None = None
    error_message: str | None = Field(default=None, alias="errorMessage", max_length=2048)
    download_result: TemplateDownloadResult | None = Field(default=None, alias="downloadResult")


def application_lifecycle_capabilities() -> dict[str, Any]:
    """发布独立应用生命周期 AG-UI 动作能力。"""

    return {
        "name": "application-lifecycle",
        "endpoint": "/application-lifecycle/run",
        "transport": "ag-ui-sse",
        "stateFile": ".xcodeagent/application-lifecycle.json",
        "actionField": "forwardedProps.applicationLifecycle",
        "actions": [
            "create",
            "get",
            "prepare_template_generation",
            "complete_template_generation",
            "bootstrap_template_generation",
            "workspace_attach",
        ],
        "customEventName": APPLICATION_LIFECYCLE_EVENT_NAME,
        "stateSnapshotKey": "applicationLifecycle",
        "workflowIndependent": True,
    }


def application_lifecycle_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 forwardedProps 读取可选的生命周期 AG-UI 动作。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("applicationLifecycle")
    return value if isinstance(value, dict) else None


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
                raise ValueError("create 必须提供有效的 application.id 和 application.appName。")
            existing = load_application_lifecycle(request.workspace_root)
            if existing is not None and existing.application.id != application.id:
                raise ValueError("当前工作区已属于另一个应用，请为新应用选择独立的项目目录。")
            state = ensure_application_lifecycle(
                request.workspace_root,
                application_id=application.id,
                application_name=application.app_name,
                initialization_thread_id=str(payload.get("threadId") or "") or None,
                active_run_id=str(payload.get("runId") or "") or None,
            )
            message = "应用生命周期已创建。"
        elif request.action == "get":
            state = load_application_lifecycle(request.workspace_root)
            if state is None:
                raise ValueError("application-lifecycle.json 不存在。")
            message = "已读取应用生命周期。"
        elif request.action == "prepare_template_generation":
            if request.download_result is None:
                raise ValueError("prepare_template_generation 必须提供 downloadResult。")
            state = begin_application_template_generation(
                request.workspace_root,
                active_run_id=str(payload.get("runId") or "") or None,
            )
            manifest = await asyncio.to_thread(
                prepare_application_template_generation,
                request.workspace_root,
                request.download_result.model_dump(mode="json", by_alias=True),
            )
            message = "页面和菜单增量初始化完成。"
        elif request.action == "bootstrap_template_generation":
            if bootstrap_service is None:
                raise RuntimeError("Workspace Bootstrap 服务尚未初始化。")
            task = await bootstrap_service.trigger(request.workspace_root)
            result = await asyncio.shield(task)
            state = load_application_lifecycle(request.workspace_root)
            if state is None:
                raise RuntimeError("Bootstrap 完成后缺少 lifecycle。")
            message = "Workspace Bootstrap 已完成，可以进入工作台。"
            data = {"action": request.action, **result, "lifecycle": application_lifecycle_payload(state)}
            return AgUiActionResult(data=data, message=message)
        elif request.action == "workspace_attach":
            attached = await asyncio.to_thread(
                template_mutation_coordinator.attach_workspace,
                request.workspace_root,
            )
            state = load_application_lifecycle(request.workspace_root)
            if state is None:
                raise ValueError("application-lifecycle.json 不存在。")
            message = "Workspace Attach 已完成。"
        else:
            if request.succeeded is None:
                raise ValueError("complete_template_generation 必须提供 succeeded。")
            state = complete_application_template_generation(
                request.workspace_root,
                succeeded=request.succeeded,
                error_message=request.error_message,
                active_run_id=str(payload.get("runId") or "") or None,
            )
            message = (
                "应用模板文件生成完成，可以进入工作台。"
                if request.succeeded
                else "应用模板文件生成失败，已保留可重试状态。"
            )
        data = {"action": request.action, "lifecycle": application_lifecycle_payload(state)}
        if request.action == "workspace_attach":
            data["workspaceAttach"] = {
                "action": attached.action,
                "cleaned": attached.cleaned,
                "lifecycleChanged": attached.lifecycle_changed,
            }
        if request.action == "prepare_template_generation":
            data["templateGenerationManifest"] = manifest
        return AgUiActionResult(data=data, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=APPLICATION_LIFECYCLE_EVENT_NAME,
        state_key="applicationLifecycle",
        run_id_prefix="application-lifecycle",
        operation=operation,
        error_message_prefix="应用生命周期操作失败",
        error_data=lambda _exc: {"action": resolved_input.get("action")},
        accept=accept,
        workspace_root=str(resolved_input.get("workspaceRoot") or "") or None,
    )
