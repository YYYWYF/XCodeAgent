"""生产环境 Durable Recovery replay policy 的唯一注册表。"""

from __future__ import annotations

from app.services.application_planning_recovery_policy import (
    ApplicationPlanningCommittedInputReplayPolicy,
)
from app.services.execution_recovery_strategy import RecoveryReplayPolicy


def production_recovery_replay_policies() -> tuple[RecoveryReplayPolicy, ...]:
    """返回 Projection、Planning GET 与执行端点共用的生产策略。"""

    return (ApplicationPlanningCommittedInputReplayPolicy(),)


__all__ = ["production_recovery_replay_policies"]
