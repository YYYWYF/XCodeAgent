"""Native Recovery Executor lifecycle invariant 的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryExecutionError,
    RecoveryPlan,
    RecoveryStrategy,
)
from app.services.execution_recovery_executor import _handoff_lifecycle


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
            decision="ready_native",
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            checkpoint_id="checkpoint-1",
            checkpoint_ns="",
            next_nodes=["technical_planning_begin"],
            reason_code="TEST",
            reason="test",
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


__all__ = ["ExecutionRecoveryExecutorTests"]
