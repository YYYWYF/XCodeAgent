"""P0.4B request authority 与 Native 可执行性合同测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPlan,
    RecoveryStrategy,
)
from app.services.application_planning_stage_recovery import (
    ApplicationPlanningStageRecoveryContract,
    _request_from_formal_artifacts,
)
from app.services.execution_recovery_action_planner import plan_recovery_action
from app.services.execution_recovery_capability import (
    assess_native_recovery_capability,
)


class P04BRecoveryContractTests(unittest.IsolatedAsyncioTestCase):
    """覆盖首次 Technical Planning request 与 Native fallback 的关键边界。"""

    def test_source_request_is_the_first_initial_request_authority(self) -> None:
        """RequirementSpec.source_request 必须优先于旧字段和 ProductPlan。"""

        request = _request_from_formal_artifacts(
            {
                "source_request": "创建一个天气预报应用",
                "request": "旧的 RequirementSpec 请求",
            },
            {"request": "旧的 ProductPlan 请求"},
        )

        self.assertEqual(request, "创建一个天气预报应用")

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
                    "strategy": RecoveryStrategy.STAGE_RESTART,
                }
            )
        )

        self.assertFalse(capability.executable)
        self.assertEqual(capability.reason_code, "NATIVE_DECISION_REQUIRED")

    async def test_native_non_root_plan_falls_back_to_stage_restart(self) -> None:
        """Native 不可执行且正式阶段有效时，Action Planner 必须返回 Stage Restart。"""

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
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        assessment = SimpleNamespace(
            available=True,
            reason_code="TECHNICAL_PLANNING_STAGE_RESTART_AVAILABLE",
            reason="stage restart is available",
            authority=SimpleNamespace(authority_sha256="a" * 64),
        )
        with patch(
            "app.services.execution_recovery_action_planner.ApplicationPlanningStageRecoveryContract.assess",
            return_value=assessment,
        ):
            action_plan, _ = await plan_recovery_action(
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

        self.assertIsNotNone(action_plan.primary_action)
        assert action_plan.primary_action is not None
        self.assertEqual(action_plan.primary_action.kind.value, "restart_stage")

    def test_initial_stage_restart_missing_request_is_unavailable(self) -> None:
        """首次 Technical Planning 缺少原始需求时必须在 Contract 层拒绝。"""

        source = self._source()
        lifecycle = SimpleNamespace(
            active_run_id=source.run_id,
            revision=1,
            initialization=SimpleNamespace(
                thread_id=source.thread_id,
                stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                status=ApplicationLifecycleStatus.RUNNING,
            ),
            active_formal_revision=None,
            pending_revision_impact=None,
            application=SimpleNamespace(name="测试应用"),
        )
        with patch(
            "app.services.application_planning_stage_recovery._load_formal_artifacts",
            return_value=(
                {"confirmation_status": "confirmed"},
                {"confirmation_status": "confirmed"},
                {"confirmation_status": "confirmed"},
                {},
            ),
        ):
            assessment = ApplicationPlanningStageRecoveryContract().assess(
                workspace=source.workspace,
                source=source,
                point=None,
                snapshot=SimpleNamespace(values={}),
                lifecycle=lifecycle,
            )

        self.assertFalse(assessment.available)
        self.assertEqual(assessment.reason_code, "STAGE_RESTART_REQUEST_MISSING")

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

    @staticmethod
    def _source() -> DurableExecutionRecord:
        """构造首次 Technical Planning 的失败 source execution。"""

        now = datetime.now(timezone.utc)
        return DurableExecutionRecord(
            run_id="source-run",
            thread_id="planning-thread",
            workspace="/tmp/p0-4b-recovery",
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_generate",
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )


__all__ = ["P04BRecoveryContractTests"]
