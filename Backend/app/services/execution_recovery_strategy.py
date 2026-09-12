"""P0.3A 恢复策略解析合同与默认安全策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    RecoveryDecision,
    RecoveryPoint,
    RecoveryStrategy,
)


@dataclass(frozen=True)
class RecoveryContext:
    """为策略评估传递最小恢复上下文，不携带完整 Graph State。"""

    source: DurableExecutionRecord
    point: RecoveryPoint
    snapshot: Any
    lifecycle_revision: int | None
    workspace_revision: str | None
    workspace_snapshot_hash: str | None


@dataclass(frozen=True)
class RecoveryStrategyAssessment:
    """保存策略层对 checkpoint 重放安全性的判断。"""

    decision: RecoveryDecision
    strategy: RecoveryStrategy
    reason_code: str
    reason: str


class RecoveryReplayPolicy(Protocol):
    """定义未来 Native Replay 或领域 Handler 的可插拔评估接口。"""

    def assess(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment | None:
        """根据执行、现场和真实快照评估是否由本策略负责恢复。"""


class DenyUnassessedReplayPolicy:
    """生产默认策略，未明确证明安全时一律要求专用 Handler。"""

    def assess(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment:
        """拒绝所有尚未声明 replay-safe 的 checkpoint。"""

        del source, point, snapshot
        return RecoveryStrategyAssessment(
            decision=RecoveryDecision.REQUIRES_HANDLER,
            strategy=RecoveryStrategy.HANDLER,
            reason_code="REPLAY_SAFETY_UNASSESSED",
            reason="checkpoint 身份正确，但 replay safety 尚未被专用策略证明。",
        )


class AllowNodePolicy:
    """测试或未来受控场景使用的节点白名单策略。"""

    def __init__(self, safe_nodes: set[str] | frozenset[str]) -> None:
        """保存允许 Native Replay 的后继节点集合。"""

        self._safe_nodes = frozenset(str(node) for node in safe_nodes)

    def assess(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment | None:
        """仅当所有后继节点都在显式白名单中时允许 Native Replay。"""

        if point.next_nodes and set(point.next_nodes).issubset(self._safe_nodes):
            return RecoveryStrategyAssessment(
                decision=RecoveryDecision.READY_NATIVE,
                strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
                reason_code="READY_FOR_NATIVE_REPLAY",
                reason="checkpoint 已通过注入的 Native Replay 节点白名单。",
            )
        del source, snapshot
        return None


class RecoveryStrategyResolver:
    """按注册顺序解析策略，首个命中者胜出并保留 deny-by-default。"""

    def __init__(
        self,
        policies: Sequence[RecoveryReplayPolicy] | None = None,
    ) -> None:
        """保存可注入的策略集合；默认拒绝只作为最终 fallback。"""

        self._policies = tuple(policies or ())

    def resolve(self, context: RecoveryContext) -> RecoveryStrategyAssessment:
        """按注册优先级返回首个非 None 评估，未命中时默认拒绝。"""

        for policy in self._policies:
            assessment = policy.assess(
                source=context.source,
                point=context.point,
                snapshot=context.snapshot,
            )
            if assessment is not None:
                return assessment
        return DenyUnassessedReplayPolicy().assess(
            source=context.source,
            point=context.point,
            snapshot=context.snapshot,
        )


def resolve_recovery_strategy(
    context: RecoveryContext,
    *,
    policies: Sequence[RecoveryReplayPolicy] | None = None,
) -> RecoveryStrategyAssessment:
    """提供无状态策略解析入口，方便 Coordinator 和测试注入策略。"""

    return RecoveryStrategyResolver(policies).resolve(context)
