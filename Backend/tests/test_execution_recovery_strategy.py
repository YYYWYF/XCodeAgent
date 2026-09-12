from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from app.domain.execution_recovery import RecoveryDecision, RecoveryStrategy
from app.services.execution_recovery_strategy import (
    RecoveryStrategyAssessment,
    RecoveryStrategyResolver,
)


class _FixedPolicy:
    """返回固定策略评估并记录是否被调用的测试策略。"""

    def __init__(self, assessment: RecoveryStrategyAssessment | None) -> None:
        """保存测试策略的评估结果。"""

        self.assessment = assessment
        self.calls = 0

    def assess(
        self,
        *,
        source: Any,
        point: Any,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment | None:
        """返回预设结果，用于验证 resolver 的 first-match 顺序。"""

        del source, point, snapshot
        self.calls += 1
        return self.assessment


class RecoveryStrategyResolverTests(unittest.TestCase):
    """覆盖 P0.3A 恢复策略的 None、first-match 和默认拒绝语义。"""

    def setUp(self) -> None:
        """为每个测试准备最小策略上下文。"""

        self.context = SimpleNamespace(
            source=object(),
            point=object(),
            snapshot=object(),
        )

    def test_unhandled_policy_delegates_to_later_policy(self) -> None:
        """前一个 policy 返回 None 时，后一个自定义判断必须生效。"""

        first = _FixedPolicy(None)
        second_assessment = RecoveryStrategyAssessment(
            decision=RecoveryDecision.REQUIRES_HANDLER,
            strategy=RecoveryStrategy.HANDLER,
            reason_code="BUILD_RECOVERY_REQUIRED",
            reason="Build 必须通过专用 Handler 恢复。",
        )
        second = _FixedPolicy(second_assessment)

        result = RecoveryStrategyResolver([first, second]).resolve(self.context)

        self.assertIs(result, second_assessment)
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_first_non_none_assessment_wins(self) -> None:
        """首个 policy 命中后，后续 policy 不得再执行或覆盖结果。"""

        first_assessment = RecoveryStrategyAssessment(
            decision=RecoveryDecision.READY_NATIVE,
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            reason_code="READY_FOR_NATIVE_REPLAY",
            reason="节点已被显式证明可安全重放。",
        )
        first = _FixedPolicy(first_assessment)
        second = _FixedPolicy(
            RecoveryStrategyAssessment(
                decision=RecoveryDecision.REQUIRES_HANDLER,
                strategy=RecoveryStrategy.HANDLER,
                reason_code="SHOULD_NOT_RUN",
                reason="不应执行。",
            )
        )

        result = RecoveryStrategyResolver([first, second]).resolve(self.context)

        self.assertIs(result, first_assessment)
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)

    def test_all_policies_unhandled_use_default_deny(self) -> None:
        """所有 policy 都返回 None 时仍必须保持默认安全拒绝。"""

        first = _FixedPolicy(None)
        second = _FixedPolicy(None)

        result = RecoveryStrategyResolver([first, second]).resolve(self.context)

        self.assertEqual(result.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(result.strategy, RecoveryStrategy.HANDLER)
        self.assertEqual(result.reason_code, "REPLAY_SAFETY_UNASSESSED")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)
