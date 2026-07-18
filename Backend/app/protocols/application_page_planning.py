from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.services.requirement_spec import (
    SaveRequirementSpecDraftRequest,
    save_requirement_spec_draft,
)


REQUIREMENT_SPEC_DRAFT_EVENT_NAME = "requirement-spec-draft"


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

    # 专用端点的范围由服务端固定，避免前端重启或恢复时丢失 forwardedProps 后重新开放需求澄清。
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
