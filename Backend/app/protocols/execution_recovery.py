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
from app.domain.execution_recovery import RecoveryExecutionError
from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.workflow import workflow_graph_for_request
from app.persistence.execution_recovery import (
    get_execution,
    list_recovery_attempts_from_source,
)
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.execution_recovery_executor import (
    NativeRecoveryRuntimeContext,
    prepare_native_recovery,
)
from app.services.execution_recovery_lineage import resolve_recovery_head
from app.services.execution_recovery_lineage import reconcile_recovery_attempt


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
}


def execution_recovery_capabilities() -> dict[str, Any]:
    """描述独立 Native Recovery AG-UI 入口的当前请求与错误合同。"""

    return {
        "name": "execution-recovery",
        "endpoint": "/execution-recovery/run",
        "transport": "ag-ui-sse",
        "request": {
            "forwardedProps": {
                "workspaceRoot": "workspace used to locate the Recovery Store",
                "executionRecovery": {
                    "action": "continue",
                    "sourceRunId": "backend-selected source execution",
                },
            },
            "clientSelectedFields": ["sourceRunId"],
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
            workspace, source_run_id = _parse_request(payload)
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
            head_run_id = await resolve_recovery_head(workspace, source_run_id)
            source = await get_execution(workspace, head_run_id)
            if source is None:
                raise RecoveryExecutionError(
                    "SOURCE_EXECUTION_NOT_FOUND",
                    "source execution 不存在。",
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
            context = await prepare_native_recovery(
                workspace=workspace,
                source_run_id=head_run_id,
                graph=graph,
                replay_policies=replay_policies,
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


def _parse_request(payload: dict[str, Any]) -> tuple[str, str]:
    """在协议边界拒绝客户端伪造的恢复定位与执行 authority。"""

    unexpected = sorted(set(payload) - {"forwardedProps"})
    if unexpected:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "execution-recovery 请求只允许 forwardedProps。",
        )
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
    forbidden = sorted(_FORBIDDEN_RECOVERY_FIELDS.intersection(recovery))
    if forbidden:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery 不允许客户端提交：" + ", ".join(forbidden),
        )
    if str(recovery.get("action") or "") != "continue":
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery.action 只支持 continue。",
        )
    source_run_id = str(recovery.get("sourceRunId") or "").strip()
    if not source_run_id:
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "executionRecovery.sourceRunId 不能为空。",
        )
    return workspace, source_run_id


async def _reconcile_prepared_lineage(
    workspace: str,
    source_run_id: str,
) -> NativeRecoveryRuntimeContext | None:
    """在解析 canonical head 前收敛同一 source 链上的 PREPARING/HANDED_OFF child。"""

    for attempt in await list_recovery_attempts_from_source(workspace, source_run_id):
        if attempt.status.value not in {"preparing", "handed_off"}:
            continue
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
