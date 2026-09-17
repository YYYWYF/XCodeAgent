"""Checkpoint active_run_id 锚定 Recovery lineage 的定向回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
)
from app.protocols.execution_recovery import _resolve_current_recovery_source
from app.services.execution_recovery_lineage import (
    RecoveryLineageState,
    resolve_recovery_lineage_head,
)


class CheckpointAnchoredRecoveryLineageTests(unittest.IsolatedAsyncioTestCase):
    """锁定 normal resume 与 RecoveryAttempt 各自的 authority 边界。"""

    def setUp(self) -> None:
        """创建稳定 workspace identity 与 execution 时间。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)
        self.thread_id = "checkpoint-lineage-thread"
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        """释放隔离工作区。"""

        self._temporary_workspace.cleanup()

    def _execution(
        self,
        run_id: str,
        status: DurableExecutionStatus,
    ) -> DurableExecutionRecord:
        """构造同一 Application Planning thread 的 durable execution。"""

        return DurableExecutionRecord(
            run_id=run_id,
            thread_id=self.thread_id,
            workspace=str(self.workspace),
            project_id="checkpoint-lineage-app",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="requirements",
            current_node="requirements",
            status=status,
            started_at=self.now,
            updated_at=self.now,
            ended_at=(
                self.now
                if status is not DurableExecutionStatus.RUNNING
                else None
            ),
        )

    def _attempt(
        self,
        source_run_id: str,
        new_run_id: str,
        status: RecoveryAttemptStatus,
    ) -> RecoveryAttempt:
        """构造仅用于 lineage 解析的 root RecoveryAttempt edge。"""

        return RecoveryAttempt(
            source_run_id=source_run_id,
            new_run_id=new_run_id,
            thread_id=self.thread_id,
            source_checkpoint_id="checkpoint-source",
            status=status,
            created_at=self.now,
            source_status=DurableExecutionStatus.FAILED,
        )

    async def _resolve(
        self,
        *,
        executions: list[DurableExecutionRecord],
        attempts: list[RecoveryAttempt],
        authoritative_run_id: str | None,
        max_hops: int = 32,
    ):
        """替换持久化读取并调用 production anchored resolver。"""

        with (
            patch(
                "app.services.execution_recovery_lineage.list_executions_for_thread",
                new=AsyncMock(return_value=executions),
            ),
            patch(
                "app.services.execution_recovery_lineage.list_recovery_attempts_for_thread",
                new=AsyncMock(return_value=attempts),
            ),
        ):
            return await resolve_recovery_lineage_head(
                str(self.workspace),
                thread_id=self.thread_id,
                execution_kind="application_planning",
                authoritative_run_id=authoritative_run_id,
                max_hops=max_hops,
            )

    async def test_normal_resume_history_is_excluded_from_checkpoint_component(self) -> None:
        """A/B 等旧 AWAITING_USER normal runs 不得让当前 C FAILED 变成 ambiguous。"""

        run_a = self._execution("run-a", DurableExecutionStatus.AWAITING_USER)
        run_b = self._execution("run-b", DurableExecutionStatus.AWAITING_USER)
        run_c = self._execution("run-c", DurableExecutionStatus.FAILED)

        resolution = await self._resolve(
            executions=[run_a, run_b, run_c],
            attempts=[],
            authoritative_run_id=run_c.run_id,
        )
        db_only_resolution = await self._resolve(
            executions=[run_a, run_b, run_c],
            attempts=[],
            authoritative_run_id=None,
        )

        self.assertEqual(resolution.state, RecoveryLineageState.RECOVERABLE_HEAD)
        self.assertIsNotNone(resolution.head)
        assert resolution.head is not None
        self.assertEqual(resolution.head.run_id, run_c.run_id)
        self.assertEqual(db_only_resolution.state, RecoveryLineageState.AMBIGUOUS)

    async def test_anchor_follows_only_recovery_edges_and_preserves_in_flight(self) -> None:
        """checkpoint 仍指向 B 时，B→C PREPARING 必须投影为唯一 in-flight child。"""

        old_run = self._execution("run-a", DurableExecutionStatus.AWAITING_USER)
        source = self._execution("run-b", DurableExecutionStatus.FAILED)
        child = self._execution("run-c", DurableExecutionStatus.RUNNING)
        failed_prestart_child = self._execution(
            "run-prestart-failed",
            DurableExecutionStatus.FAILED,
        )

        resolution = await self._resolve(
            executions=[old_run, source, child, failed_prestart_child],
            attempts=[
                self._attempt(
                    source.run_id,
                    failed_prestart_child.run_id,
                    RecoveryAttemptStatus.FAILED_PRESTART,
                ),
                self._attempt(
                    source.run_id,
                    child.run_id,
                    RecoveryAttemptStatus.PREPARING,
                )
            ],
            authoritative_run_id=source.run_id,
        )

        self.assertEqual(resolution.state, RecoveryLineageState.RECOVERY_IN_FLIGHT)
        self.assertIsNotNone(resolution.head)
        assert resolution.head is not None
        self.assertEqual(resolution.head.run_id, child.run_id)

    async def test_started_child_can_be_the_checkpoint_anchor(self) -> None:
        """fork 已提交 active_run_id=C 后必须从 C 解析，不能退回 source B。"""

        source = self._execution("run-b", DurableExecutionStatus.FAILED)
        child = self._execution("run-c", DurableExecutionStatus.RUNNING)

        resolution = await self._resolve(
            executions=[source, child],
            attempts=[
                self._attempt(
                    source.run_id,
                    child.run_id,
                    RecoveryAttemptStatus.STARTED,
                )
            ],
            authoritative_run_id=child.run_id,
        )

        self.assertEqual(resolution.state, RecoveryLineageState.RUNNING_HEAD)
        self.assertIsNotNone(resolution.head)
        assert resolution.head is not None
        self.assertEqual(resolution.head.run_id, child.run_id)

    async def test_missing_checkpoint_execution_fails_closed_without_db_fallback(self) -> None:
        """checkpoint run 缺少 durable record 时不得选择任一旧 execution。"""

        old_run = self._execution("run-a", DurableExecutionStatus.AWAITING_USER)
        failed_run = self._execution("run-b", DurableExecutionStatus.FAILED)

        resolution = await self._resolve(
            executions=[old_run, failed_run],
            attempts=[],
            authoritative_run_id="missing-run",
        )

        self.assertEqual(resolution.state, RecoveryLineageState.AMBIGUOUS)
        self.assertIsNone(resolution.head)
        self.assertEqual(
            resolution.reason_code,
            "RECOVERY_CHECKPOINT_RUN_INVALID",
        )

    async def test_branching_and_cycle_corruption_remain_ambiguous(self) -> None:
        """锚定正常 resume 不能放宽 RecoveryAttempt 分叉或成环 corruption。"""

        source = self._execution("run-b", DurableExecutionStatus.FAILED)
        child_c = self._execution("run-c", DurableExecutionStatus.RUNNING)
        child_d = self._execution("run-d", DurableExecutionStatus.RUNNING)
        branching = await self._resolve(
            executions=[source, child_c, child_d],
            attempts=[
                self._attempt(
                    source.run_id,
                    child_c.run_id,
                    RecoveryAttemptStatus.STARTED,
                ),
                self._attempt(
                    source.run_id,
                    child_d.run_id,
                    RecoveryAttemptStatus.STARTED,
                ),
            ],
            authoritative_run_id=source.run_id,
        )
        cycle = await self._resolve(
            executions=[source, child_c],
            attempts=[
                self._attempt(
                    source.run_id,
                    child_c.run_id,
                    RecoveryAttemptStatus.STARTED,
                ),
                self._attempt(
                    child_c.run_id,
                    source.run_id,
                    RecoveryAttemptStatus.STARTED,
                ),
            ],
            authoritative_run_id=source.run_id,
        )

        self.assertEqual(branching.state, RecoveryLineageState.AMBIGUOUS)
        self.assertEqual(branching.reason_code, "RECOVERY_LINEAGE_AMBIGUOUS")
        self.assertEqual(cycle.state, RecoveryLineageState.AMBIGUOUS)
        self.assertEqual(cycle.reason_code, "RECOVERY_LINEAGE_AMBIGUOUS")

    async def test_anchor_chain_respects_recovery_hop_limit(self) -> None:
        """超过 max_hops 的 RecoveryAttempt chain 必须继续 fail closed。"""

        source = self._execution("run-b", DurableExecutionStatus.FAILED)
        child = self._execution("run-c", DurableExecutionStatus.FAILED)

        resolution = await self._resolve(
            executions=[source, child],
            attempts=[
                self._attempt(
                    source.run_id,
                    child.run_id,
                    RecoveryAttemptStatus.STARTED,
                )
            ],
            authoritative_run_id=source.run_id,
            max_hops=0,
        )

        self.assertEqual(resolution.state, RecoveryLineageState.AMBIGUOUS)
        self.assertEqual(resolution.reason_code, "RECOVERY_LINEAGE_AMBIGUOUS")

    async def test_execute_guard_rejects_source_superseded_by_checkpoint_child(self) -> None:
        """执行入口必须用 Graph active_run_id 拒绝旧 action，而非用 source 自证。"""

        source = self._execution("run-b", DurableExecutionStatus.FAILED)
        child = self._execution("run-c", DurableExecutionStatus.RUNNING)
        graph = SimpleNamespace(
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={"active_run_id": child.run_id},
                )
            )
        )
        with (
            patch(
                "app.services.execution_recovery_lineage.list_executions_for_thread",
                new=AsyncMock(return_value=[source, child]),
            ),
            patch(
                "app.services.execution_recovery_lineage.list_recovery_attempts_for_thread",
                new=AsyncMock(
                    return_value=[
                        self._attempt(
                            source.run_id,
                            child.run_id,
                            RecoveryAttemptStatus.STARTED,
                        )
                    ]
                ),
            ),
        ):
            with self.assertRaises(RecoveryExecutionError) as raised:
                await _resolve_current_recovery_source(
                    workspace=str(self.workspace),
                    requested=source,
                    graph=graph,
                )

        self.assertEqual(raised.exception.code, "RECOVERY_SOURCE_SUPERSEDED")
        self.assertEqual(
            raised.exception.details,
            {"currentSourceRunId": child.run_id},
        )
        graph.aget_state.assert_awaited_once_with(
            {
                "configurable": {
                    "thread_id": source.thread_id,
                    "checkpoint_ns": "",
                }
            }
        )


if __name__ == "__main__":
    unittest.main()
