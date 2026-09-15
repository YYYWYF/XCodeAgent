"""独立 `/execution-recovery/run` AG-UI 协议与请求边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Sequence
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from app.domain.execution_recovery import (
    DurableExecutionStatus,
    RecoveryActionKind,
    RecoveryExecutionError,
)
from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.workflow import workflow_graph_for_request
from app.persistence.execution_recovery import (
    get_execution,
    list_recovery_projection_candidates,
    list_recovery_attempts_from_source,
)
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.execution_recovery_executor import (
    NativeRecoveryRuntimeContext,
    WorkflowReentryExecutor,
    prepare_stage_restart,
    prepare_native_recovery,
    prepare_operation_retry,
)
from app.services.execution_recovery_lineage import reconcile_recovery_attempt
from app.services.execution_recovery_lineage import resolve_recovery_lineage_head
from app.services.execution_recovery_lineage import RecoveryLineageState
from app.services.execution_recovery_policies import (
    production_recovery_replay_policies,
)
from app.services.execution_recovery_action_planner import (
    build_recovery_facts,
    plan_failed_node_reentry_action,
    plan_recovery_action,
)
from app.services.workflow_reentry import FailureTargetResolver


_FORBIDDEN_RECOVERY_FIELDS = {
    "node",
    "phase",
    "checkpointId",
    "checkpoint_id",
    "checkpointNs",
    "checkpoint_ns",
    "resumeFrom",
    "resume_from",
    "threadId",
    "thread_id",
    "newRunId",
    "new_run_id",
    "strategy",
    "replaySafe",
    "replay_safe",
    "handler",
    "workflowAction",
    "workflow_action",
}


def execution_recovery_capabilities() -> dict[str, Any]:
    """描述独立 Native Recovery AG-UI 入口的当前请求与错误合同。"""

    return {
        "name": "execution-recovery",
        "endpoint": "/execution-recovery/execute",
        "transport": "ag-ui-sse",
        "request": {
            "forwardedProps": {
                "workspaceRoot": "workspace used to locate the Recovery Store",
                "executionRecovery": {
                    "action": "execute | continue | retry_current_failure",
                    "incidentId": "backend-issued current recovery incident",
                    "actionId": "backend-issued recovery action",
                    "sourceRunId": "current projection hint used by continue/retry_current_failure",
                },
            },
            "clientSelectedFields": ["incidentId", "actionId", "sourceRunId"],
            "backendOwnedFields": sorted(_FORBIDDEN_RECOVERY_FIELDS),
        },
    }


def build_execution_recovery_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None = None,
    replay_policies: Sequence[Any] | None = None,
) -> AsyncIterator[str]:
    """校验恢复请求并把已准备的 Native context 交给现有 Runtime stream。"""

    encoder = EventEncoder(accept or "text/event-stream")

    async def stream() -> AsyncIterator[str]:
        """准备恢复上下文并输出标准 AG-UI 运行生命周期。"""

        message_id = str(uuid4())
        fallback_thread_id = str(uuid4())
        request_run_id = f"recovery-request-{uuid4().hex[:12]}"
        source_run_id = ""
        workspace = ""
        try:
            workspace, action, source_run_id, incident_id, action_id = _parse_request(payload)
            if action == "execute":
                source_run_id = await _resolve_action_source_run(
                    workspace=workspace,
                    incident_id=incident_id,
                    action_id=action_id,
                )
            reconciled_context = await _reconcile_prepared_lineage(
                workspace,
                source_run_id,
            )
            if reconciled_context is not None:
                source = reconciled_context.source_execution
                fallback_thread_id = source.thread_id
                if _workspace_identity(source.workspace) != _workspace_identity(workspace):
                    raise RecoveryExecutionError(
                        "INVALID_EXECUTION_RECOVERY_REQUEST",
                        "workspaceRoot 与 source execution 的 workspace 不一致。",
                    )
                async for frame in build_workflow_ag_ui_stream(
                    graph=reconciled_context.graph,
                    payload={},
                    accept=accept,
                    native_recovery_context=reconciled_context,
                ):
                    yield frame
                return
            source = await _resolve_current_recovery_source(
                workspace=workspace,
                requested_source_run_id=source_run_id,
            )
            fallback_thread_id = source.thread_id
            if _workspace_identity(source.workspace) != _workspace_identity(workspace):
                raise RecoveryExecutionError(
                    "INVALID_EXECUTION_RECOVERY_REQUEST",
                    "workspaceRoot 与 source execution 的 workspace 不一致。",
                )
            graph_factory = (
                application_planning_graph_for_request
                if source.execution_kind == "application_planning"
                else workflow_graph_for_request
            )
            graph = await graph_factory(
                workspace=workspace,
                project_id=source.project_id,
            )
            recovery_plan = None
            stage_assessment = None
            reentry_plan = None
            if source.status is DurableExecutionStatus.FAILED:
                try:
                    reentry_plan = await FailureTargetResolver().resolve(
                        workspace=workspace,
                        source=source,
                        graph=graph,
                    )
                except RecoveryExecutionError as exc:
                    action_plan = plan_failed_node_reentry_action(
                        workspace=workspace,
                        source=source,
                        error=exc,
                    )
                else:
                    action_plan = plan_failed_node_reentry_action(
                        workspace=workspace,
                        source=source,
                        reentry_plan=reentry_plan,
                    )
            else:
                recovery_plan = await _prepare_recovery_plan(
                    workspace=workspace,
                    source=source,
                    graph=graph,
                    replay_policies=replay_policies,
                )
                facts = await build_recovery_facts(
                    workspace=workspace,
                    source=source,
                    recovery_plan=recovery_plan,
                    graph=graph,
                )
                action_plan, stage_assessment = await plan_recovery_action(
                    workspace=workspace,
                    source=source,
                    recovery_plan=recovery_plan,
                    point=facts.point,
                    snapshot=facts.snapshot,
                    lifecycle=facts.lifecycle,
                    graph=graph,
                )
            if action == "execute" and (
                action_plan.incident_id != incident_id
                or action_plan.primary_action is None
                or action_plan.primary_action.action_id != action_id
            ):
                raise RecoveryExecutionError(
                    "STALE_RECOVERY_ACTION",
                    "恢复动作已经过期，请刷新当前 Recovery Incident。",
                )
            if action_plan.primary_action is None:
                raise RecoveryExecutionError(action_plan.reason_code, action_plan.message)
            kind = action_plan.primary_action.kind
            if kind is RecoveryActionKind.RETRY_FAILED_NODE:
                if reentry_plan is None:
                    raise RecoveryExecutionError(
                        "WORKFLOW_REENTRY_PLAN_INVALID",
                        "FAILED action 缺少 Workflow Re-entry 计划。",
                    )
                context = await WorkflowReentryExecutor().prepare_failure_retry(
                    workspace=workspace,
                    source_run_id=source.run_id,
                    graph=graph,
                    reentry_plan=reentry_plan,
                )
            elif kind is RecoveryActionKind.CONTINUE_CHECKPOINT:
                if recovery_plan is None:
                    raise RecoveryExecutionError(
                        "RECOVERY_ACTION_NOT_EXECUTABLE",
                        "FAILED execution 不能走 legacy continue checkpoint。",
                    )
                context = await prepare_native_recovery(
                    workspace=workspace,
                    source_run_id=source.run_id,
                    graph=graph,
                    replay_policies=(
                        replay_policies
                        if replay_policies is not None
                        else production_recovery_replay_policies()
                    ),
                )
            elif kind is RecoveryActionKind.RETRY_OPERATION:
                if recovery_plan is None:
                    raise RecoveryExecutionError(
                        "RECOVERY_ACTION_NOT_EXECUTABLE",
                        "FAILED execution 不能走 operation retry。",
                    )
                context = await prepare_operation_retry(
                    workspace=workspace,
                    source_run_id=source.run_id,
                    graph=graph,
                    recovery_plan=recovery_plan,
                )
            elif kind is RecoveryActionKind.RESTART_STAGE:
                if recovery_plan is None:
                    raise RecoveryExecutionError(
                        "RECOVERY_ACTION_NOT_EXECUTABLE",
                        "FAILED execution 不能走 stage restart。",
                    )
                context = await prepare_stage_restart(
                    workspace=workspace,
                    source_run_id=source.run_id,
                    graph=graph,
                    assessment=stage_assessment,
                )
            else:
                raise RecoveryExecutionError(
                    "RECOVERY_ACTION_NOT_EXECUTABLE",
                    "当前 RecoveryActionPlan 没有可执行的 Workbench action。",
                )
            async for frame in build_workflow_ag_ui_stream(
                graph=context.graph,
                payload={},
                accept=accept,
                native_recovery_context=context,
            ):
                yield frame
            return
        except Exception as exc:
            error_code = str(
                getattr(exc, "code", None) or "EXECUTION_RECOVERY_FAILED"
            )
            message = str(exc) or "Execution Recovery failed."
            yield encoder.encode(
                RunStartedEvent(threadId=fallback_thread_id, runId=request_run_id)
            )
            yield encoder.encode(
                TextMessageStartEvent(messageId=message_id, role="assistant")
            )
            yield encoder.encode(
                CustomEvent(
                    name="execution-recovery",
                    value={
                        "status": "failed",
                        "sourceRunId": source_run_id,
                        "errorCode": error_code,
                        "message": message,
                        **(
                            getattr(exc, "details", {})
                            if isinstance(getattr(exc, "details", {}), dict)
                            else {}
                        ),
                    },
                )
            )
            yield encoder.encode(
                TextMessageContentEvent(messageId=message_id, delta=message)
            )
            yield encoder.encode(TextMessageEndEvent(messageId=message_id))
            yield encoder.encode(
                RunErrorEvent(message=message, code=error_code)
            )

    return stream()


async def _resolve_current_recovery_source(
    *,
    workspace: str,
    requested_source_run_id: str,
) -> Any:
    """在创建 recovery child 前校验请求仍指向当前唯一 lineage head。"""

    requested = await get_execution(workspace, requested_source_run_id)
    if requested is None:
        raise RecoveryExecutionError(
            "SOURCE_EXECUTION_NOT_FOUND",
            "source execution 不存在。",
        )
    if _workspace_identity(requested.workspace) != _workspace_identity(workspace):
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "workspaceRoot 与 source execution 的 workspace 不一致。",
        )
    resolution = await resolve_recovery_lineage_head(
        workspace,
        thread_id=requested.thread_id,
        execution_kind=requested.execution_kind,
    )
    if resolution.state is RecoveryLineageState.AMBIGUOUS:
        raise RecoveryExecutionError(
            resolution.reason_code,
            "Recovery lineage 存在多个无法安全解释的当前 head。",
        )
    if resolution.head is None:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_NOT_CURRENT",
            "当前没有可用的 recovery source。",
        )
    if resolution.head.run_id != requested.run_id:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_SUPERSEDED",
            "当前 recovery source 已被新的 child execution 替代，请刷新后继续。",
            details={"currentSourceRunId": resolution.head.run_id},
        )
    return requested


def _parse_request(payload: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """在协议边界拒绝客户端伪造的恢复定位与执行 authority。"""

    forwarded = payload.get("forwardedProps")
    if not isinstance(forwarded, dict):
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "请求必须通过 forwardedProps 提交 executionRecovery。",
        )
    workspace = str(forwarded.get("workspaceRoot") or "").strip()
    recovery = forwarded.get("executionRecovery")
    if not workspace or not isinstance(recovery, dict):
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery 必须包含 workspaceRoot 和对象值。",
        )
    unexpected = sorted(
        set(recovery) - {"action", "sourceRunId", "incidentId", "actionId"}
    )
    if unexpected:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery 只允许 action、incidentId、actionId 和 sourceRunId。",
        )
    forbidden = sorted(_FORBIDDEN_RECOVERY_FIELDS.intersection(recovery))
    if forbidden:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery 不允许客户端提交：" + ", ".join(forbidden),
        )
    action = str(recovery.get("action") or "")
    if action not in {"execute", "continue", "retry_current_failure"}:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery.action 只支持 execute、continue 或 retry_current_failure。",
        )
    source_run_id = str(recovery.get("sourceRunId") or "").strip()
    incident_id = str(recovery.get("incidentId") or "").strip()
    action_id = str(recovery.get("actionId") or "").strip()
    if action == "execute" and (not incident_id or not action_id):
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "execute 必须提供 Backend 签发的 incidentId 和 actionId。",
        )
    if action != "execute" and not source_run_id:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery.sourceRunId 不能为空。",
        )
    if action == "execute" and source_run_id:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "execute 不允许客户端提交 sourceRunId。",
        )
    return workspace, action, source_run_id, incident_id, action_id


async def _prepare_recovery_plan(
    *,
    workspace: str,
    source: Any,
    graph: Any,
    replay_policies: Sequence[Any] | None,
) -> Any:
    """重新读取当前 source 的安全 RecoveryPlan，避免客户端决定执行策略。"""

    from app.services.execution_recovery_coordinator import prepare_continue

    return await prepare_continue(
        workspace=workspace,
        source_run_id=source.run_id,
        graph=graph,
        replay_policies=(
            replay_policies
            if replay_policies is not None
            else production_recovery_replay_policies()
        ),
    )


async def _resolve_action_source_run(
    *,
    workspace: str,
    incident_id: str,
    action_id: str,
) -> str:
    """按 Backend 生成的 incident/action 身份寻找当前 source，不接受客户端定位。"""

    records = await list_recovery_projection_candidates(workspace, limit=128)
    for record in records:
        graph_factory = (
            application_planning_graph_for_request
            if record.execution_kind == "application_planning"
            else workflow_graph_for_request
        )
        graph = await graph_factory(workspace=workspace, project_id=record.project_id)
        reentry_plan = None
        if record.status is DurableExecutionStatus.FAILED:
            try:
                reentry_plan = await FailureTargetResolver().resolve(
                    workspace=workspace,
                    source=record,
                    graph=graph,
                )
            except RecoveryExecutionError as exc:
                action_plan = plan_failed_node_reentry_action(
                    workspace=workspace,
                    source=record,
                    error=exc,
                )
            else:
                action_plan = plan_failed_node_reentry_action(
                    workspace=workspace,
                    source=record,
                    reentry_plan=reentry_plan,
                )
        else:
            plan = await _prepare_recovery_plan(
                workspace=workspace,
                source=record,
                graph=graph,
                replay_policies=None,
            )
            facts = await build_recovery_facts(
                workspace=workspace,
                source=record,
                recovery_plan=plan,
                graph=graph,
            )
            action_plan, _assessment = await plan_recovery_action(
                workspace=workspace,
                source=record,
                recovery_plan=plan,
                point=facts.point,
                snapshot=facts.snapshot,
                lifecycle=facts.lifecycle,
                graph=graph,
            )
        if (
            action_plan.incident_id == incident_id
            and action_plan.primary_action is not None
            and action_plan.primary_action.action_id == action_id
        ):
            return record.run_id
    raise RecoveryExecutionError(
        "STALE_RECOVERY_ACTION",
        "恢复动作已经过期，请刷新当前 Recovery Incident。",
    )


async def _reconcile_prepared_lineage(
    workspace: str,
    source_run_id: str,
) -> NativeRecoveryRuntimeContext | None:
    """在解析 canonical head 前收敛同一 source 链上的 pre-runtime child。"""

    attempts = [
        attempt
        for attempt in await list_recovery_attempts_from_source(workspace, source_run_id)
        if attempt.status.value in {"preparing", "handed_off", "finalizing"}
    ]
    if len(attempts) > 1:
        raise RecoveryExecutionError(
            "RECOVERY_LINEAGE_AMBIGUOUS",
            "Recovery source 存在多个未收敛的 child execution。",
        )
    for attempt in attempts:
        child = await get_execution(workspace, attempt.new_run_id)
        if child is None:
            continue
        graph_factory = (
            application_planning_graph_for_request
            if child.execution_kind == "application_planning"
            else workflow_graph_for_request
        )
        graph = await graph_factory(workspace=workspace, project_id=child.project_id)
        reconciled = await reconcile_recovery_attempt(
            workspace=workspace,
            new_run_id=attempt.new_run_id,
            graph=graph,
        )
        if isinstance(reconciled, NativeRecoveryRuntimeContext):
            return reconciled
    return None


def _workspace_identity(value: str) -> str:
    """把请求和持久记录中的 workspaceRoot 规范化为同一安全比较键。"""

    return str(Path(value).expanduser().resolve(strict=False))


__all__ = [
    "build_execution_recovery_ag_ui_stream",
    "execution_recovery_capabilities",
]
