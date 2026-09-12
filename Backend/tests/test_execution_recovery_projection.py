from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPlan,
    RecoveryStrategy,
)
from app.services.execution_recovery_projection import (
    resolve_execution_recovery_projection,
)


class ExecutionRecoveryProjectionTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 lifecycle GET 的 canonical interrupted execution 安全投影。"""

    def setUp(self) -> None:
        """为投影测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放投影测试的临时工作区。"""

        self._temporary_workspace.cleanup()

    def _record(self, run_id: str, thread_id: str = "thread-A") -> DurableExecutionRecord:
        """构造一条只包含公开投影来源字段的中断 execution。"""

        now = datetime.now(timezone.utc)
        return DurableExecutionRecord(
            run_id=run_id,
            thread_id=thread_id,
            owner_session_id="session-A",
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench",
            workflow_scope=None,
            first_node="build",
            current_node="build",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )

    def _plan(self, run_id: str, decision: RecoveryDecision) -> RecoveryPlan:
        """构造 P0.3A 返回的只读判断结果。"""

        return RecoveryPlan(
            source_run_id=run_id,
            thread_id="thread-A",
            decision=decision,
            strategy=(
                RecoveryStrategy.NATIVE_CHECKPOINT
                if decision is RecoveryDecision.READY_NATIVE
                else RecoveryStrategy.NONE
            ),
            reason_code=decision.value.upper(),
            reason="projection test",
        )

    async def test_empty_projection_when_no_interrupted_execution_exists(self) -> None:
        """没有中断 execution 时返回空 candidates。"""

        with patch(
            "app.services.execution_recovery_projection.list_recovery_projection_candidates",
            new=AsyncMock(return_value=[]),
        ):
            projection = await resolve_execution_recovery_projection(str(self.workspace))

        self.assertEqual(projection.candidates, [])

    async def test_ready_native_only_exposes_public_fields(self) -> None:
        """READY_NATIVE 只能投影可继续状态，不泄漏内部 RecoveryPlan authority。"""

        record = self._record("run-A")
        with (
            patch(
                "app.services.execution_recovery_projection.list_recovery_projection_candidates",
                new=AsyncMock(return_value=[record]),
            ),
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.execution_recovery_projection.prepare_continue",
                new=AsyncMock(return_value=self._plan(record.run_id, RecoveryDecision.READY_NATIVE)),
            ),
        ):
            projection = await resolve_execution_recovery_projection(str(self.workspace))

        self.assertEqual(len(projection.candidates), 1)
        candidate = projection.candidates[0].model_dump(mode="json", by_alias=True)
        self.assertTrue(candidate["canContinue"])
        self.assertEqual(candidate["availability"], "ready")
        self.assertEqual(candidate["ownerSessionId"], "session-A")
        for forbidden in (
            "checkpointId",
            "checkpointNs",
            "workspaceRevision",
            "workspaceSnapshotHash",
            "recoveryPointId",
            "strategy",
        ):
            self.assertNotIn(forbidden, candidate)

    async def test_non_ready_decisions_are_mapped_without_exposing_reason_text(self) -> None:
        """handler、drift 与 user input 状态映射为普通用户可理解的 UI 状态。"""

        decisions = (
            (RecoveryDecision.REQUIRES_HANDLER, "requires_handler", False),
            (RecoveryDecision.STATE_DRIFT, "blocked", False),
            (RecoveryDecision.INVALID_RECOVERY_POINT, "blocked", False),
            (RecoveryDecision.AWAITING_USER, "awaiting_user", False),
        )
        for decision, availability, can_continue in decisions:
            record = self._record(f"run-{decision.value}")
            with self.subTest(decision=decision):
                with (
                    patch(
                        "app.services.execution_recovery_projection.list_recovery_projection_candidates",
                        new=AsyncMock(return_value=[record]),
                    ),
                    patch(
                        "app.services.execution_recovery_projection.workflow_graph_for_request",
                        new=AsyncMock(return_value=object()),
                    ),
                    patch(
                        "app.services.execution_recovery_projection.prepare_continue",
                        new=AsyncMock(return_value=self._plan(record.run_id, decision)),
                    ),
                ):
                    projection = await resolve_execution_recovery_projection(str(self.workspace))
            self.assertEqual(projection.candidates[0].availability, availability)
            self.assertEqual(projection.candidates[0].can_continue, can_continue)

    async def test_legacy_record_uses_exact_checkpoint_owner_without_mutating_record(self) -> None:
        """旧记录只允许从 P0.3A 精确 checkpoint 读取 ownership。"""

        record = self._record("run-legacy").model_copy(update={"owner_session_id": None})
        snapshot = type("Snapshot", (), {"values": {"owner_session_id": "session-legacy"}})()
        plan = self._plan(record.run_id, RecoveryDecision.READY_NATIVE).model_copy(
            update={"checkpoint_id": "checkpoint-exact", "checkpoint_ns": "namespace"}
        )
        graph = type("Graph", (), {"aget_state": AsyncMock(return_value=snapshot)})()
        with (
            patch(
                "app.services.execution_recovery_projection.list_recovery_projection_candidates",
                new=AsyncMock(return_value=[record]),
            ),
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=graph),
            ),
            patch(
                "app.services.execution_recovery_projection.prepare_continue",
                new=AsyncMock(return_value=plan),
            ),
        ):
            projection = await resolve_execution_recovery_projection(str(self.workspace))

        self.assertEqual(projection.candidates[0].owner_session_id, "session-legacy")
        graph.aget_state.assert_awaited_once_with(
            {
                "configurable": {
                    "thread_id": record.thread_id,
                    "checkpoint_ns": "namespace",
                    "checkpoint_id": "checkpoint-exact",
                }
            }
        )
        self.assertIsNone(record.owner_session_id)

    async def test_legacy_record_without_checkpoint_owner_is_not_projected(self) -> None:
        """旧记录无法从精确 checkpoint 证明 ownership 时必须 fail closed。"""

        record = self._record("run-unowned").model_copy(update={"owner_session_id": None})
        snapshot = type("Snapshot", (), {"values": {}})()
        plan = self._plan(record.run_id, RecoveryDecision.READY_NATIVE).model_copy(
            update={"checkpoint_id": "checkpoint-exact"}
        )
        graph = type("Graph", (), {"aget_state": AsyncMock(return_value=snapshot)})()
        with (
            patch(
                "app.services.execution_recovery_projection.list_recovery_projection_candidates",
                new=AsyncMock(return_value=[record]),
            ),
            patch(
                "app.services.execution_recovery_projection.workflow_graph_for_request",
                new=AsyncMock(return_value=graph),
            ),
            patch(
                "app.services.execution_recovery_projection.prepare_continue",
                new=AsyncMock(return_value=plan),
            ),
        ):
            projection = await resolve_execution_recovery_projection(str(self.workspace))

        self.assertEqual(projection.candidates, [])


if __name__ == "__main__":
    unittest.main()
