"""生产环境 Durable Recovery replay policy 的唯一注册表。"""

from __future__ import annotations

from typing import Any, Sequence

from app.domain.application_planning_recovery import parse_application_planning_boundary
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPoint,
    RecoveryStrategy,
)
from app.services.application_planning_recovery_contracts import (
    ApplicationPlanningRecoveryContract,
    production_application_planning_recovery_contracts,
)
from app.services.execution_recovery_strategy import (
    RecoveryReplayPolicy,
    RecoveryStrategyAssessment,
)


class ApplicationPlanningContractReplayPolicy:
    """把所有 Application Planning Contract 适配为一个生产 replay policy。"""

    def __init__(
        self,
        contracts: Sequence[ApplicationPlanningRecoveryContract] | None = None,
    ) -> None:
        """保存当前生产 Contract 快照，避免协调器复制业务分支。"""

        self._contracts = tuple(
            contracts or production_application_planning_recovery_contracts()
        )

    def assess(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment | None:
        """按注册顺序交给首个匹配 Contract 评估，未命中则交回默认拒绝。"""

        for contract in self._contracts:
            if contract.match(source=source, point=point, snapshot=snapshot):
                return contract.assess_replay(
                    source=source,
                    point=point,
                    snapshot=snapshot,
                )
        # 升级前的 TechnicalPlan checkpoint 没有 boundary，不能根据旧节点名猜测
        # successor；显式返回稳定 blocked 原因，要求用户重新发起当前规划事务。
        values = getattr(snapshot, "values", {})
        values = values if isinstance(values, dict) else {}
        if (
            source.execution_kind == "application_planning"
            and source.status is DurableExecutionStatus.INTERRUPTED
            and any(str(node).startswith("technical_planning") for node in point.next_nodes)
            and parse_application_planning_boundary(
                values.get("application_planning_recovery_boundary")
            )
            is None
        ):
            return RecoveryStrategyAssessment(
                decision=RecoveryDecision.REQUIRES_HANDLER,
                strategy=RecoveryStrategy.HANDLER,
                reason_code="PLANNING_RECOVERY_BOUNDARY_MISSING",
                reason="历史 TechnicalPlan checkpoint 缺少 Recovery Contract boundary，拒绝猜测恢复位置。",
            )
        return None


def production_recovery_replay_policies() -> tuple[RecoveryReplayPolicy, ...]:
    """返回 Projection、Planning GET 与执行端点共用的生产策略。"""

    return (ApplicationPlanningContractReplayPolicy(),)


__all__ = [
    "ApplicationPlanningContractReplayPolicy",
    "production_recovery_replay_policies",
]
