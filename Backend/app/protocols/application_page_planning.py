from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from app.protocols.workflow import build_workflow_ag_ui_stream


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
        "writesApplicationJsonAfterConfirmation": True,
        "persistedPlanningFields": ["planning.requirementSpec", "planning.projectPlan"],
        "deferredApplicationFields": ["menus", "apis", "schemas", "dataSources"],
        "mainWorkflowIndependent": True,
    }


def build_application_page_planning_ag_ui_stream(
    *,
    graph: Callable[..., Any],
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """使用主 Workflow 的稳定 AG-UI 投射运行独立两节点 Graph。"""

    return build_workflow_ag_ui_stream(graph=graph, payload=payload, accept=accept)
