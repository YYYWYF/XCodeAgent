from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services import planning_run as transitions
from app.services.build_task_planning_service import persist_planning_recovery_if_applicable
from app.services.build_task_planning_service import run_mainline_planning
from app.services.dag_planning_inputs import MainlinePlanningInputs
from app.services.dag_planning_regeneration import regenerate_pending_build_task_plan
from app.services.dag_planning_orchestrator import DagPlanningError, plan_dag_sequential
from app.services.planning_frozen import plain_json
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
from app.workspace.task_documents import (
    build_planning_provenance,
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
)
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

    def test_load_rejects_filename_and_internal_source_id_mismatch(self) -> None:
        """完整合法的 A Snapshot 被复制到 B 文件时，loader 仍必须拒绝串 Run。"""

        snapshot = self._snapshot()
        payload = snapshot.model_dump(mode="json")
        payload["source_workflow_run_id"] = "run-A"
        payload["snapshot_digest"] = planning_recovery_snapshot_digest(payload)
        snapshot_a = PlanningRecoverySnapshot.model_validate(payload)

        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            target = planning_recovery_path(state, "run-B")
            target.parent.mkdir(parents=True)
            target.write_text(snapshot_a.model_dump_json(), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "source_workflow_run_id"):
                load_planning_recovery(state, "run-B")

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


class PlanningRecoveryRegenerateHookTests(unittest.IsolatedAsyncioTestCase):
    """验证 fresh Regenerate 只写自身失败 Run 的 Recovery，不读取旧 Snapshot。"""

    def setUp(self) -> None:
        """创建隔离工作区及固定 Unit generation policy。"""

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.policy = UnitGenerationPolicy(**_policy_payload())

    async def _generate_valid(self, job, **_: object) -> UnitGenerationAttemptResult:
        """为旧 Pending 生成完整合法的 Candidate。"""

        tasks = model_tasks(job)
        return UnitGenerationAttemptResult(
            identity=job.identity,
            input_fingerprint=job.context.input_fingerprint,
            raw_response=json.dumps({"tasks": tasks}),
            tasks=tasks,
        )

    def _write_old_recovery(self) -> PlanningRecoverySnapshot:
        """预置一个可验证的旧 Workflow Recovery，确保 Regenerate 不会读取它。"""

        generating = transitions.begin_generation(run(unit("page:a")), at=AT)
        failed = transitions.fail(
            ready(generating, "page:a"),
            _infrastructure_issue(),
            at=AT,
        )
        snapshot = build_planning_recovery_snapshot(failed, owner_session_id="old-session")
        assert snapshot is not None
        payload = snapshot.model_dump(mode="json")
        payload["source_workflow_run_id"] = "workflow-old"
        payload["snapshot_digest"] = planning_recovery_snapshot_digest(payload)
        old_snapshot = PlanningRecoverySnapshot.model_validate(payload)
        write_planning_recovery_atomic(self.state, old_snapshot)
        return old_snapshot

    async def _write_old_pending(self) -> dict:
        """创建旧 Pending，供真实 Regenerate facade 精确消费。"""

        inputs = planning_inputs(required=["page:a", "page:b"])
        old = await plan_dag_sequential(
            inputs,
            workspace_state=self.state,
            planning_run_id="planning-old",
            workflow_run_id="workflow-old",
            thread_id="thread-old",
            policy=self.policy,
            generate_once=self._generate_valid,
        )
        assembled_plan = plain_json(old.assembly.assembled_plan)
        write_pending_build_task_plan_atomic(
            self.state,
            assembled_plan,
            owner_session_id="session-regenerate",
            planning_run_id=old.planning_run.planning_run_id,
            workflow_run_id=old.planning_run.workflow_run_id,
            base_confirmed_plan_digest=old.planning_run.base_confirmed_plan_digest,
            input_fingerprint=old.planning_run.input_fingerprint,
            build_execution_scope=plain_json(old.planning_run.build_execution_scope),
            created_at=old.planning_run.updated_at,
            planning_provenance=build_planning_provenance(
                assembled_plan,
                old.assembly.retained_task_ids,
                old.assembly.review_task_ids,
                old.assembly.reused_task_ids,
                old.assembly.platform_task_ids,
            ),
        )
        pending = load_pending_build_task_plan(self.state)
        assert pending is not None
        return pending["draft_identity"]

    async def test_regenerate_writes_new_snapshot_without_reading_old_recovery(self) -> None:
        """旧 Recovery 存在时，A/B 仍 fresh 生成，失败只保存 workflow-new 的 A。"""

        old_snapshot = self._write_old_recovery()
        identity = await self._write_old_pending()
        generated_units: list[str] = []
        first_returned = asyncio.Event()

        async def generate(job, **_: object) -> UnitGenerationAttemptResult:
            """让 A 先成功并让 B 在其后确定性触发基础设施失败。"""

            generated_units.append(job.identity.unit_id)
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

        def current_inputs(formal: dict | None):
            """Regenerate 重新构造当前正式输入，不读取旧 Recovery。"""

            self.assertIsNone(formal)
            return planning_inputs(required=["page:a", "page:b"], baseline=formal)

        with self.assertRaises(DagPlanningError):
            await regenerate_pending_build_task_plan(
                self.state,
                planning_run_id=identity["planning_run_id"],
                draft_digest=identity["draft_digest"],
                workflow_run_id="workflow-new",
                thread_id="thread-new",
                current_inputs_factory=current_inputs,
                policy=self.policy,
                generate_once=generate,
                planning_run_id_factory=lambda: "planning-new",
            )

        self.assertEqual(set(generated_units), {"page:a", "page:b"})
        self.assertEqual(load_planning_recovery(self.state, "workflow-old"), old_snapshot)
        new_snapshot = load_planning_recovery(self.state, "workflow-new")
        self.assertIsNotNone(new_snapshot)
        assert new_snapshot is not None
        self.assertEqual(set(new_snapshot.candidates_by_unit), {"page:a"})
        self.assertEqual(
            new_snapshot.candidates_by_unit["page:a"].identity.planning_run_id,
            "planning-new",
        )
        self.assertIsNone(load_pending_build_task_plan(self.state))


class PlanningRecoveryRetryConsumptionTests(unittest.IsolatedAsyncioTestCase):
    """验证明确 Retry 在新 PlanningRun 中按当前输入重新校验并选择性恢复 Candidate。"""

    def setUp(self) -> None:
        """创建隔离工作区和固定 Unit 生成策略。"""

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.policy = UnitGenerationPolicy(**_policy_payload())

    def _mainline_inputs(self, sequential, *, workflow_run_id: str) -> MainlinePlanningInputs:
        """为同一正式输入绑定不同 Workflow execution 身份。"""

        return MainlinePlanningInputs.model_validate({
            **sequential.model_dump(mode="python"),
            "owner_session_id": "session-retry",
            "workflow_run_id": workflow_run_id,
            "thread_id": f"thread-{workflow_run_id}",
        })

    async def test_retry_creates_new_run_and_recovers_only_ready_siblings(self) -> None:
        """R1 失败后 R2 只复用当前仍有效的 A，并为失败 B 新建真实 Attempt。"""

        sequential = planning_inputs(required=["page:a", "page:b"])
        source_inputs = self._mainline_inputs(
            sequential,
            workflow_run_id="workflow-r1",
        )
        page_a_ready = asyncio.Event()

        async def source_generate(job, **_: object) -> UnitGenerationAttemptResult:
            """让 A 先提交 Candidate，再让 B 触发基础设施终止。"""

            if job.identity.unit_id == "page:b":
                await page_a_ready.wait()
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
                    cause=RuntimeError("source provider unavailable"),
                )
            tasks = model_tasks(job)
            page_a_ready.set()
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        with patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-r1",
        ):
            with self.assertRaises(DagPlanningError):
                await run_mainline_planning(
                    source_inputs,
                    workspace_state=self.state,
                    policy=self.policy,
                    generate_once=source_generate,
                )

        source_snapshot = load_planning_recovery(self.state, "workflow-r1")
        self.assertIsNotNone(source_snapshot)
        assert source_snapshot is not None
        source_candidate = source_snapshot.candidates_by_unit["page:a"]

        retry_calls: list[str] = []

        async def retry_generate(job, **_: object) -> UnitGenerationAttemptResult:
            """R2 的模型替身只允许失败 sibling 进入当前 Scheduler。"""

            retry_calls.append(job.identity.unit_id)
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        retry_inputs = self._mainline_inputs(
            sequential,
            workflow_run_id="workflow-r2",
        )
        with patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-r2",
        ):
            retry_result = await run_mainline_planning(
                retry_inputs,
                workspace_state=self.state,
                policy=self.policy,
                generate_once=retry_generate,
                recovery_source_workflow_run_id="workflow-r1",
            )

        current = retry_result.validated_assembled_plan.planning_run
        recovered = current.candidates[current.unit_states["page:a"].latest_candidate_id]
        regenerated = current.candidates[current.unit_states["page:b"].latest_candidate_id]
        self.assertEqual(retry_calls, ["page:b"])
        self.assertEqual(current.planning_run_id, "planning-r2")
        self.assertEqual(current.status, "active")
        self.assertEqual(current.input_fingerprint, source_snapshot.input_fingerprint)
        self.assertEqual(current.input_fingerprint, retry_inputs.sequential_inputs().input_fingerprint())
        self.assertEqual(recovered.origin, "recovered")
        self.assertIsNone(recovered.generated_from)
        self.assertEqual(recovered.identity.planning_run_id, "planning-r2")
        self.assertEqual(recovered.identity.generation_round, 1)
        self.assertEqual(recovered.recovered_from.source_planning_run_id, "planning-r1")
        self.assertEqual(recovered.recovered_from.source_candidate_id, source_candidate.candidate_id)
        self.assertNotEqual(recovered.candidate_id, source_candidate.candidate_id)
        self.assertEqual(
            (current.unit_states["page:a"].attempt_in_round, current.unit_states["page:a"].total_attempts),
            (0, 0),
        )
        self.assertEqual(regenerated.origin, "generated")
        self.assertIsNotNone(regenerated.generated_from)
        self.assertGreater(current.unit_states["page:b"].total_attempts, 0)
        self.assertEqual(load_planning_run(self.state)["status"], "active")
        self.assertEqual(load_planning_run(self.state)["planning_run_id"], "planning-r2")
        self.assertEqual(load_planning_recovery(self.state, "workflow-r1"), source_snapshot)

    async def test_corrupt_or_absent_recovery_is_fail_open_and_no_source_skips_loader(self) -> None:
        """损坏 Snapshot 只导致全量生成；普通调用没有 source ID 时根本不读 loader。"""

        inputs = self._mainline_inputs(
            planning_inputs(required=["page:a"]),
            workflow_run_id="workflow-fresh",
        )
        generated: list[str] = []

        async def generate(job, **_: object) -> UnitGenerationAttemptResult:
            """返回当前 Unit 的合法 fresh Candidate。"""

            generated.append(job.identity.unit_id)
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        with patch(
            "app.services.build_task_planning_service.load_planning_recovery",
            side_effect=ValueError("corrupt snapshot"),
        ) as loader:
            with patch(
                "app.services.build_task_planning_service._new_planning_run_id",
                return_value="planning-fresh",
            ):
                await run_mainline_planning(
                    inputs,
                    workspace_state=self.state,
                    policy=self.policy,
                    generate_once=generate,
                    recovery_source_workflow_run_id="workflow-corrupt",
                )
            loader.assert_called_once()

        generated.clear()
        with patch(
            "app.services.build_task_planning_service.load_planning_recovery",
        ) as loader:
            with patch(
                "app.services.build_task_planning_service._new_planning_run_id",
                return_value="planning-without-source",
            ):
                await run_mainline_planning(
                    inputs,
                    workspace_state=self.state,
                    policy=self.policy,
                    generate_once=generate,
                )
            loader.assert_not_called()
        self.assertEqual(generated, ["page:a"])


if __name__ == "__main__":
    unittest.main()
