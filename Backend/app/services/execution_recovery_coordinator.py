"""P0.3A 只读恢复协调器：选择并验证现场，但不执行 Graph。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import get_execution
from app.services.application_lifecycle import load_application_lifecycle
from app.services.execution_recovery_selector import (
    RecoveryPointSelection,
    RecoveryPointSelector,
)
from app.services.execution_recovery_strategy import (
    RecoveryContext,
    RecoveryReplayPolicy,
    RecoveryStrategyAssessment,
    RecoveryStrategyResolver,
)
from app.services.workspace_inspector import (
    INSPECTOR_SCHEMA_VERSION,
    snapshot_hash,
    workspace_inventory,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


@dataclass(frozen=True)
class _CheckpointValidation:
    """保存单个 checkpoint 读取、身份和后继节点校验结果。"""

    snapshot: Any | None = None
    decision: RecoveryDecision | None = None
    reason_code: str | None = None
    reason: str | None = None
    lifecycle_revision: int | None = None
    workspace_revision: str | None = None
    workspace_snapshot_hash: str | None = None


class RecoveryCoordinator:
    """把中断执行转换为纯 RecoveryPlan，不创建新的执行尝试。"""

    def __init__(
        self,
        *,
        selector: RecoveryPointSelector | None = None,
        strategy_resolver: RecoveryStrategyResolver | Callable[[RecoveryContext], RecoveryStrategyAssessment] | None = None,
        replay_policies: Sequence[RecoveryReplayPolicy] | None = None,
    ) -> None:
        """保存可替换的现场选择器和 replay safety 策略。"""

        self._selector = selector or RecoveryPointSelector()
        if strategy_resolver is not None:
            self._strategy_resolver = strategy_resolver
        else:
            self._strategy_resolver = RecoveryStrategyResolver(replay_policies)

    async def prepare_continue(
        self,
        *,
        workspace: str,
        source_run_id: str,
        graph: Any,
    ) -> RecoveryPlan:
        """读取 source execution 并返回安全恢复判断，绝不启动 Graph。"""

        source = await get_execution(workspace, source_run_id)
        if source is None:
            return _plan_without_source(
                source_run_id=source_run_id,
                decision=RecoveryDecision.NOT_RECOVERABLE,
                strategy=RecoveryStrategy.NONE,
                reason_code="SOURCE_EXECUTION_NOT_FOUND",
                reason="找不到要继续执行的 source execution。",
            )

        status_plan = _source_status_plan(source)
        if status_plan is not None:
            return status_plan

        selection = await self._selector.select_recovery_point(
            workspace=workspace,
            source=source,
            graph=graph,
        )
        if selection.point is not None and not selection.candidates:
            return _plan_from_point(
                source=source,
                point=selection.point,
                decision=RecoveryDecision.INVALID_RECOVERY_POINT,
                strategy=RecoveryStrategy.NONE,
                reason_code=selection.reason_code,
                reason=selection.reason,
            )
        if selection.point is None:
            return _plan_from_selection_failure(source, selection)

        last_failure: _CheckpointValidation | None = None
        for point in selection.candidates or (selection.point,):
            validation = await _validate_checkpoint(
                workspace=workspace,
                source=source,
                point=point,
                graph=graph,
            )
            if validation.decision is not None:
                if validation.decision is not RecoveryDecision.INVALID_RECOVERY_POINT:
                    return _plan_from_validation(source, point, validation)
                last_failure = validation
                continue

            assert validation.snapshot is not None
            assessment = self._resolve_strategy(
                RecoveryContext(
                    source=source,
                    point=point,
                    snapshot=validation.snapshot,
                    lifecycle_revision=validation.lifecycle_revision,
                    workspace_revision=validation.workspace_revision,
                    workspace_snapshot_hash=validation.workspace_snapshot_hash,
                )
            )
            return _plan_from_assessment(
                source=source,
                point=point,
                assessment=assessment,
                lifecycle_revision=validation.lifecycle_revision,
                workspace_revision=validation.workspace_revision,
                workspace_snapshot_hash=validation.workspace_snapshot_hash,
            )

        if last_failure is not None:
            point = selection.candidates[-1]
            return _plan_from_validation(source, point, last_failure)
        return _plan_from_selection_failure(source, selection)

    def _resolve_strategy(self, context: RecoveryContext) -> RecoveryStrategyAssessment:
        """兼容对象式 resolver 与函数式 resolver 的注入方式。"""

        resolver = self._strategy_resolver
        if hasattr(resolver, "resolve"):
            return resolver.resolve(context)  # type: ignore[union-attr]
        return resolver(context)  # type: ignore[misc]


async def prepare_continue(
    *,
    workspace: str,
    source_run_id: str,
    graph: Any,
    selector: RecoveryPointSelector | None = None,
    strategy_resolver: RecoveryStrategyResolver | Callable[[RecoveryContext], RecoveryStrategyAssessment] | None = None,
    replay_policies: Sequence[RecoveryReplayPolicy] | None = None,
) -> RecoveryPlan:
    """提供 P0.3A 的模块级入口，名称明确表示当前只准备而不执行。"""

    return await RecoveryCoordinator(
        selector=selector,
        strategy_resolver=strategy_resolver,
        replay_policies=replay_policies,
    ).prepare_continue(
        workspace=workspace,
        source_run_id=source_run_id,
        graph=graph,
    )


async def _validate_checkpoint(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    graph: Any,
) -> _CheckpointValidation:
    """用精确 config 回查真实 LangGraph checkpoint 并验证其 successor。"""

    if not hasattr(graph, "aget_state"):
        return _invalid_checkpoint(
            "CHECKPOINT_NOT_FOUND",
            "提供的 Graph 不支持读取真实 checkpoint。",
        )
    config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": point.checkpoint_ns,
            "checkpoint_id": point.checkpoint_id,
        }
    }
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        return _invalid_checkpoint(
            "CHECKPOINT_NOT_FOUND",
            "真实 LangGraph checkpoint 不存在或无法读取。",
        )
    if snapshot is None:
        return _invalid_checkpoint(
            "CHECKPOINT_NOT_FOUND",
            "真实 LangGraph checkpoint 不存在。",
        )

    identity = _snapshot_identity(snapshot)
    if identity is None:
        return _invalid_checkpoint(
            "CHECKPOINT_NOT_FOUND",
            "真实 checkpoint 没有完整的 threadId、checkpointNs、checkpointId。",
        )
    actual_thread_id, actual_checkpoint_ns, actual_checkpoint_id = identity
    if actual_thread_id != source.thread_id or actual_thread_id != point.thread_id:
        return _invalid_checkpoint(
            "CHECKPOINT_THREAD_MISMATCH",
            "真实 checkpoint 的 threadId 与 source execution 不一致。",
        )
    if (
        actual_checkpoint_ns != point.checkpoint_ns
        or actual_checkpoint_id != point.checkpoint_id
    ):
        return _invalid_checkpoint(
            "CHECKPOINT_NOT_FOUND",
            "真实 checkpoint 身份与 RecoveryPoint 不完全一致。",
        )

    actual_next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
    if actual_next_nodes != point.next_nodes:
        return _invalid_checkpoint(
            "CHECKPOINT_NEXT_MISMATCH",
            "真实 checkpoint 的 nextNodes 与 RecoveryPoint 不一致。",
        )
    if _snapshot_requires_user_input(snapshot):
        return _CheckpointValidation(
            snapshot=snapshot,
            decision=RecoveryDecision.AWAITING_USER,
            reason_code="CHECKPOINT_REQUIRES_USER_INPUT",
            reason="真实 checkpoint 正在等待用户业务输入，必须走结构化 interaction resume。",
        )

    lifecycle = _validate_lifecycle(workspace=workspace, source=source, point=point)
    if lifecycle.decision is not None:
        return _CheckpointValidation(**{**lifecycle.__dict__, "snapshot": snapshot})
    workspace_state = _validate_workspace(workspace=workspace, point=point)
    if workspace_state.decision is not None:
        return _CheckpointValidation(
            **{
                **workspace_state.__dict__,
                "snapshot": snapshot,
                "lifecycle_revision": lifecycle.lifecycle_revision,
            },
        )
    return _CheckpointValidation(
        snapshot=snapshot,
        lifecycle_revision=lifecycle.lifecycle_revision,
        workspace_revision=workspace_state.workspace_revision,
        workspace_snapshot_hash=workspace_state.workspace_snapshot_hash,
    )


def _validate_lifecycle(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
) -> _CheckpointValidation:
    """保守比较 lifecycle revision，并区分允许缺失的 application planning。"""

    try:
        lifecycle = load_application_lifecycle(workspace)
    except (OSError, ValueError):
        return _requires_handler_state(
            "LIFECYCLE_STATE_MISSING",
            "当前 ApplicationLifecycle 无法安全读取。",
        )
    if lifecycle is None:
        if source.execution_kind == "application_planning":
            return _CheckpointValidation()
        return _requires_handler_state(
            "LIFECYCLE_STATE_MISSING",
            "Workbench execution 缺少应存在的 ApplicationLifecycle。",
        )
    if (
        point.lifecycle_revision is not None
        and lifecycle.revision != point.lifecycle_revision
    ):
        return _invalid_state(
            "LIFECYCLE_DRIFT",
            "当前 ApplicationLifecycle revision 已偏离 RecoveryPoint。",
        )
    return _CheckpointValidation(lifecycle_revision=lifecycle.revision)


def _validate_workspace(
    *,
    workspace: str,
    point: RecoveryPoint,
) -> _CheckpointValidation:
    """从当前磁盘重新计算 revision，再严格比较对应 snapshot hash。"""

    if point.workspace_revision is None and point.workspace_snapshot_hash is None:
        return _CheckpointValidation()

    if point.workspace_revision is None:
        return _requires_handler_state(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "RecoveryPoint 缺少 workspaceRevision，无法验证当前磁盘状态。",
        )

    workspace_root = Path(workspace).expanduser().resolve()
    if not workspace_root.is_dir():
        return _requires_handler_state(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "当前工作区不存在，无法安全读取真实磁盘状态。",
        )
    try:
        _files, current_revision = workspace_inventory(workspace_root)
    except (OSError, ValueError):
        return _requires_handler_state(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "当前工作区状态无法安全读取。",
        )

    if not current_revision:
        return _requires_handler_state(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "当前工作区未能生成有效 revision，无法安全验证恢复现场。",
        )
    if current_revision != point.workspace_revision:
        return _invalid_state(
            "WORKSPACE_DRIFT",
            "当前 workspace revision 已偏离 RecoveryPoint。",
            workspace_revision=current_revision,
        )

    if point.workspace_snapshot_hash is None:
        return _CheckpointValidation(workspace_revision=current_revision)

    snapshot = _load_workspace_snapshot_for_revision(workspace, current_revision)
    if snapshot is None:
        return _requires_handler_state(
            "WORKSPACE_SNAPSHOT_UNAVAILABLE",
            "当前 workspace revision 缺少可验证的 snapshot 证据。",
        )
    current_hash = snapshot_hash(snapshot)
    if current_hash != point.workspace_snapshot_hash:
        return _invalid_state(
            "WORKSPACE_DRIFT",
            "当前 workspace snapshot hash 已偏离 RecoveryPoint。",
            workspace_revision=current_revision,
            workspace_snapshot_hash=current_hash,
        )
    return _CheckpointValidation(
        workspace_revision=current_revision,
        workspace_snapshot_hash=current_hash,
    )


def validate_recovery_workspace_state(
    *,
    workspace: str,
    point: RecoveryPoint,
) -> _CheckpointValidation:
    """复用 P0.3A 的当前磁盘校验，供 finalization 在 fork 前重验。"""

    return _validate_workspace(workspace=workspace, point=point)


def _load_workspace_snapshot_for_revision(
    workspace: str,
    revision: str,
) -> dict[str, Any] | None:
    """按精确 revision 和当前 inspector schema 读取已有 snapshot。"""

    roots = (
        Path(workspace).expanduser().resolve() / ".xcodeagent" / "cache" / "workspace-snapshots",
        Path(workspace).expanduser().resolve() / "cache" / "workspace-snapshots",
    )
    for root in roots:
        path = root / f"{revision}.{INSPECTOR_SCHEMA_VERSION}.json"
        if not path.is_file():
            continue
        try:
            snapshot = load_workspace_snapshot_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(snapshot, dict):
            return snapshot
    return None


def _snapshot_identity(snapshot: Any) -> tuple[str, str, str] | None:
    """从 StateSnapshot.config 提取完整 checkpoint 身份。"""

    config = getattr(snapshot, "config", None)
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = _optional_text(configurable.get("thread_id"))
    checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
    checkpoint_id = _optional_text(configurable.get("checkpoint_id"))
    if thread_id is None or checkpoint_id is None:
        return None
    return thread_id, checkpoint_ns, checkpoint_id


def _snapshot_requires_user_input(snapshot: Any) -> bool:
    """检测 StateSnapshot 的 interrupt、task metadata 和状态等待标记。"""

    for task in getattr(snapshot, "tasks", ()) or ():
        if getattr(task, "interrupts", ()):
            return True
        metadata = getattr(task, "metadata", {})
        if isinstance(metadata, dict) and _metadata_requires_user_input(metadata):
            return True
    metadata = getattr(snapshot, "metadata", {})
    if isinstance(metadata, dict) and _metadata_requires_user_input(metadata):
        return True
    values = getattr(snapshot, "values", {})
    if isinstance(values, dict):
        return _status_requires_user_input(values.get("status")) or _status_requires_user_input(
            values.get("state_status")
        )
    return False


def _metadata_requires_user_input(metadata: dict[str, Any]) -> bool:
    """判断 metadata 中是否存在明确的人工输入等待标记。"""

    for key in ("awaiting_user", "requires_user_input", "pending_user_input"):
        if metadata.get(key) is True:
            return True
    return any(
        _status_requires_user_input(metadata.get(key))
        for key in ("status", "state_status", "phase")
    )


def _status_requires_user_input(value: Any) -> bool:
    """规范化状态文本并识别用户输入等待状态。"""

    return str(value or "").strip().lower() in {
        "awaiting_user",
        "requires_user_input",
        "pending_user_input",
        "awaiting_confirmation",
        "pending_user_confirmation",
    }


def _source_status_plan(source: DurableExecutionRecord) -> RecoveryPlan | None:
    """为不应进入通用 checkpoint 分析的 source 状态返回明确结果。"""

    if source.status is DurableExecutionStatus.RUNNING:
        return _plan_from_source(
            source=source,
            decision=RecoveryDecision.NOT_RECOVERABLE,
            strategy=RecoveryStrategy.NONE,
            reason_code="SOURCE_EXECUTION_STILL_RUNNING",
            reason="source execution 仍在运行，应先由 P0.2 liveness scanner 收敛。",
        )
    if source.status is DurableExecutionStatus.COMPLETED:
        return _plan_from_source(
            source=source,
            decision=RecoveryDecision.NOT_RECOVERABLE,
            strategy=RecoveryStrategy.NONE,
            reason_code="SOURCE_EXECUTION_COMPLETED",
            reason="已完成的 source execution 不允许从历史 checkpoint 重放。",
        )
    if source.status is DurableExecutionStatus.AWAITING_USER:
        return _plan_from_source(
            source=source,
            decision=RecoveryDecision.AWAITING_USER,
            strategy=RecoveryStrategy.NONE,
            reason_code="SOURCE_AWAITING_USER",
            reason="source execution 正在等待结构化用户交互，不走通用 Continue。",
        )
    return None


def _invalid_checkpoint(reason_code: str, reason: str) -> _CheckpointValidation:
    """构造可继续尝试历史候选的 checkpoint 无效结果。"""

    return _CheckpointValidation(
        decision=RecoveryDecision.INVALID_RECOVERY_POINT,
        reason_code=reason_code,
        reason=reason,
    )


def _invalid_state(
    reason_code: str,
    reason: str,
    *,
    lifecycle_revision: int | None = None,
    workspace_revision: str | None = None,
    workspace_snapshot_hash: str | None = None,
) -> _CheckpointValidation:
    """构造阻止 replay 的 lifecycle/workspace state drift 结果。"""

    return _CheckpointValidation(
        decision=RecoveryDecision.STATE_DRIFT,
        reason_code=reason_code,
        reason=reason,
        lifecycle_revision=lifecycle_revision,
        workspace_revision=workspace_revision,
        workspace_snapshot_hash=workspace_snapshot_hash,
    )


def _requires_handler_state(
    reason_code: str,
    reason: str,
) -> _CheckpointValidation:
    """构造需要领域 Handler 介入的状态缺失结果。"""

    return _CheckpointValidation(
        decision=RecoveryDecision.REQUIRES_HANDLER,
        reason_code=reason_code,
        reason=reason,
    )


def _plan_from_source(
    *,
    source: DurableExecutionRecord,
    decision: RecoveryDecision,
    strategy: RecoveryStrategy,
    reason_code: str,
    reason: str,
) -> RecoveryPlan:
    """从 source execution 生成不包含 checkpoint 的结果计划。"""

    return RecoveryPlan(
        source_run_id=source.run_id,
        thread_id=source.thread_id,
        decision=decision,
        strategy=strategy,
        reason_code=reason_code,
        reason=reason,
    )


def _plan_without_source(
    *,
    source_run_id: str,
    decision: RecoveryDecision,
    strategy: RecoveryStrategy,
    reason_code: str,
    reason: str,
) -> RecoveryPlan:
    """为缺失 source execution 生成最低限度的结果计划。"""

    return RecoveryPlan(
        source_run_id=source_run_id,
        decision=decision,
        strategy=strategy,
        reason_code=reason_code,
        reason=reason,
    )


def _plan_from_selection_failure(
    source: DurableExecutionRecord,
    selection: RecoveryPointSelection,
) -> RecoveryPlan:
    """把没有候选现场的选择结果转换为要求 Handler 的计划。"""

    return _plan_from_source(
        source=source,
        decision=RecoveryDecision.REQUIRES_HANDLER,
        strategy=RecoveryStrategy.HANDLER,
        reason_code=selection.reason_code,
        reason=selection.reason,
    )


def _plan_from_point(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    decision: RecoveryDecision,
    strategy: RecoveryStrategy,
    reason_code: str,
    reason: str,
) -> RecoveryPlan:
    """把现场索引的稳定引用复制到 RecoveryPlan，而不复制其完整状态。"""

    return RecoveryPlan(
        source_run_id=source.run_id,
        thread_id=source.thread_id,
        decision=decision,
        strategy=strategy,
        recovery_point_id=point.recovery_point_id,
        checkpoint_id=point.checkpoint_id,
        checkpoint_ns=point.checkpoint_ns,
        next_nodes=list(point.next_nodes),
        reason_code=reason_code,
        reason=reason,
        lifecycle_revision=point.lifecycle_revision,
        workspace_revision=point.workspace_revision,
        workspace_snapshot_hash=point.workspace_snapshot_hash,
    )


def _plan_from_validation(
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    validation: _CheckpointValidation,
) -> RecoveryPlan:
    """把 checkpoint 校验失败结果转换为带稳定现场引用的计划。"""

    return _plan_from_point(
        source=source,
        point=point,
        decision=validation.decision or RecoveryDecision.INVALID_RECOVERY_POINT,
        strategy=(
            RecoveryStrategy.HANDLER
            if validation.decision is RecoveryDecision.REQUIRES_HANDLER
            else RecoveryStrategy.NONE
        ),
        reason_code=validation.reason_code or "INVALID_RECOVERY_POINT",
        reason=validation.reason or "RecoveryPoint 校验失败。",
    )


def _plan_from_assessment(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    assessment: RecoveryStrategyAssessment,
    lifecycle_revision: int | None,
    workspace_revision: str | None,
    workspace_snapshot_hash: str | None,
) -> RecoveryPlan:
    """把策略评估结果与已校验的当前状态引用组合成最终计划。"""

    plan = _plan_from_point(
        source=source,
        point=point,
        decision=assessment.decision,
        strategy=assessment.strategy,
        reason_code=assessment.reason_code,
        reason=assessment.reason,
    )
    return plan.model_copy(
        update={
            "lifecycle_revision": lifecycle_revision,
            "workspace_revision": workspace_revision,
            "workspace_snapshot_hash": workspace_snapshot_hash,
        }
    )


def _optional_text(value: Any) -> str | None:
    """把可选身份字段转换为非空文本。"""

    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
