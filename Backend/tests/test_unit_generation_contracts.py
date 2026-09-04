"""T1.2 DTO 序列化、冻结输入、字段边界与平台候选身份回归。"""

from copy import deepcopy
import json
import unittest

from pydantic import ValidationError

from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import (
    CandidateAttempt, GenerationRequirement, UnitGenerationAttemptResult,
    UnitGenerationContext, UnitGenerationPolicy,
)


def _context_payload() -> dict:
    """提供带多层合同、工作区、依赖及约束的完整业务快照。"""

    return {
        "planning_run_id": "planning-run-1", "unit_id": "page:orders", "unit_kind": "page",
        "build_execution_scope": {"type": "page", "targetId": "orders"},
        "input_fingerprint": "input-digest", "base_confirmed_plan_digest": None,
        "generation_requirements": [{
            "requirement_id": "orders-page", "description": "实现订单查询页面",
            "source_refs": {"artifact": "technical-plan", "pointers": ["/pages/orders"]},
        }],
        "formal_contracts": {
            "inline_slices": [{"endpoint_id": "orders.list", "fields": [{"name": "id"}]}],
            "frozen_catalog_refs": [{"ref_id": "orders-contract", "digest": "frozen-digest"}],
        },
        "workspace_context": {"snapshot_id": "workspace-1", "relevant_paths": ["frontend/src/pages/Orders/index.tsx"]},
        "dependency_context": {"dependency_unit_ids": ["frontend:api-client"], "retained_task_summaries": [{"id": "api:orders"}]},
        "constraints": {"owner": "frontend", "managed_files": ["frontend/src/constants/resources.ts"]},
    }


def _policy_payload() -> dict:
    """显式提供测试保护预算，数值不代表生产默认值。"""

    return {
        "request_timeout": 30.0, "unit_session_timeout": 120.0, "model_turn_limit": 4,
        "frozen_contract_read_limits": {"max_reads": 5, "max_total_bytes": 10000},
    }


def _candidate_payload(status: str = "valid") -> dict:
    """构造平台候选元数据，任务正文只使用模型原始 ID。"""

    return {
        "planning_run_id": "planning-run-1", "unit_id": "page:orders",
        "generation_round": 1, "attempt_in_round": 1, "input_fingerprint": "input-digest",
        "status": status, "tasks": [{"id": "model-task-orders", "dependencies": []}],
        "validation_issues": [], "generation_metadata": {"model": "test-model", "tokens": 10},
    }


def _result_payload() -> dict:
    """构造未经 Local Validator 判定的单次响应。"""

    return {
        "planning_run_id": "planning-run-1", "unit_id": "page:orders", "input_fingerprint": "input-digest",
        "raw_response": '{"tasks":[{"id":"model-task-orders"}]}',
        "tasks": [{"id": "model-task-orders"}],
        "validation_issues": [], "generation_metadata": {"finish_reason": "stop"},
    }


class UnitGenerationContractTests(unittest.TestCase):
    def test_all_models_serialize_and_round_trip(self) -> None:
        """五种模型均输出标准 JSON，反序列化保持业务内容和平台候选身份。"""

        cases = (
            (GenerationRequirement, _context_payload()["generation_requirements"][0]),
            (UnitGenerationContext, _context_payload()), (UnitGenerationPolicy, _policy_payload()),
            (CandidateAttempt, _candidate_payload()), (UnitGenerationAttemptResult, _result_payload()),
        )
        for model, payload in cases:
            with self.subTest(model=model.__name__):
                instance = model.model_validate(payload)
                encoded = instance.model_dump_json()
                dumped = instance.model_dump(mode="json")
                self.assertEqual(json.loads(encoded), dumped)
                self.assertEqual(model.model_validate_json(encoded), instance)
                self.assertEqual(model.model_validate(dumped), instance)
                for key, value in payload.items():
                    self.assertEqual(dumped[key], value)

    def test_all_declared_required_fields_are_required(self) -> None:
        """逐项省略核心字段均被拒绝，空基线必须显式传 None 而非遗漏 digest。"""

        cases = (
            (GenerationRequirement, _context_payload()["generation_requirements"][0], ("requirement_id", "description")),
            (UnitGenerationContext, _context_payload(), tuple(_context_payload())),
            (UnitGenerationPolicy, _policy_payload(), tuple(_policy_payload())),
            (CandidateAttempt, _candidate_payload(), ("planning_run_id", "unit_id", "generation_round", "attempt_in_round", "input_fingerprint", "status", "tasks")),
            (UnitGenerationAttemptResult, _result_payload(), ("planning_run_id", "unit_id", "input_fingerprint", "raw_response", "tasks")),
        )
        for model, payload, required in cases:
            for field in required:
                with self.subTest(model=model.__name__, missing=field), self.assertRaises(ValidationError):
                    incomplete = {key: value for key, value in payload.items() if key != field}
                    model.model_validate(incomplete)

    def test_frozen_models_reject_field_assignment(self) -> None:
        """冻结 DTO 不能就地替换字段或改变 Candidate 状态。"""

        cases = (
            (GenerationRequirement(**_context_payload()["generation_requirements"][0]), "description", "changed"),
            (UnitGenerationContext(**_context_payload()), "unit_id", "page:other"),
            (UnitGenerationPolicy(**_policy_payload()), "model_max_tokens", 100),
            (CandidateAttempt(**_candidate_payload()), "status", "superseded"),
            (UnitGenerationAttemptResult(**_result_payload()), "raw_response", "changed"),
        )
        for instance, field, value in cases:
            with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                setattr(instance, field, value)

    def test_context_freezes_nested_objects_and_arrays(self) -> None:
        """Context 所有业务区段递归只读，嵌套列表也不能绕过 frozen 约束。"""

        context = UnitGenerationContext(**_context_payload())
        for mapping in (
            context.build_execution_scope, context.formal_contracts, context.workspace_context,
            context.dependency_context, context.constraints,
            context.formal_contracts["inline_slices"][0]["fields"][0],
            context.generation_requirements[0].source_refs,
        ):
            with self.subTest(mapping=mapping), self.assertRaises(TypeError):
                mapping["changed"] = True
        for sequence in (
            context.generation_requirements, context.formal_contracts["inline_slices"],
            context.workspace_context["relevant_paths"], context.dependency_context["dependency_unit_ids"],
            context.constraints["managed_files"], context.generation_requirements[0].source_refs["pointers"],
        ):
            self.assertIsInstance(sequence, tuple)
            with self.assertRaises(AttributeError):
                sequence.append("changed")

    def test_input_and_serialized_output_mutations_do_not_change_context(self) -> None:
        """构造输入或序列化副本后续被修改时，冻结业务快照保持原值。"""

        payload = _context_payload()
        before = deepcopy(payload)
        context = UnitGenerationContext(**payload)
        payload["formal_contracts"]["inline_slices"][0]["fields"][0]["name"] = "changed"
        payload["generation_requirements"][0]["source_refs"]["pointers"].append("changed")
        payload["dependency_context"]["retained_task_summaries"][0]["id"] = "changed"
        dumped = context.model_dump(mode="json")
        dumped["workspace_context"]["relevant_paths"].clear()
        self.assertEqual(context.model_dump(mode="json"), before)

    def test_context_rejects_policy_and_attempt_counter_fields(self) -> None:
        """运行策略和 Attempt 计数均不属于 Context，不允许透传未知字段。"""

        forbidden = {
            **_policy_payload(), "local_max_attempts": 3, "model_max_retries": 0, "model_max_tokens": 4096,
            "retry_counter": 1, "generation_round": 1, "attempt_in_round": 1,
            "total_attempts": 1, "global_repair_round": 1, "token_budget": 4096, "timeout": 30,
        }
        for field, value in forbidden.items():
            with self.subTest(field=field), self.assertRaises(ValidationError):
                UnitGenerationContext(**{**_context_payload(), field: value})

    def test_policy_rejects_business_contract_fields(self) -> None:
        """Policy 拒绝 Context 字段，读取预算也不能隐藏正式合同正文。"""

        for field, value in _context_payload().items():
            with self.subTest(field=field), self.assertRaises(ValidationError):
                UnitGenerationPolicy(**{**_policy_payload(), field: value})
        with self.assertRaises(ValidationError):
            UnitGenerationPolicy(**{**_policy_payload(), "frozen_contract_read_limits": {"formal_contracts": {"body": "secret"}}})
        policy = UnitGenerationPolicy(**_policy_payload())
        with self.assertRaises(TypeError):
            policy.frozen_contract_read_limits["max_reads"] = 20

    def test_policy_defaults_and_invalid_budgets(self) -> None:
        """策略默认 Local=3、SDK retry=0、tokens=4096，拒绝非正预算与错误类型。"""

        policy = UnitGenerationPolicy(**_policy_payload())
        self.assertEqual((policy.local_max_attempts, policy.model_max_retries, policy.model_max_tokens), (3, 0, 4096))
        for change in (
            {"local_max_attempts": 4}, {"model_max_retries": 1}, {"model_max_retries": False},
            {"model_max_tokens": 0}, {"model_max_tokens": "4096"},
            {"request_timeout": 0}, {"request_timeout": float("inf")},
            {"unit_session_timeout": -1}, {"unit_session_timeout": float("nan")},
            {"model_turn_limit": True}, {"frozen_contract_read_limits": {"max_reads": -1}},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                UnitGenerationPolicy(**{**_policy_payload(), **change})

    def test_candidate_accepts_only_explicit_valid_invalid_superseded_status(self) -> None:
        """候选状态严格使用三种显式值，DTO 不执行状态推导或转移。"""

        for status in ("valid", "invalid", "superseded"):
            candidate = CandidateAttempt(**_candidate_payload(status))
            self.assertEqual(candidate.status, status)
            self.assertEqual(CandidateAttempt.model_validate_json(candidate.model_dump_json()).status, status)
        for status in ("pending", "confirmed", "failed", "", None):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                CandidateAttempt(**_candidate_payload(status))

    def test_platform_candidate_id_is_independent_of_model_task_ids(self) -> None:
        """相同模型任务产生不同平台 Candidate ID，序列化恢复时保持原 Candidate ID。"""

        payload = _candidate_payload()
        first = CandidateAttempt(**payload)
        second = CandidateAttempt(**payload)
        self.assertRegex(first.candidate_id, r"^candidate-[0-9a-f]{32}$")
        self.assertNotEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(first.tasks[0]["id"], "model-task-orders")
        self.assertEqual(first.model_dump(mode="json")["tasks"], payload["tasks"])
        self.assertEqual(first.model_copy().candidate_id, first.candidate_id)
        with self.assertRaises(ValidationError):
            CandidateAttempt(**{**payload, "candidate_id": "model-task-orders"})
        # 本任务不做 Task Validator；缺少 ID 的候选仍完整保留，不能自动补 Task ID。
        malformed = CandidateAttempt(**{**payload, "status": "invalid", "tasks": [{"description": "missing id"}]})
        self.assertNotIn("id", malformed.tasks[0])

    def test_attempt_result_does_not_auto_promote_raw_response_to_valid_candidate(self) -> None:
        """单次生成结果只保存响应，不获得 Candidate 身份或校验状态。"""

        result = UnitGenerationAttemptResult(**_result_payload())
        self.assertEqual(result.raw_response, _result_payload()["raw_response"])
        self.assertNotIn("status", result.model_dump())
        self.assertNotIn("candidate_id", result.model_dump())
        for field, value in (("status", "valid"), ("candidate_id", "candidate-" + "a" * 32)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                UnitGenerationAttemptResult(**{**_result_payload(), field: value})

    def test_candidate_and_result_reuse_structured_validation_issue_contract(self) -> None:
        """候选及单次结果序列化 T1.1 Issue，同时拒绝非法重试目标契约。"""

        issue = ValidationIssue(code="missing_id", level="unit", category="generation",
            unit_ids=["page:orders"], retry_unit_ids=["page:orders"], retryable=True, message="缺少任务 ID")
        for model, payload in ((CandidateAttempt, _candidate_payload("invalid")), (UnitGenerationAttemptResult, _result_payload())):
            with self.subTest(model=model.__name__):
                instance = model(**{**payload, "validation_issues": [issue]})
                self.assertEqual(instance.validation_issues[0], issue)
                self.assertEqual(instance.model_dump(mode="json")["validation_issues"], [issue.model_dump(mode="json")])
                with self.assertRaises(ValidationError):
                    model(**{**payload, "validation_issues": [{**issue.model_dump(), "retry_unit_ids": []}]})

    def test_validated_copy_cannot_bypass_context_policy_boundaries(self) -> None:
        """复制会重新校验，不能利用 Pydantic 默认 update 绕过 frozen 或字段边界。"""

        context = UnitGenerationContext(**_context_payload())
        copied = context.model_copy(deep=True)
        self.assertEqual(copied, context)
        self.assertIsNot(copied.formal_contracts, context.formal_contracts)
        with self.assertRaises(ValidationError):
            context.model_copy(update={"model_max_tokens": 4096})
        with self.assertRaises(ValidationError):
            UnitGenerationPolicy(**_policy_payload()).model_copy(update={"formal_contracts": {}})

    def test_context_accepts_explicit_empty_and_confirmed_baseline_identity(self) -> None:
        """没有 confirmed 基线时显式使用 None，有基线时保留调用方提供的摘要。"""

        for digest in (None, "confirmed-digest"):
            context = UnitGenerationContext(**{**_context_payload(), "base_confirmed_plan_digest": digest})
            self.assertEqual(context.base_confirmed_plan_digest, digest)
        for change in ({"unit_id": " "}, {"unit_kind": "unknown"}, {"input_fingerprint": ""}, {"base_confirmed_plan_digest": ""}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                UnitGenerationContext(**{**_context_payload(), **change})
