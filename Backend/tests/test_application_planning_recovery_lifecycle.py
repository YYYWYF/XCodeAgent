"""Application Planning committed-input 生命周期兼容窗口单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import insert_execution, insert_recovery_point
from app.services.application_lifecycle import (
    create_application_lifecycle,
    write_application_lifecycle,
)
from app.services.application_planning_recovery_coordinator import (
    resolve_application_planning_recovery,
)
from app.services.execution_recovery_coordinator import prepare_continue
from app.services.execution_recovery_policies import (
    production_recovery_replay_policies,
)


class _RecoveryGraph:
    """返回与 RecoveryPoint identity 一致的固定 checkpoint。"""

    def __init__(self, snapshot: SimpleNamespace) -> None:
        """保存当前场景唯一 checkpoint。"""

        self.snapshot = snapshot

    async def aget_state(self, _config: dict[str, object]) -> SimpleNamespace:
        """为 P0.3A 精确回查返回固定快照。"""

        return self.snapshot


class ApplicationPlanningRecoveryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 committed answer 的两个合法窗口与所有关键拒绝边界。"""

    def setUp(self) -> None:
        """为每个测试创建隔离的 lifecycle 和 recovery store。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        """清理隔离工作区。"""

        self.temporary_workspace.cleanup()

    async def test_committed_answer_before_requirements_lifecycle_transition_is_ready(
        self,
    ) -> None:
        """Window A 应绕过旧 awaiting_user 冲突并完成正式恢复校验。"""

        source = await self._insert_source(run_id="run-A", thread_id="thread-A")
        point = self._point(source=source, lifecycle_revision=10)
        await insert_recovery_point(workspace=self.workspace, point=point)
        lifecycle = self._lifecycle(
            source=source,
            revision=10,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)
        snapshot = self._snapshot(source=source, point=point)

        projection = await resolve_application_planning_recovery(
            workspace=str(self.workspace),
            thread_id=source.thread_id,
            graph=_RecoveryGraph(snapshot),
            snapshot=snapshot,
            lifecycle=lifecycle,
            source=source,
        )

        self.assertEqual(projection.classification, "ready_to_continue")
        self.assertTrue(projection.can_continue)
        self.assertTrue(projection.input_committed)

    async def test_committed_answer_allows_single_requirements_lifecycle_advance(
        self,
    ) -> None:
        """Window B 应使用重新验证后的 N+1 revision 生成 Native plan。"""

        source = await self._insert_source(run_id="run-B", thread_id="thread-B")
        point = self._point(source=source, lifecycle_revision=10)
        await insert_recovery_point(workspace=self.workspace, point=point)
        lifecycle = self._lifecycle(
            source=source,
            revision=11,
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)

        plan = await self._prepare(source=source, point=point)

        self.assertEqual(plan.decision, RecoveryDecision.READY_NATIVE)
        self.assertEqual(plan.lifecycle_revision, 11)
        self.assertEqual(point.lifecycle_revision, 10)

    async def test_unexplained_revision_stage_status_or_owner_drift_is_rejected(
        self,
    ) -> None:
        """额外 revision、错误阶段状态或错误 owner 都必须保持 fail closed。"""

        awaiting = ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION
        analyzing = ApplicationLifecycleStage.ANALYZING_REQUIREMENT
        generating = ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT
        awaiting_user = ApplicationLifecycleStatus.AWAITING_USER
        running = ApplicationLifecycleStatus.RUNNING
        failed = ApplicationLifecycleStatus.FAILED
        cases = (
            ("awaiting_revision_plus_one", 11, awaiting, awaiting_user, None, None),
            ("revision_plus_two", 12, analyzing, running, None, None),
            ("wrong_active_run", 10, analyzing, running, "other-run", None),
            ("wrong_thread", 10, analyzing, running, None, "other-thread"),
            ("wrong_stage", 10, generating, running, None, None),
            ("wrong_analyzing_status", 10, analyzing, failed, None, None),
            ("wrong_awaiting_status", 10, awaiting, running, None, None),
        )

        for index, case in enumerate(cases):
            name, revision, stage, status, active_run_id, thread_id = case
            with self.subTest(name=name):
                source = await self._insert_source(
                    run_id=f"run-drift-{index}",
                    thread_id=f"thread-drift-{index}",
                )
                point = self._point(source=source, lifecycle_revision=10)
                await insert_recovery_point(workspace=self.workspace, point=point)
                lifecycle = self._lifecycle(
                    source=source,
                    revision=revision,
                    stage=stage,
                    status=status,
                    active_run_id=active_run_id,
                    thread_id=thread_id,
                )
                write_application_lifecycle(self.workspace, lifecycle)
                plan = await self._prepare(source=source, point=point)

                self.assertEqual(plan.decision, RecoveryDecision.STATE_DRIFT)
                self.assertEqual(plan.reason_code, "LIFECYCLE_DRIFT")

    async def test_non_candidate_keeps_generic_lifecycle_validation(self) -> None:
        """非 committed answer 现场继续走 generic Lifecycle 与默认 replay 拒绝。"""

        source = await self._insert_source(run_id="run-generic", thread_id="thread-generic")
        point = self._point(source=source, lifecycle_revision=10)
        await insert_recovery_point(workspace=self.workspace, point=point)
        lifecycle = self._lifecycle(
            source=source,
            revision=10,
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
        )
        write_application_lifecycle(self.workspace, lifecycle)

        plan = await self._prepare(
            source=source,
            point=point,
            interaction_action="confirm",
        )

        self.assertEqual(plan.decision, RecoveryDecision.REQUIRES_HANDLER)
        self.assertEqual(plan.reason_code, "REPLAY_SAFETY_UNASSESSED")

    async def test_other_successor_or_interaction_cannot_use_transition_window(
        self,
    ) -> None:
        """非 requirements successor 与非 answer 交互不能获得特殊权限。"""

        cases = (
            ("technical_planning", ["technical_planning"], "answer"),
            ("confirm", ["requirements"], "confirm"),
        )
        for index, (name, next_nodes, action) in enumerate(cases):
            with self.subTest(name=name):
                source = await self._insert_source(
                    run_id=f"run-policy-{index}",
                    thread_id=f"thread-policy-{index}",
                )
                point = self._point(
                    source=source,
                    lifecycle_revision=10,
                    next_nodes=next_nodes,
                )
                await insert_recovery_point(workspace=self.workspace, point=point)
                lifecycle = self._lifecycle(
                    source=source,
                    revision=11,
                    stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    status=ApplicationLifecycleStatus.RUNNING,
                )
                write_application_lifecycle(self.workspace, lifecycle)
                plan = await self._prepare(
                    source=source,
                    point=point,
                    interaction_action=action,
                )

                self.assertEqual(plan.decision, RecoveryDecision.STATE_DRIFT)
                self.assertEqual(plan.reason_code, "LIFECYCLE_DRIFT")

    async def _prepare(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        interaction_action: str = "answer",
    ):
        """使用生产 replay policies 运行当前场景的 P0.3A 校验。"""

        return await prepare_continue(
            workspace=str(self.workspace),
            source_run_id=source.run_id,
            graph=_RecoveryGraph(
                self._snapshot(
                    source=source,
                    point=point,
                    interaction_action=interaction_action,
                )
            ),
            replay_policies=production_recovery_replay_policies(),
        )

    async def _insert_source(
        self,
        *,
        run_id: str,
        thread_id: str,
    ) -> DurableExecutionRecord:
        """写入一个中断的 Application Planning durable source。"""

        source = DurableExecutionRecord(
            run_id=run_id,
            thread_id=thread_id,
            workspace=str(self.workspace),
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="requirements",
            current_node="requirements",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=self.now,
            updated_at=self.now,
            ended_at=self.now,
        )
        await insert_execution(source)
        return source

    def _point(
        self,
        *,
        source: DurableExecutionRecord,
        lifecycle_revision: int,
        next_nodes: list[str] | None = None,
    ) -> RecoveryPoint:
        """构造保留历史 lifecycle revision 的 committed-input RecoveryPoint。"""

        successors = next_nodes or ["requirements"]
        return RecoveryPoint(
            recovery_point_id=f"point-{source.run_id}",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id=f"checkpoint-{source.run_id}",
            checkpoint_ns="",
            graph_node=successors[0],
            completed_node=None,
            next_nodes=successors,
            lifecycle_revision=lifecycle_revision,
            captured_at=self.now,
        )

    def _lifecycle(
        self,
        *,
        source: DurableExecutionRecord,
        revision: int,
        stage: ApplicationLifecycleStage,
        status: ApplicationLifecycleStatus,
        active_run_id: str | None = None,
        thread_id: str | None = None,
    ) -> ApplicationLifecycle:
        """构造指定 revision、阶段与 owner 的 lifecycle 快照。"""

        owner_thread_id = thread_id or source.thread_id
        lifecycle = create_application_lifecycle(
            application_id="app-1",
            application_name="花名册",
            initialization_thread_id=owner_thread_id,
            active_run_id=active_run_id or source.run_id,
        )
        return lifecycle.model_copy(
            update={
                "revision": revision,
                "initialization": ApplicationInitialization(
                    stage=stage,
                    status=status,
                    threadId=owner_thread_id,
                ),
            }
        )

    def _snapshot(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        interaction_action: str = "answer",
    ) -> SimpleNamespace:
        """构造已提交回答且没有 Native Interrupt 的精确 checkpoint。"""

        return SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": source.thread_id,
                    "checkpoint_ns": point.checkpoint_ns,
                    "checkpoint_id": point.checkpoint_id,
                }
            },
            next=tuple(point.next_nodes),
            tasks=(),
            values={
                "active_run_id": source.run_id,
                "application_planning_interaction": {
                    "action": interaction_action,
                    "artifact": "requirement_spec",
                    "gate_id": "requirement_spec:revision-1",
                    "artifact_revision": "revision-1",
                    "answers": {"role": "本人"},
                    "request": "创建花名册",
                },
            },
        )


__all__ = ["ApplicationPlanningRecoveryLifecycleTests"]
