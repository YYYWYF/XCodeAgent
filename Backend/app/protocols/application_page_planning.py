from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.services.requirement_spec import (
    SaveRequirementSpecDraftRequest,
    save_requirement_spec_draft,
)
from app.services.application_lifecycle import (
    complete_application_template_generation,
    application_lifecycle_payload,
    ensure_application_lifecycle,
    load_application_lifecycle,
)


REQUIREMENT_SPEC_DRAFT_EVENT_NAME = "requirement-spec-draft"
APPLICATION_LIFECYCLE_EVENT_NAME = "application-lifecycle"


class ApplicationLifecycleApplication(BaseModel):
    """校验创建 lifecycle 所需的最小应用身份。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    app_name: str = Field(alias="appName", min_length=1, max_length=512)


class ApplicationLifecycleAction(BaseModel):
    """校验生命周期创建、读取和应用模板文件生成结果动作。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["create", "get", "complete_template_generation"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    application: ApplicationLifecycleApplication | None = None
    succeeded: bool | None = None
    error_message: str | None = Field(default=None, alias="errorMessage", max_length=2048)


def application_page_planning_capabilities() -> dict[str, Any]:
    """发布创建应用专用两节点 Workflow 的 AG-UI 能力。"""

    return {
        "name": "application-page-planning",
        "endpoint": "/application-page-planning/run",
        "transport": "ag-ui-sse",
        "eventProtocol": "xcodeagent.workflow.event.v1",
        "stateSnapshotKey": "workflow",
        "customEventName": "workflow-run",
        "phases": ["requirements", "project_planning"],
        "confirmationArtifacts": ["requirement_spec", "project_plan"],
        "editableArtifacts": {
            "requirement_spec": {
                "requestField": "forwardedProps.editedRequirementSpec",
                "saveActionField": "forwardedProps.requirementSpecDraft",
                "actions": ["save"],
                "writes": ["requirement-spec.md", "requirement-spec.json"],
            }
        },
        "lifecycle": {
            "stateFile": ".xcodeagent/application-lifecycle.json",
            "stateField": "initialization",
            "actionField": "forwardedProps.applicationLifecycle",
            "actions": ["create", "get", "complete_template_generation"],
            "customEventName": APPLICATION_LIFECYCLE_EVENT_NAME,
        },
        "writesApplicationJsonAfterConfirmation": False,
        "artifactDirectories": [".xcodeagent/specs", ".xcodeagent/plans"],
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
    draft_input = _requirement_spec_draft_input(normalized_payload)
    if draft_input is not None:
        return _build_requirement_spec_draft_ag_ui_stream(
            payload=normalized_payload,
            draft_input=draft_input,
            accept=accept,
        )
    lifecycle_input = _application_lifecycle_input(normalized_payload)
    if lifecycle_input is not None:
        return _build_application_lifecycle_ag_ui_stream(
            payload=normalized_payload,
            lifecycle_input=lifecycle_input,
            accept=accept,
        )
    return build_workflow_ag_ui_stream(
        graph=graph,
        payload=normalized_payload,
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


def _requirement_spec_draft_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 读取可选的需求草稿保存请求。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("requirementSpecDraft")
    return value if isinstance(value, dict) else None


def _build_application_lifecycle_ag_ui_stream(
    *,
    payload: dict[str, Any],
    lifecycle_input: dict[str, Any],
    accept: str | None,
) -> AsyncIterator[str]:
    """通过现有规划端点执行生命周期创建、读取和模板文件生成裁定。"""

    async def operation() -> AgUiActionResult:
        """校验动作并返回唯一权威 lifecycle 快照。"""

        request = ApplicationLifecycleAction.model_validate(lifecycle_input)
        if request.action == "create":
            application = request.application
            if application is None:
                raise ValueError("create 必须提供有效的 application.id 和 application.appName。")
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
        return AgUiActionResult(
            data={"action": request.action, "lifecycle": application_lifecycle_payload(state)},
            message=message,
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=APPLICATION_LIFECYCLE_EVENT_NAME,
        state_key="applicationLifecycle",
        run_id_prefix="application-lifecycle",
        operation=operation,
        error_message_prefix="应用生命周期操作失败",
        error_data=lambda _exc: {"action": lifecycle_input.get("action")},
        accept=accept,
    )


def _application_lifecycle_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 forwardedProps 读取可选的生命周期 AG-UI 动作。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("applicationLifecycle")
    return value if isinstance(value, dict) else None
