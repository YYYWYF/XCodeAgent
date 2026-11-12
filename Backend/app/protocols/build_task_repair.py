"""独立单任务修复 AG-UI 入口；生成草稿和明确确认执行使用不同动作。"""

from app.domain.build_task_repair import BuildTaskRepairInput
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.build_task_repair import repair_action


def build_task_repair_capabilities() -> dict:
    """公开当前单任务修复协议和不改动其他任务的确认边界。"""
    return {"endpoint": "/build-task-repair/run", "transport": "ag-ui-sse",
        "actionField": "forwardedProps.buildTaskRepair", "actions": ["get", "generate", "regenerate", "discard", "confirm"],
        "customEventName": "build-task-repair", "stateSnapshotKey": "buildTaskRepair",
        "scope": "single", "requiresConfirmation": True, "requiresDrainedBuild": True}


async def build_task_repair_stream(*, payload: dict, accept: str | None = None):
    """所有失败均走完整生命周期；确认后交给现有标准 Workflow 执行流。"""
    prepared = None
    request = None
    try:
        request = BuildTaskRepairInput.model_validate((payload.get("forwardedProps") or {}).get("buildTaskRepair"))
        run_id = payload.get("runId")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("必须提供前端生成的 runId。")
        if request.action == "confirm":
            if payload.get("threadId") != request.sourceThreadId:
                raise ValueError("执行线程必须与原 Build 一致。")
            from app.services.build_task_repair_execution import prepare_repair_execution
            prepared = await prepare_repair_execution(request, run_id)
        else:
            async def operation(progress):
                """只读诊断和草稿操作不会执行任务，成功结果明确保留确认边界。"""
                data = await repair_action(request, progress)
                return AgUiActionResult(data=data, message="修复草稿已准备，请审阅后确认。" if data.get("draft") else "任务修复草稿已读取或放弃。")

            async for frame in build_ag_ui_action_stream(payload=payload, event_name="build-task-repair",
                    state_key="buildTaskRepair", run_id_prefix="build-task-repair", progress_operation=operation,
                    error_message_prefix="任务修复失败", accept=accept):
                yield frame
            return
    except Exception as exc:
        message = str(exc)
        async def failed():
            """准入或确认失败保留原任务与草稿，不将失败伪装成修复成功。"""
            raise ValueError(message)
        async for frame in build_ag_ui_action_stream(payload=payload, event_name="build-task-repair",
                state_key="buildTaskRepair", run_id_prefix="build-task-repair", operation=failed,
                error_message_prefix="任务修复失败", accept=accept, register_workspace_run=False):
            yield frame
        return
    runtime, original, committed_digest, draft = prepared
    from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
    try:
        async for frame in build_workflow_ag_ui_stream(graph=runtime.graph, payload={}, accept=accept,
                native_recovery_context=runtime):
            yield frame
    finally:
        if not runtime.runtime_started:
            from app.services.build_task_repair_execution import rollback_repair_plan
            from app.services.retry_preparation import abandon_prepared_retry
            from app.services.failed_run_cleanup import shield_run_cleanup
            try:
                rollback_repair_plan(request, original, committed_digest, draft)
            finally:
                await shield_run_cleanup(abandon_prepared_retry(workspace=runtime.workspace,
                    source=runtime.source_execution, before=runtime.retry_lifecycle_before_handoff,
                    new_run_id=runtime.new_run_id, lease=runtime.workspace_lease))
