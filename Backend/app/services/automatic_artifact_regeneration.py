"""将开发前发现的失效产物交给现有规划图，沿用正式确认和 continuation。"""

from types import SimpleNamespace
from typing import Any

from app.services.stage_prerequisites import regeneration_for_retry


async def prepare_automatic_regeneration(inputs: dict[str, Any], *, run_id: str, thread_id: str):
    """在开发执行入场前选择最早失效产物，原目标由生命周期持有直到确认后恢复。"""
    if inputs.get("workflow_scope") or inputs.get("workflow_action") or inputs.get("ended_resume"):
        return None
    workspace = inputs.get("workspace")
    if not workspace:
        return None
    values = inputs.get("resume_values") or {}
    node = inputs.get("resume_from") or "api_design_readiness_gate"
    issue = regeneration_for_retry(workspace, node, state={**values, "workspace": workspace})
    if issue is None:
        return None
    from app.services.retry_node_state import retry_node_state
    if issue.key == "build_task_plan":
        # 旧任务失效只重开当前目标的规划，不进入旧 Build 或替用户确认新计划。
        inputs["resume_from"] = "api_design_readiness_gate"
        values = retry_node_state(values, "prepare_build_tasks", planning=False)
        inputs["resume_values"] = values
        return None
    from app.graph.application_planning_workflow import application_planning_graph_for_request
    from app.services.artifact_regeneration_handoff import handoff_artifact_generation
    graph = await application_planning_graph_for_request(workspace=workspace, project_id=inputs.get("project_id"))
    scope = values.get("build_execution_scope") or {}
    target = (
        {"type": "page", "pageId": scope["targetId"]} if scope.get("type") == "page"
        else {"type": "endpoint", "endpointId": scope["targetId"], "apiContractId": scope.get("apiContractId")}
        if scope.get("type") == "endpoint" else {"type": "application"}
    )
    lifecycle = handoff_artifact_generation(
        SimpleNamespace(workspace=workspace, run_id=run_id, thread_id=thread_id,
            owner_session_id=values.get("owner_session_id", "")),
        new_run_id=run_id, node=issue.node, request=inputs["request"], target=target,
    )
    values = retry_node_state(values, issue.node, planning=True)
    values.update(change_id=lifecycle.active_formal_revision.change_id,
        change_target=lifecycle.active_formal_revision.target.model_dump(mode="json", by_alias=True))
    inputs.update(workflow_scope="application_planning", resume_values=values,
        resume_from="technical_planning" if issue.node == "technical_planning_begin" else issue.node)
    return graph
