"""T2 前置契约：dispatch 身份回传、配置一致性与冻结快照。"""

import unittest

from pydantic import ValidationError

from app.config import Settings
from app.services.planning_issues import ValidationIssue, group_issues_by_retry_unit
from app.services.unit_generation_contracts import (
    AttemptIdentity, CandidateAttempt, UnitAttemptJob, UnitGenerationAttemptResult,
    UnitGenerationContext, UnitGenerationPolicy,
)
from tests.test_unit_generation_contracts import (
    _candidate_payload, _context_payload, _identity_payload, _policy_payload, _result_payload,
)


class UnitAttemptIdentityTests(unittest.TestCase):
    def test_identity_requires_every_field_and_valid_platform_id(self) -> None:
        """身份缺项或非法轮次、ID 必须失败；结果不得临时生成缺失身份。"""

        for field in _identity_payload():
            payload = _identity_payload()
            del payload[field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AttemptIdentity(**payload)
            with self.assertRaises(ValidationError):
                UnitGenerationAttemptResult(**{**_result_payload(), "identity": payload})
        for change in (
            {"generation_round": 0}, {"attempt_in_round": -1}, {"attempt_in_round": True},
            {"generation_round": "1"}, {"attempt_id": "candidate-" + "a" * 32},
            {"attempt_id": "model-task-orders"}, {"unit_id": " "}, {"planning_run_id": ""},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                AttemptIdentity(**{**_identity_payload(), **change})

    def test_platform_allocates_distinct_ids_before_dispatch(self) -> None:
        """相同 Run/Unit/计数也分配独立 dispatch ID，复制与序列化保留原 ID。"""

        fields = {key: value for key, value in _identity_payload().items() if key != "attempt_id"}
        first = AttemptIdentity.allocate(**fields)
        second = AttemptIdentity.allocate(**fields)
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        self.assertEqual(first.model_copy(), first)
        self.assertEqual(AttemptIdentity.model_validate_json(first.model_dump_json()), first)
        with self.assertRaises(ValidationError):
            first.attempt_id = second.attempt_id

    def test_job_requires_context_policy_and_matching_run_unit(self) -> None:
        """Job 三部分均必填，构造和复制均拒绝与 Context 不匹配的身份。"""

        payload = {"identity": _identity_payload(), "context": _context_payload(), "policy": _policy_payload()}
        job = UnitAttemptJob(**payload)
        for field in payload:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                UnitAttemptJob(**{key: value for key, value in payload.items() if key != field})
        for field in ("planning_run_id", "unit_id"):
            identity = {**_identity_payload(), field: "other"}
            with self.assertRaises(ValidationError):
                UnitAttemptJob(**{**payload, "identity": identity})
            with self.assertRaises(ValidationError):
                job.model_copy(update={"identity": identity})
        with self.assertRaises(ValidationError):
            job.context = UnitGenerationContext(**_context_payload())
        with self.assertRaises(TypeError):
            job.context.constraints["changed"] = True

    def test_same_context_results_preserve_attempt_and_round_identity(self) -> None:
        """相同业务指纹仍能区分晚到 attempt 和旧 round；这里只验证 DTO 不执行 discard。"""

        context = UnitGenerationContext(**_context_payload())
        results = []
        for generation_round, attempt_in_round in ((1, 1), (1, 2), (2, 1)):
            identity = AttemptIdentity.allocate(
                planning_run_id=context.planning_run_id, unit_id=context.unit_id,
                generation_round=generation_round, attempt_in_round=attempt_in_round,
            )
            job = UnitAttemptJob(identity=identity, context=context, policy=UnitGenerationPolicy(**_policy_payload()))
            result = UnitGenerationAttemptResult(**{**_result_payload(), "identity": job.identity})
            restored = UnitGenerationAttemptResult.model_validate_json(result.model_dump_json())
            self.assertEqual(restored.identity, job.identity)
            self.assertEqual(restored.input_fingerprint, context.input_fingerprint)
            candidate = CandidateAttempt(**{**_candidate_payload(), "identity": restored.identity})
            self.assertEqual(candidate.identity, identity)
            self.assertNotEqual(candidate.candidate_id, identity.attempt_id)
            self.assertEqual(candidate.tasks[0]["id"], "model-task-orders")
            results.append(restored)
        self.assertEqual(len({result.identity.attempt_id for result in results}), 3)
        self.assertEqual([(r.identity.generation_round, r.identity.attempt_in_round) for r in results], [(1, 1), (1, 2), (2, 1)])

    def test_flattened_identity_is_not_a_second_source_of_truth(self) -> None:
        """Candidate/Result 仅接受嵌套 identity，不保留重复字段或旧形状读取。"""

        for model, payload in ((CandidateAttempt, _candidate_payload()), (UnitGenerationAttemptResult, _result_payload())):
            for field, value in _identity_payload().items():
                with self.subTest(model=model.__name__, field=field), self.assertRaises(ValidationError):
                    model(**{**payload, field: value})

    def test_candidate_and_result_issues_cannot_mutate_snapshot_or_routing(self) -> None:
        """冻结记录内的 Issue 与多层诊断也只读；JSON 副本不会改变原路由。"""

        issue = ValidationIssue(
            code="missing_id", level="unit", category="generation", retryable=True,
            unit_ids=["page:retained"], task_ids=["task-1"], retry_unit_ids=["page:orders"],
            message="缺少任务 ID", details={"nested": [{"items": [1]}]},
        )
        for model, payload in ((CandidateAttempt, _candidate_payload("invalid")), (UnitGenerationAttemptResult, _result_payload())):
            instance = model(**{**payload, "validation_issues": [issue]})
            embedded = instance.validation_issues[0]
            with self.assertRaises(ValidationError):
                embedded.retryable = False
            with self.assertRaises(AttributeError):
                embedded.retry_unit_ids.append("page:other")
            with self.assertRaises(TypeError):
                embedded.details["nested"][0]["items"] = []
            with self.assertRaises(AttributeError):
                embedded.details["nested"][0]["items"].append(2)
            dumped = instance.model_dump(mode="json")
            dumped["validation_issues"][0]["retry_unit_ids"].clear()
            self.assertEqual(list(group_issues_by_retry_unit(instance.validation_issues)), ["page:orders"])
            self.assertEqual(model.model_validate_json(instance.model_dump_json()), instance)
            self.assertEqual(instance.model_copy(deep=True), instance)

    def test_settings_policy_handoff_preserves_fixed_local_contract(self) -> None:
        """Settings 的固定 Local 值直接构造 Policy，不存在接受 1 后交接失败的路径。"""

        settings = Settings(model_base_url="test", model_api_key="test", model_name="test")
        policy = UnitGenerationPolicy(
            **_policy_payload(), local_max_attempts=settings.dag_unit_local_max_attempts,
            model_max_tokens=settings.dag_unit_max_tokens,
        )
        self.assertEqual(policy.local_max_attempts, 3)
        self.assertEqual(settings.dag_global_repair_limit, 2)
