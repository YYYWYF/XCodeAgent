"""通过各自的现行读取入口验证文档审阅，不依赖已撤销的统一恢复快照。"""

import json

from app.services.document_review_assets import project_document_review_availability
from app.services.recovery_checkpoint import checkpoint_thread_exists
from app.services.session_workflow_projection import project_session_workflow


async def read_planning_review(*, workspace, application_id, thread_id, graph=None):
    """消费独立规划 AG-UI 读取结果，供原生确认和错误边界测试使用。"""
    from app.graph.application_planning_workflow import application_planning_graph_for_request
    from app.protocols.application_page_planning import _build_application_planning_recovery_ag_ui_stream
    frames = [frame async for frame in _build_application_planning_recovery_ag_ui_stream(
        graph=graph or application_planning_graph_for_request,
        payload={"threadId": thread_id, "runId": "read-review"},
        recovery_input={"action": "get", "workspaceRoot": workspace, "applicationId": application_id},
        accept=None)]
    events = [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]
    result = next(event["value"] for event in events if event["type"] == "CUSTOM")
    if result.get("status") == "failed":
        from app.domain.execution_recovery import RecoveryExecutionError
        raise RecoveryExecutionError(result.get("code") or "READ_FAILED", result.get("error", {}).get("message") or "读取失败")
    return result if result.get("checkpointAvailable") is not False else None, None, None


async def read_document_review_state(*, workspace, application_id, thread_id, session_id=None, planning=False):
    """分别核对文档可用性、现场存在性和原接口 Workflow，不拼装业务执行权限。"""
    review = await project_document_review_availability(workspace, thread_id=thread_id, planning=planning)
    present = await checkpoint_thread_exists(workspace, thread_id)
    workflow = None
    if present and review is None:
        workflow = (await read_planning_review(workspace=workspace, application_id=application_id,
            thread_id=thread_id))[0] if planning else await project_session_workflow(
                workspace, session_id=session_id or "", thread_id=thread_id)
    return {"checkpointAvailable": present, "workflow": workflow, "documentReview": review}
