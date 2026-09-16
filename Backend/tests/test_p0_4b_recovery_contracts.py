"""P0.4B request authority 与 Native 可执行性合同测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryIncidentStatus,
    RecoveryPlan,
    RecoveryStrategy,
)
from app.services.execution_recovery_action_planner import plan_recovery_action
from app.services.execution_recovery_capability import (
    assess_native_recovery_capability,
)


class P04BRecoveryContractTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 P0.5-D 删除 Stage Restart 后的 Native fail-closed 边界。"""

    def test_native_capability_accepts_only_root_single_successor_plan(self) -> None:
        """Native capability 必须与 Executor 的 root/single-successor 硬约束一致。"""

        capability = assess_native_recovery_capability(self._native_plan())

        self.assertTrue(capability.executable)

    def test_native_capability_rejects_non_root_and_parallel_plans(self) -> None:
        """非 root namespace 和并行 successor 都必须 fail closed。"""

        non_root = assess_native_recovery_capability(
            self._native_plan().model_copy(update={"checkpoint_ns": "recovery-stage-C"})
        )
        parallel = assess_native_recovery_capability(
            self._native_plan().model_copy(update={"next_nodes": ["a", "b"]})
        )

        self.assertFalse(non_root.executable)
        self.assertEqual(non_root.reason_code, "NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED")
        self.assertFalse(parallel.executable)
        self.assertEqual(parallel.reason_code, "NATIVE_PARALLEL_REPLAY_UNSUPPORTED")

    def test_non_native_decision_is_not_executable(self) -> None:
        """非 READY_NATIVE 的计划不能被 capability 误判为可执行。"""

        capability = assess_native_recovery_capability(
            self._native_plan().model_copy(
                update={
                    "decision": RecoveryDecision.REQUIRES_HANDLER,
                    "strategy": RecoveryStrategy.HANDLER,
                }
            )
        )

        self.assertFalse(capability.executable)
        self.assertEqual(capability.reason_code, "NATIVE_DECISION_REQUIRED")

    async def test_native_non_root_plan_fails_closed_without_stage_restart(self) -> None:
        """Native 不可执行时，Action Planner 必须直接 fail closed。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="planning-thread",
            workspace="/tmp/p0-4b-recovery",
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_generate",
            status=DurableExecutionStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        action_plan = await plan_recovery_action(
            workspace=source.workspace,
            source=source,
            recovery_plan=self._native_plan().model_copy(
                update={
                    "source_run_id": source.run_id,
                    "thread_id": source.thread_id,
                    "checkpoint_ns": "recovery-stage-C",
                }
            ),
            point=None,
            snapshot=SimpleNamespace(values={}),
            lifecycle=SimpleNamespace(revision=1),
        )

        self.assertEqual(action_plan.status, RecoveryIncidentStatus.NEEDS_ATTENTION)
        self.assertIsNone(action_plan.primary_action)
        self.assertEqual(action_plan.reason_code, "NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED")

    @staticmethod
    def _native_plan() -> RecoveryPlan:
        """构造满足当前 Native capability 的最小 RecoveryPlan。"""

        return RecoveryPlan(
            source_run_id="source-run",
            thread_id="planning-thread",
            decision=RecoveryDecision.READY_NATIVE,
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            recovery_point_id="recovery-point",
            checkpoint_id="checkpoint",
            checkpoint_ns="",
            next_nodes=["technical_planning_generate"],
            reason_code="READY_FOR_NATIVE_REPLAY",
            reason="test",
        )


__all__ = ["P04BRecoveryContractTests"]
