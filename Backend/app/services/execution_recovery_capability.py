"""统一定义 Native Recovery 是否真的可以由 Executor 执行。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.execution_recovery import (
    RecoveryDecision,
    RecoveryPlan,
    RecoveryStrategy,
)


@dataclass(frozen=True, slots=True)
class NativeRecoveryCapability:
    """保存一次 RecoveryPlan 的 Native 可执行性与拒绝原因。"""

    executable: bool
    reason_code: str
    reason: str


def assess_native_recovery_capability(
    plan: RecoveryPlan,
) -> NativeRecoveryCapability:
    """按 Executor 的硬约束判断 RecoveryPlan 是否可以 Native replay。"""

    if plan.decision is not RecoveryDecision.READY_NATIVE:
        return _unavailable(
            "NATIVE_DECISION_REQUIRED",
            "RecoveryPlan 不是明确允许 Native Recovery 的 READY_NATIVE 结果。",
        )
    if plan.strategy is not RecoveryStrategy.NATIVE_CHECKPOINT:
        return _unavailable(
            "NATIVE_STRATEGY_REQUIRED",
            "RecoveryPlan 没有选择 NATIVE_CHECKPOINT 执行策略。",
        )
    if not plan.recovery_point_id:
        return _unavailable(
            "NATIVE_RECOVERY_POINT_MISSING",
            "RecoveryPlan 缺少 Native RecoveryPoint 身份。",
        )
    if not plan.checkpoint_id:
        return _unavailable(
            "NATIVE_CHECKPOINT_MISSING",
            "RecoveryPlan 缺少 Native checkpoint 身份。",
        )
    if plan.checkpoint_ns != "":
        return _unavailable(
            "NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED",
            "P0.3B 暂不支持非 root checkpoint namespace replay。",
        )
    if len(plan.next_nodes) != 1:
        return _unavailable(
            "NATIVE_PARALLEL_REPLAY_UNSUPPORTED",
            "P0.3B 暂不支持并行 next nodes replay。",
        )
    return NativeRecoveryCapability(
        executable=True,
        reason_code="NATIVE_RECOVERY_EXECUTABLE",
        reason="RecoveryPlan 满足当前 Native Recovery Executor 的全部执行约束。",
    )


def _unavailable(reason_code: str, reason: str) -> NativeRecoveryCapability:
    """构造统一格式的 Native 不可执行结果。"""

    return NativeRecoveryCapability(
        executable=False,
        reason_code=reason_code,
        reason=reason,
    )


__all__ = ["NativeRecoveryCapability", "assess_native_recovery_capability"]
