from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import insert_execution, list_recovery_points
from app.services.application_lifecycle import (
    create_application_lifecycle,
    transition_application_lifecycle,
)
from app.services.application_planning_recovery_coordinator import (
    resolve_application_planning_recovery,
    sanitize_application_planning_recovery_result,
)
from app.services.application_planning_recovery_policy import (
    ApplicationPlanningCommittedInputReplayPolicy,
)


class _RecoveryGraph:
    """返回同一真实 checkpoint，供索引补写和 Coordinator 精确回查。"""

    def __init__(self, snapshot: object) -> None:
        """保存固定快照并记录读取次数。"""

        self.snapshot = snapshot
        self.read_count = 0

    async def aget_state(self, _config: dict[str, object]) -> object:
        """模拟 latest 与精确 checkpoint 查询。"""

        self.read_count += 1
        return self.snapshot


class ApplicationPlanningRecoveryConsistencyTests(unittest.IsolatedAsyncioTestCase):
    """覆盖已提交回答、真实 Native Interrupt 与冲突 Lifecycle。"""

    def setUp(self) -> None:
        """为每个恢复场景创建隔离的 Recovery Store。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        """清理隔离工作区。"""

        self.temporary_workspace.cleanup()

    async def test_stale_clarification_with_committed_answer_is_ready(self) -> None:
        """旧 requires_user_input 没有 Native Interrupt 时不得复活问题。"""

        source = await self._insert_source(DurableExecutionStatus.INTERRUPTED)
        snapshot = self._snapshot()
        graph = _RecoveryGraph(snapshot)

        projection = await resolve_application_planning_recovery(
            workspace=str(self.workspace),
            thread_id=source.thread_id,
            graph=graph,
            snapshot=snapshot,
            lifecycle=None,
            source=source,
        )
        public = sanitize_application_planning_recovery_result(
            dict(snapshot.values),
            projection=projection,
        )

        self.assertEqual(projection.classification, "ready_to_continue")
        self.assertTrue(projection.can_continue)
        self.assertTrue(projection.input_committed)
        self.assertFalse(projection.user_action_required)
        self.assertEqual(public["status"], "failed")
        self.assertNotIn("clarification", public)
        self.assertNotIn("application_planning_interrupt", public)
        points = await list_recovery_points(self.workspace, source.run_id)
        self.assertEqual(len(points), 1)
        self.assertIsNone(points[0].completed_node)
        self.assertEqual(points[0].checkpoint_id, "cp-committed")

        repeated = await resolve_application_planning_recovery(
            workspace=str(self.workspace),
            thread_id=source.thread_id,
            graph=graph,
            snapshot=snapshot,
            lifecycle=None,
            source=source,
        )
        self.assertEqual(repeated, projection)
        self.assertEqual(len(await list_recovery_points(self.workspace, source.run_id)), 1)

    async def test_typed_native_interrupt_is_the_only_awaiting_user_authority(self) -> None:
        """真实 application_planning_review 中断必须优先保护当前门禁。"""

        source = await self._insert_source(DurableExecutionStatus.AWAITING_USER)
        snapshot = self._snapshot(
            tasks=(
                SimpleNamespace(
                    interrupts=(
                        SimpleNamespace(
                            id="interrupt-1",
                            value={
                                "type": "application_planning_review",
                                "gateId": "requirement_spec:revision-1",
                                "artifact": "requirement_spec",
                                "artifactRevision": "revision-1",
                                "phase": "requirements",
                                "clarification": {
                                    "mode": "ask_user_question",
                                    "status": "requires_user_input",
                                    "questions": [{"id": "role", "prompt": "你的角色？"}],
                                },
                            },
                        ),
                    )
                ),
            ),
        )

        projection = await resolve_application_planning_recovery(
            workspace=str(self.workspace),
            thread_id=source.thread_id,
            graph=_RecoveryGraph(snapshot),
            snapshot=snapshot,
            lifecycle=None,
            source=source,
        )

        self.assertEqual(projection.classification, "awaiting_user")
        self.assertTrue(projection.user_action_required)
        self.assertFalse(projection.can_continue)

    async def test_lifecycle_awaiting_user_without_committed_answer_is_conflict(self) -> None:
        """未提交回答时 Lifecycle 单方面等待用户仍只能返回冲突。"""

        source = await self._insert_source(DurableExecutionStatus.INTERRUPTED)
        lifecycle = create_application_lifecycle(
            application_id="app-1",
            application_name="Test",
            initialization_thread_id=source.thread_id,
        )
        lifecycle = transition_application_lifecycle(
            lifecycle,
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
        )
        lifecycle = transition_application_lifecycle(
            lifecycle,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
        )
        snapshot = self._snapshot(interaction_action="confirm")

        projection = await resolve_application_planning_recovery(
            workspace=str(self.workspace),
            thread_id=source.thread_id,
            graph=_RecoveryGraph(snapshot),
            snapshot=snapshot,
            lifecycle=lifecycle,
            source=source,
        )

        self.assertEqual(projection.classification, "conflict")
        self.assertFalse(projection.user_action_required)
        self.assertFalse(projection.can_continue)

    async def test_replay_policy_denies_every_other_planning_checkpoint(self) -> None:
        """节点、动作、身份或中断任一不匹配时都不能获得 Native Replay。"""

        source = await self._insert_source(DurableExecutionStatus.INTERRUPTED)
        base_snapshot = self._snapshot()
        base_point = RecoveryPoint(
            recovery_point_id="policy-point",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id="cp-committed",
            checkpoint_ns="",
            graph_node="requirements",
            completed_node=None,
            next_nodes=["requirements"],
            captured_at=self.now,
        )
        policy = ApplicationPlanningCommittedInputReplayPolicy()
        cases: list[tuple[str, DurableExecutionRecord, RecoveryPoint, SimpleNamespace]] = []

        cases.append(
            (
                "other_next",
                source,
                base_point.model_copy(update={"next_nodes": ["product_planning"]}),
                base_snapshot,
            )
        )
        for name, interaction_patch in (
            ("confirm", {"action": "confirm"}),
            ("design_change", {"action": "design_change"}),
            ("gate_missing", {"gate_id": ""}),
            ("revision_missing", {"artifact_revision": ""}),
        ):
            values = dict(base_snapshot.values)
            values["application_planning_interaction"] = {
                **values["application_planning_interaction"],
                **interaction_patch,
            }
            cases.append((name, source, base_point, base_snapshot.__class__(**{
                **base_snapshot.__dict__,
                "values": values,
            })))
        missing_values = dict(base_snapshot.values)
        missing_values.pop("application_planning_interaction")
        cases.append((
            "interaction_missing",
            source,
            base_point,
            base_snapshot.__class__(**{**base_snapshot.__dict__, "values": missing_values}),
        ))
        interrupt_snapshot = self._snapshot(
            tasks=(
                SimpleNamespace(
                    interrupts=(
                        SimpleNamespace(
                            value={"type": "application_planning_review"},
                        ),
                    )
                ),
            )
        )
        cases.append(("native_interrupt", source, base_point, interrupt_snapshot))
        cases.append((
            "workbench_source",
            source.model_copy(update={"execution_kind": "workbench"}),
            base_point,
            base_snapshot,
        ))

        for name, case_source, point, snapshot in cases:
            with self.subTest(name=name):
                self.assertIsNone(
                    policy.assess(
                        source=case_source,
                        point=point,
                        snapshot=snapshot,
                    )
                )

    async def _insert_source(
        self,
        status: DurableExecutionStatus,
    ) -> DurableExecutionRecord:
        """写入当前测试 thread 的 Application Planning Durable source。"""

        source = DurableExecutionRecord(
            run_id="run-A",
            thread_id="planning-thread",
            workspace=str(self.workspace),
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="requirements",
            current_node="requirements",
            status=status,
            started_at=self.now,
            updated_at=self.now,
            ended_at=(self.now if status is not DurableExecutionStatus.RUNNING else None),
        )
        await insert_execution(source)
        return source

    def _snapshot(
        self,
        *,
        tasks: tuple[object, ...] = (),
        checkpoint_id: str = "cp-committed",
        next_nodes: tuple[str, ...] = ("requirements",),
        interaction_action: str = "answer",
    ) -> SimpleNamespace:
        """构造回答已写入但旧 clarification 仍残留的真实 checkpoint。"""

        return SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": "planning-thread",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint_id,
                }
            },
            next=next_nodes,
            tasks=tasks,
            values={
                "active_run_id": "run-A",
                "phase": "requirements",
                "status": "requires_user_input",
                "clarification": {
                    "mode": "ask_user_question",
                    "status": "requires_user_input",
                    "questions": [{"id": "role", "prompt": "你的角色？"}],
                },
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


__all__ = ["ApplicationPlanningRecoveryConsistencyTests"]
