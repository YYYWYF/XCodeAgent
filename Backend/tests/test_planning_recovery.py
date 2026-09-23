from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import asyncio

from app.services import planning_run as transitions
from app.services.build_task_planning_service import persist_planning_recovery_if_applicable
from app.services.build_task_planning_service import run_mainline_planning
from app.services.dag_planning_inputs import MainlinePlanningInputs
from app.services.dag_planning_orchestrator import DagPlanningError
from app.services.planning_recovery_contracts import (
    PlanningRecoverySnapshot,
    build_planning_recovery_snapshot,
    planning_recovery_snapshot_digest,
)
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import PlanningRun
from app.services.unit_generation_contracts import CandidateAttempt
from app.workspace.planning_recovery_documents import (
    delete_planning_recovery,
    load_planning_recovery,
    planning_recovery_directory,
    planning_recovery_path,
    write_planning_recovery_atomic,
)
from app.workspace.planning_run_documents import planning_run_json_path, write_planning_run_atomic
from app.workspace.planning_run_documents import load_planning_run
from tests.planning_run_fixtures import AT, ready, run, unit
from tests.dag_planning_orchestrator_fixtures import model_tasks, planning_inputs
from tests.test_unit_generation_contracts import _policy_payload
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.services.unit_generation_contracts import UnitGenerationAttemptResult, UnitGenerationPolicy


def _infrastructure_issue() -> ValidationIssue:
    """创建第一版 Recovery 唯一允许的基础设施失败。"""

    return ValidationIssue(
        code="UNIT_GENERATION_INFRASTRUCTURE_FAILURE",
        level="system",
        category="infrastructure",
        unit_ids=("page:failed",),
        retryable=False,
        message="provider unavailable",
        details={"attempt_id": "attempt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )


class PlanningRecoveryContractTests(unittest.TestCase):
    """验证 Recovery Snapshot 的独立合同、Candidate 指针提取和摘要门禁。"""

    def test_builder_extracts_only_current_ready_candidates(self) -> None:
        """failed Run 只保存 candidate_ready Unit 的 latest Candidate，忽略 aborted Unit。"""

        initial = run(unit("page:a"), unit("page:failed"), unit("page:c"))
        generating = transitions.begin_generation(initial, at=AT)
        current = ready(generating, "page:a")
        current = ready(current, "page:c")
        failed = transitions.fail(current, _infrastructure_issue(), at=AT)

        snapshot = build_planning_recovery_snapshot(
            failed,
            owner_session_id="session-1",
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(set(snapshot.candidates_by_unit), {"page:a", "page:c"})
        self.assertEqual(
            {
                candidate.candidate_id
                for candidate in snapshot.candidates_by_unit.values()
            },
            {
                failed.unit_states[key].latest_candidate_id
                for key in ("page:a", "page:c")
            },
        )
        self.assertEqual(snapshot.source_workflow_run_id, "workflow-1")
        self.assertEqual(snapshot.source_planning_run_id, "run-1")

    def test_round_trip_and_digest_mismatch_are_rejected(self) -> None:
        """Snapshot 可序列化恢复，但任意正文改动都会被 digest 拒绝。"""

        initial = transitions.begin_generation(run(), at=AT)
        failed = transitions.fail(ready(initial), _infrastructure_issue(), at=AT)
        snapshot = build_planning_recovery_snapshot(failed, owner_session_id="session-1")
        assert snapshot is not None

        restored = PlanningRecoverySnapshot.model_validate_json(snapshot.model_dump_json())
        self.assertEqual(restored, snapshot)
        self.assertEqual(planning_recovery_snapshot_digest(snapshot), snapshot.snapshot_digest)

        payload = snapshot.model_dump(mode="json")
        payload["candidates_by_unit"]["page:orders"]["generation_metadata"] = {"changed": True}
        with self.assertRaises(ValueError):
            PlanningRecoverySnapshot.model_validate(payload)

    def test_failure_gate_and_empty_ready_set(self) -> None:
        """普通失败不能建 Recovery；没有 ready Candidate 时返回空而非伪造空 Snapshot。"""

        generating = transitions.begin_generation(run(), at=AT)
        failed_without_ready = transitions.fail(generating, _infrastructure_issue(), at=AT)
        self.assertIsNone(
            build_planning_recovery_snapshot(
                failed_without_ready,
                owner_session_id="session-1",
            )
        )

        cancelled = transitions.cancel(generating, at=AT)
        with self.assertRaises(ValueError):
            build_planning_recovery_snapshot(cancelled, owner_session_id="session-1")

        ordinary_failure = ValidationIssue(
            code="UNIT_GENERATION_FROZEN_CONTRACT_SOURCE_INVALID",
            level="system",
            category="platform",
            unit_ids=("page:orders",),
            retryable=False,
            message="frozen source invalid",
        )
        failed_with_other_code = transitions.fail(ready(generating), ordinary_failure, at=AT)
        with self.assertRaises(ValueError):
            build_planning_recovery_snapshot(failed_with_other_code, owner_session_id="session-1")

    def test_generated_and_recovered_candidates_are_saved_as_current_source_state(self) -> None:
        """source Run 可同时保存 generated/recovered Candidate，且不追溯旧 Recovery。"""

        recovered_id = "candidate-" + "b" * 32
        recovered = CandidateAttempt(
            candidate_id=recovered_id,
            identity={
                "planning_run_id": "run-1",
                "unit_id": "page:orders",
                "generation_round": 1,
            },
            origin="recovered",
            generated_from=None,
            recovered_from={
                "source_planning_run_id": "run-0",
                "source_candidate_id": "candidate-" + "a" * 32,
            },
            input_fingerprint="frozen-input",
            status="valid",
            tasks=({"id": "task:recovered", "unit_id": "page:orders"},),
        )
        recovered_unit = unit("page:orders").model_copy(update={
            "generation_status": "candidate_ready",
            "latest_candidate_id": recovered_id,
            "candidate_task_count": 1,
        })
        pending_unit = unit("page:failed")
        failed = PlanningRun(
            planning_run_id="run-1",
            workflow_run_id="workflow-recovered",
            thread_id="thread-1",
            status="failed",
            phase="generating_units",
            build_execution_scope={"type": "page", "targetId": "orders"},
            input_fingerprint="frozen-input",
            base_confirmed_plan_digest="confirmed-digest",
            required_unit_ids=("page:orders", "page:failed"),
            planning_unit_ids=("page:orders", "page:failed"),
            unit_states={
                "page:orders": recovered_unit,
                "page:failed": pending_unit.model_copy(update={"generation_status": "aborted"}),
            },
            failure=_infrastructure_issue(),
            started_at=AT,
            updated_at=AT,
            candidates={recovered_id: recovered},
        )

        snapshot = build_planning_recovery_snapshot(failed, "session-1")

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.candidates_by_unit["page:orders"].origin, "recovered")


class PlanningRecoveryDocumentTests(unittest.TestCase):
    """验证 Recovery 文件的显式 ID 路径、原子写入和不回退读取。"""

    def _snapshot(self):
        """创建可写入文档的最小合法 Snapshot。"""

        generating = transitions.begin_generation(run(), at=AT)
        failed = transitions.fail(ready(generating), _infrastructure_issue(), at=AT)
        snapshot = build_planning_recovery_snapshot(failed, owner_session_id="session-1")
        assert snapshot is not None
        return snapshot

    def test_explicit_workflow_ids_are_isolated_and_exactly_loaded(self) -> None:
        """run-A 与 run-B 互不覆盖，load 只读取指定文件而不猜最近结果。"""

        snapshot = self._snapshot()
        # 变更 source identity 后先重算摘要，再构造新的合法 Snapshot。
        snapshot_b_payload = snapshot.model_dump(mode="json")
        snapshot_b_payload["source_workflow_run_id"] = "run-B"
        snapshot_b_payload["snapshot_digest"] = planning_recovery_snapshot_digest(snapshot_b_payload)
        snapshot_b = PlanningRecoverySnapshot.model_validate(snapshot_b_payload)

        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            write_planning_recovery_atomic(state, snapshot)
            write_planning_recovery_atomic(state, snapshot_b)

            self.assertEqual(load_planning_recovery(state, "workflow-1"), snapshot)
            self.assertEqual(load_planning_recovery(state, "run-B"), snapshot_b)
            self.assertIsNone(load_planning_recovery(state, "missing"))
            self.assertEqual(
                sorted(path.name for path in planning_recovery_directory(state).iterdir()),
                ["run-B.json", "workflow-1.json"],
            )

    def test_path_traversal_is_rejected(self) -> None:
        """文件名组件拒绝点路径、正反斜杠和目录逃逸。"""

        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            for unsafe in (".", "..", "../run", "a/b", r"a\b"):
                with self.subTest(unsafe=unsafe), self.assertRaises((TypeError, ValueError)):
                    planning_recovery_path(state, unsafe)

    def test_atomic_storage_does_not_change_planning_projection(self) -> None:
        """Recovery 写入独立 runtime 路径，planning-run.json 仍没有 Candidate 正文。"""

        snapshot = self._snapshot()
        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            write_planning_run_atomic(state, transitions.begin_generation(run(), at=AT))
            before = planning_run_json_path(state).read_bytes()
            target = Path(write_planning_recovery_atomic(state, snapshot))
            after = planning_run_json_path(state).read_bytes()

            self.assertTrue(target.is_file())
            self.assertEqual(before, after)
            raw = target.read_text(encoding="utf-8")
            self.assertIn("candidates_by_unit", raw)
            self.assertNotIn('"candidates":', after.decode("utf-8"))
            self.assertFalse(list(target.parent.glob("*.tmp")))

    def test_corrupt_or_tampered_snapshot_is_not_silent(self) -> None:
        """JSON 损坏和 digest 损坏必须报告 invalid，而不是当作不存在。"""

        snapshot = self._snapshot()
        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            target = planning_recovery_path(state, snapshot.source_workflow_run_id)
            target.parent.mkdir(parents=True)
            target.write_text("{corrupt", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_planning_recovery(state, snapshot.source_workflow_run_id)

            target.write_text(
                json.dumps({**snapshot.model_dump(mode="json"), "snapshot_digest": "0" * 64}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_planning_recovery(state, snapshot.source_workflow_run_id)

    def test_delete_is_explicit_only(self) -> None:
        """基础 delete primitive 只删除指定文件，缺失时幂等返回 False。"""

        snapshot = self._snapshot()
        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            write_planning_recovery_atomic(state, snapshot)
            self.assertTrue(delete_planning_recovery(state, snapshot.source_workflow_run_id))
            self.assertFalse(delete_planning_recovery(state, snapshot.source_workflow_run_id))


class PlanningRecoveryFailureBoundaryTests(unittest.TestCase):
    """验证 Recovery persistence failure 不会覆盖原始 DagPlanningError。"""

    def test_storage_failure_is_logged_and_original_error_remains(self) -> None:
        """writer 抛错时 helper 只告警，调用方仍可原样传播 DagPlanningError。"""

        generating = transitions.begin_generation(run(), at=AT)
        failed = transitions.fail(ready(generating), _infrastructure_issue(), at=AT)
        original = DagPlanningError((failed.failure,), failed)

        with patch(
            "app.services.build_task_planning_service.write_planning_recovery_atomic",
            side_effect=OSError("disk full"),
        ):
            with TemporaryDirectory() as directory:
                persist_planning_recovery_if_applicable(
                    {"workspace": directory},
                    original,
                    owner_session_id="session-1",
                )

        self.assertIs(original.snapshot, failed)
        self.assertEqual(original.issues[0].code, "UNIT_GENERATION_INFRASTRUCTURE_FAILURE")


class PlanningRecoveryProductionHookTests(unittest.IsolatedAsyncioTestCase):
    """验证 production mainline 在 Scheduler fatal 收口后写自己的 source Workflow Snapshot。"""

    def setUp(self) -> None:
        """创建隔离工作区及固定 Unit generation policy。"""

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.policy = UnitGenerationPolicy(**_policy_payload())

    async def test_mainline_failure_writes_ready_sibling_only(self) -> None:
        """一个 sibling 已 ready 后另一个 Unit infrastructure fail 时，Snapshot 保存前者正文。"""

        sequential = planning_inputs(required=["page:a", "page:b"])
        inputs = MainlinePlanningInputs.model_validate({
            **sequential.model_dump(mode="python"),
            "owner_session_id": "session-mainline",
            "workflow_run_id": "workflow-mainline-recovery",
            "thread_id": "thread-mainline",
        })
        first_returned = asyncio.Event()

        async def generate(job, **_: object) -> UnitGenerationAttemptResult:
            """让成功 sibling 先完成，再确定性触发另一个 Unit 的 fatal。"""

            if job.identity.unit_id == "page:b":
                await first_returned.wait()
                while True:
                    persisted = load_planning_run(self.state)
                    if (
                        persisted is not None
                        and persisted["unit_states"]["page:a"]["generation_status"]
                        == "candidate_ready"
                    ):
                        break
                    await asyncio.sleep(0)
                raise UnitGenerationInfrastructureError(
                    identity=job.identity,
                    stage="model_invoke",
                    cause=RuntimeError("provider unavailable"),
                )
            tasks = model_tasks(job)
            first_returned.set()
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        with patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-mainline-recovery",
        ):
            with self.assertRaises(DagPlanningError) as caught:
                await run_mainline_planning(
                    inputs,
                    workspace_state=self.state,
                    policy=self.policy,
                    generate_once=generate,
                )

        snapshot = load_planning_recovery(self.state, "workflow-mainline-recovery")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(set(snapshot.candidates_by_unit), {"page:a"})
        self.assertEqual(
            snapshot.candidates_by_unit["page:a"].identity.planning_run_id,
            "planning-mainline-recovery",
        )
        self.assertEqual(
            caught.exception.issues[0].code,
            "UNIT_GENERATION_INFRASTRUCTURE_FAILURE",
        )
        self.assertEqual(load_planning_run(self.state)["status"], "failed")
        self.assertNotIn(
            '"candidates":',
            planning_run_json_path(self.state).read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
