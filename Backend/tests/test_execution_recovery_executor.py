"""Native Recovery Executor lifecycle invariant 的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryExecutionError,
    RecoveryLifecycleOwnershipMode,
    RecoveryPlan,
)
from app.services.execution_recovery_executor import (
    _apply_application_planning_transaction_authority,
    _handoff_lifecycle,
    _validate_finalization_lifecycle,
)


class DesignIntentRecoveryOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """验证设计分类失败只在原待确认窗口中领取恢复 ownership。"""

    async def test_design_intent_recovery_requires_same_thread_revision_and_run(self) -> None:
        """同身份待确认窗口可以恢复，线程、版本、源 Run 或运行状态漂移不能授权。"""

        async def read_state(_config):
            """提供已验证源 Run 的分类节点入口快照。"""
            return SimpleNamespace(values={"active_run_id": "source-run"})

        source = SimpleNamespace(execution_kind="application_planning", thread_id="thread", run_id="source-run")
        plan = RecoveryPlan(source_run_id="source-run", thread_id="thread", target_node="design_intent_analysis", checkpoint_id="entry", lifecycle_revision=7)
        for thread_id, revision, status, run_id, allowed in (
            ("thread", 7, ApplicationLifecycleStatus.AWAITING_USER, "source-run", True),
            ("other", 7, ApplicationLifecycleStatus.AWAITING_USER, "source-run", False),
            ("thread", 8, ApplicationLifecycleStatus.AWAITING_USER, "source-run", False),
            ("thread", 7, ApplicationLifecycleStatus.RUNNING, "source-run", False),
            ("thread", 7, ApplicationLifecycleStatus.AWAITING_USER, "other-run", False),
        ):
            with self.subTest(thread=thread_id, revision=revision, status=status, run=run_id):
                source.run_id = run_id
                lifecycle = SimpleNamespace(revision=revision, initialization=SimpleNamespace(thread_id=thread_id, status=status, stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION))
                with patch("app.services.execution_recovery_executor.load_application_lifecycle", return_value=lifecycle):
                    result = await _apply_application_planning_transaction_authority(workspace="/tmp/design-recovery", source=source, graph=SimpleNamespace(aget_state=read_state), plan=plan)
                self.assertEqual(result.lifecycle_ownership_mode is RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP, allowed)


class ExecutionRecoveryExecutorTests(unittest.TestCase):
    """验证 Executor 对 Application Planning lifecycle revision 的最终守卫。"""

    def test_application_planning_missing_lifecycle_revision_is_explicit_error(self) -> None:
        """缺失 lifecycle revision 必须返回稳定领域错误，而不能降级为断言或 prestart failure。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="planning-thread",
            workspace="/tmp/native-recovery-executor",
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            target_node="technical_planning_begin",
            checkpoint_id="checkpoint-1",
            checkpoint_ns="",
        )

        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(RecoveryExecutionError) as raised:
                _handoff_lifecycle(
                    Path(workspace),
                    source=source,
                    plan=plan,
                    new_run_id="recovery-child",
                )

        self.assertEqual(raised.exception.code, "RECOVERY_LIFECYCLE_REVISION_MISSING")

    def test_application_planning_finalization_requires_lifecycle_ownership(self) -> None:
        """Application Planning finalization 缺少 lifecycle 时必须拒绝 child fork。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="planning-thread",
            workspace="/tmp/native-recovery-executor",
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        attempt = SimpleNamespace(
            new_run_id="recovery-child",
            thread_id="planning-thread",
            lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )

        with self.assertRaises(RecoveryExecutionError) as raised:
            _validate_finalization_lifecycle(
                source=source,
                attempt=attempt,
                lifecycle=None,
            )

        self.assertEqual(raised.exception.code, "RECOVERY_STATE_DRIFT")

    def test_application_planning_finalization_rejects_child_identity_drift(self) -> None:
        """finalization 必须同时拒绝 child activeRunId 或 threadId 漂移。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="planning-thread",
            workspace="/tmp/native-recovery-executor",
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        attempt = SimpleNamespace(
            new_run_id="recovery-child",
            thread_id="planning-thread",
            lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )
        for lifecycle in (
            SimpleNamespace(
                active_run_id="other-child",
                initialization=SimpleNamespace(thread_id="planning-thread"),
            ),
            SimpleNamespace(
                active_run_id="recovery-child",
                initialization=SimpleNamespace(thread_id="other-thread"),
            ),
        ):
            with self.subTest(lifecycle=lifecycle):
                with self.assertRaises(RecoveryExecutionError) as raised:
                    _validate_finalization_lifecycle(
                        source=source,
                        attempt=attempt,
                        lifecycle=lifecycle,
                    )
                self.assertEqual(raised.exception.code, "RECOVERY_STATE_DRIFT")

    def test_application_planning_pre_ownership_finalization_accepts_child(self) -> None:
        """PRE_OWNERSHIP child 在 lifecycle identity 正确时继续允许 finalization。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="planning-thread",
            workspace="/tmp/native-recovery-executor",
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        attempt = SimpleNamespace(
            new_run_id="recovery-child",
            thread_id="planning-thread",
            lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )

        _validate_finalization_lifecycle(
            source=source,
            attempt=attempt,
            lifecycle=SimpleNamespace(
                active_run_id="recovery-child",
                initialization=SimpleNamespace(thread_id="planning-thread"),
            ),
        )


__all__ = ["ExecutionRecoveryExecutorTests"]
