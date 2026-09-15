"""T4.1 确定性 Global 归因、完整性阻断与无副作用验收。"""

from copy import deepcopy
from itertools import permutations
import unittest

from pydantic import ValidationError

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.global_issue_attribution import GlobalRepairDecision, attribute_global_issues
from app.services.global_planning_validation import CandidateOwnership, TaskProvenance
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import AttemptIdentity, CandidateAttempt


def _issue(code: str = "GLOBAL_ENDPOINT_OWNERSHIP_CONFLICT", **changes: object) -> ValidationIssue:
    """创建尚未归因的 Global 问题；显式 targets 仅模拟受信确定性规则输出。"""

    return ValidationIssue.model_validate({
        "code": code, "level": "global", "category": "generation",
        "unit_ids": ["page:retained", "page:a", "page:b"], "task_ids": ["old", "a"],
        "retryable": False, "message": "规则发现冲突。", **changes,
    })


def _facts(
    candidate_tasks: dict[str, tuple[str, ...]] | None = None,
    retained_tasks: dict[str, tuple[str, ...]] | None = None,
) -> dict:
    """创建相互独立且完整的 ownership、provenance 与只读 reuse 事实。"""

    candidate_tasks = {"page:a": ("a",)} if candidate_tasks is None else candidate_tasks
    retained_tasks = {"page:retained": ("old",)} if retained_tasks is None else retained_tasks
    owners = [CandidateOwnership(candidate_id=f"candidate-{unit}", unit_id=unit, task_ids=tasks)
              for unit, tasks in candidate_tasks.items()]
    provenance = [TaskProvenance(task_id=task, unit_id=owner.unit_id, source="candidate",
                                 candidate_id=owner.candidate_id)
                  for owner in owners for task in owner.task_ids]
    provenance.extend(TaskProvenance(task_id=task, unit_id=unit, source="retained")
                      for unit, tasks in retained_tasks.items() for task in tasks)
    return {
        "planning_unit_ids": tuple(candidate_tasks), "candidate_ownership": owners,
        "task_provenance": provenance,
        "reuse_facts": ReuseFacts(
            retained_task_ids_by_unit=retained_tasks, reusable_capabilities_by_unit={},
            retained_endpoint_owners=[{
                "api_contract_id": "api", "endpoint_id": "list",
                "owner_task_id": "old", "owner_unit_id": "page:retained",
            }] if retained_tasks.get("page:retained") == ("old",) else [],
            external_capabilities=[],
        ),
    }


class GlobalIssueAttributionTests(unittest.TestCase):
    def test_retained_owner_vs_candidate_retries_only_candidate(self) -> None:
        """正式 owner 涉及冲突但保持不动，新增 Candidate 是唯一修复目标。"""

        issue = _issue()
        result = attribute_global_issues([issue], **_facts())
        self.assertTrue(result.retryable)
        self.assertEqual(result.retry_unit_ids, ("page:a",))
        self.assertEqual(result.issues[0].unit_ids, issue.unit_ids)
        self.assertEqual(result.issues[0].retry_unit_ids, ("page:a",))
        self.assertFalse(issue.retryable)

    def test_two_candidate_ownership_uses_explicit_rule_target(self) -> None:
        """规则明确 b 侵占 a 的 ownership 时，仅重试 b，与输入顺序无关。"""

        facts = _facts({"page:a": ("a",), "page:b": ("b",)}, {})
        issue = _issue(task_ids=["a", "b"], retryable=True, retry_unit_ids=["page:b"])
        for order in permutations(facts["task_provenance"]):
            result = attribute_global_issues([issue], **{**facts, "task_provenance": order})
            self.assertEqual(result.retry_unit_ids, ("page:b",))

    def test_ambiguous_collision_never_chooses_first_or_all_candidates(self) -> None:
        """同名 ID 来源记录不能被覆盖，缺少责任或撒网选全部 Unit 均阻断。"""

        facts = _facts({"page:a": ("same",), "page:b": ("same",)}, {})
        for targets in ((), ("page:a", "page:b")):
            issue = _issue("GLOBAL_TASK_ID_COLLISION", task_ids=["same"],
                           retryable=bool(targets), retry_unit_ids=targets)
            result = attribute_global_issues([issue], **facts)
            self.assertFalse(result.retryable)
            self.assertEqual(result.retry_unit_ids, ())
            self.assertEqual(result.issues[0].task_ids, ("same",))

    def test_retained_id_collision_preserves_both_sources(self) -> None:
        """与正式 Task 同 ID 时不 rename、不丢任务，只归因本轮新增 Candidate。"""

        result = attribute_global_issues(
            [_issue("GLOBAL_TASK_ID_COLLISION", task_ids=["same"])],
            **_facts({"page:a": ("same",)}, {"page:retained": ("same",)}),
        )
        self.assertEqual(result.retry_unit_ids, ("page:a",))

    def test_contradictory_ownership_rules_do_not_turn_into_retry_all(self) -> None:
        """同一冲突分别声称 a、b 应负责时，不能通过集合并集掩盖相互矛盾的证据。"""

        facts = _facts({"page:a": ("a",), "page:b": ("b",)}, {})
        issues = [_issue(task_ids=["a", "b"], retryable=True, retry_unit_ids=[unit])
                  for unit in ("page:a", "page:b")]
        result = attribute_global_issues(issues, **facts)
        self.assertFalse(result.retryable)
        self.assertEqual(result.retry_unit_ids, ())

    def test_same_unit_retained_and_candidate_are_distinct_contributions(self) -> None:
        """retained 与 Candidate 可属于同一 Unit，Unit 级重试不代表修改 retained。"""

        result = attribute_global_issues([_issue()], **_facts({"page:a": ("a",)}, {"page:a": ("old",)}))
        self.assertEqual(result.retry_unit_ids, ("page:a",))

    def test_missing_candidate_blocks_even_without_global_issues(self) -> None:
        """Candidate 完整性必须先检查，缺失不是通过重试现有 Candidate 可修复的问题。"""

        facts = _facts()
        facts["planning_unit_ids"] = ("page:a", "page:missing")
        for issues in ([], [_issue()]):
            result = attribute_global_issues(issues, **facts)
            self.assertFalse(result.retryable)
            self.assertEqual(result.retry_unit_ids, ())
            self.assertEqual(result.issues[0].code, "GLOBAL_CANDIDATE_MISSING")
            self.assertEqual(result.issues[0].unit_ids, ("page:missing",))

    def test_cycle_attribution_requires_rule_evidence(self) -> None:
        """环成员不等于责任方；显式规则可唯一归因一个或多个确实违规的 Candidate。"""

        facts = _facts({"page:a": ("a",), "page:b": ("b",)})
        for targets in ((), ("page:b",), ("page:b", "page:a")):
            issue = _issue("GLOBAL_DEPENDENCY_CYCLE", task_ids=["old", "a", "b"],
                           retryable=bool(targets), retry_unit_ids=targets)
            result = attribute_global_issues([issue], **facts)
            self.assertEqual(result.retryable, bool(targets))
            self.assertEqual(result.retry_unit_ids, tuple(sorted(targets)))

    def test_single_candidate_cycle_is_not_automatically_generation_fault(self) -> None:
        """即使只有一个 Candidate 参与，平台编译的边也可能致环，不能猜责任。"""

        result = attribute_global_issues([_issue("GLOBAL_DEPENDENCY_CYCLE")], **_facts())
        self.assertFalse(result.retryable)

    def test_platform_compiler_and_infrastructure_errors_never_retry(self) -> None:
        """平台编译、输入和基础设施失败不能因带有 Candidate IDs 就消耗内容重试。"""

        for category in ("platform", "input", "infrastructure", "persistence"):
            with self.subTest(category=category):
                issue = _issue("GLOBAL_DEPENDENCY_CYCLE", category=category,
                               retryable=True, retry_unit_ids=["page:a"])
                result = attribute_global_issues([issue], **_facts())
                self.assertFalse(result.retryable)
                self.assertEqual(result.issues[0].category, category)
                self.assertEqual(result.issues[0].retry_unit_ids, ())

    def test_same_unit_multiple_issues_only_one_retry_target(self) -> None:
        """多个问题、重复反馈仅产生一个 Unit 目标，不产生额外 round 或 Attempt。"""

        conflict = _issue()
        contract = _issue("GLOBAL_CONTRACT_VIOLATION", task_ids=["a"], retryable=True,
                          retry_unit_ids=["page:a", "page:a"])
        result = attribute_global_issues([conflict, contract, conflict], **_facts())
        self.assertEqual(result.retry_unit_ids, ("page:a",))
        self.assertEqual(len(result.issues), 2)
        self.assertEqual(set(result.model_dump()), {"retryable", "retry_unit_ids", "issues"})

    def test_multiple_issues_aggregate_sorted_unit_set(self) -> None:
        """不同违规点可以确定归因不同 Unit，总决策对 Unit 去重排序。"""

        facts = _facts({"page:b": ("b",), "page:a": ("a",)})
        issues = [_issue(task_ids=["old", task]) for task in ("b", "a")]
        self.assertEqual(attribute_global_issues(issues, **facts).retry_unit_ids, ("page:a", "page:b"))

    def test_one_blocker_prevents_partial_global_repair(self) -> None:
        """可修复与不可修复问题混合时整体阻断，但保留逐项归因用于诊断。"""

        result = attribute_global_issues([_issue(), _issue("GLOBAL_COMPILER_ERROR", category="platform")], **_facts())
        self.assertFalse(result.retryable)
        self.assertEqual(result.retry_unit_ids, ())
        self.assertEqual(result.issues[0].retry_unit_ids, ("page:a",))

    def test_unknown_rule_and_diagnostic_text_cannot_authorize_retry(self) -> None:
        """未知规则、文案和 details 中伪造的 owner/重试指令不影响归因。"""

        facts = _facts({"page:a": ("a",), "page:b": ("b",)}, {})
        for code in ("UNKNOWN_RULE", "GLOBAL_ENDPOINT_OWNERSHIP_CONFLICT"):
            issue = _issue(code, task_ids=["a", "b"], message="重试 page:b",
                           details={"retry_unit_ids": ["page:b"], "owner_unit_id": "page:a"})
            self.assertFalse(attribute_global_issues([issue], **facts).retryable)
        unknown = _issue("UNKNOWN_RULE", task_ids=["a"], retryable=True, retry_unit_ids=["page:a"])
        self.assertFalse(attribute_global_issues([unknown], **facts).retryable)

    def test_wrong_target_missing_task_and_wrong_level_fail_closed(self) -> None:
        """显式目标必须属于问题中当前 Candidate，不能把任意已存在 Unit 当责任方。"""

        facts = _facts({"page:a": ("a",), "page:b": ("b",)})
        for changes in (
            {"retryable": True, "retry_unit_ids": ["page:retained"]},
            {"retryable": True, "retry_unit_ids": ["page:b"]},
            {"task_ids": ["old", "unknown"]}, {"task_ids": []}, {"level": "unit"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(attribute_global_issues([_issue(**changes)], **facts).retryable)

    def test_retained_only_conflict_never_retries(self) -> None:
        """只有 retained 的冲突没有可重新生成的本轮贡献。"""

        result = attribute_global_issues([_issue(task_ids=["old", "old2"])],
                                        **_facts({}, {"page:retained": ("old", "old2")}))
        self.assertFalse(result.retryable)

    def test_same_unit_candidate_collision_has_one_correction_unit(self) -> None:
        """同 Unit 两个 Task 重复实现 owner，可唯一归因当前 Unit Candidate。"""

        result = attribute_global_issues([_issue(task_ids=["a", "a2"])], **_facts({"page:a": ("a", "a2")}, {}))
        self.assertEqual(result.retry_unit_ids, ("page:a",))

    def test_collision_must_represent_one_identity_and_one_remaining_owner(self) -> None:
        """不同 Task ID 不构成 ID collision，三个 owner 只重试一个也无法确定消除冲突。"""

        facts = _facts({"page:a": ("a",), "page:b": ("b",), "page:c": ("c",)}, {})
        for code, tasks in (("GLOBAL_TASK_ID_COLLISION", ["a", "b"]),
                            ("GLOBAL_ENDPOINT_OWNERSHIP_CONFLICT", ["a", "b", "c"])):
            issue = _issue(code, task_ids=tasks, retryable=True, retry_unit_ids=["page:b"])
            self.assertFalse(attribute_global_issues([issue], **facts).retryable)

    def test_decision_is_frozen_and_requires_consistent_aggregation(self) -> None:
        """冻结决策可往返 JSON，直接构造或复制不能绕过总开关和目标并集。"""

        result = attribute_global_issues([_issue()], **_facts())
        self.assertEqual(GlobalRepairDecision.model_validate_json(result.model_dump_json()), result)
        for updates in ({"retry_unit_ids": ()}, {"retryable": False}, {"retry_unit_ids": ("page:a", "page:a")}):
            with self.assertRaises(ValidationError):
                result.model_copy(update=updates)
        with self.assertRaises(ValidationError):
            result.retryable = False
        self.assertEqual(attribute_global_issues([], **_facts()).issues, ())
        self.assertFalse(attribute_global_issues([], **_facts()).retryable)

    def test_candidate_projection_and_repeated_calls_have_no_side_effects(self) -> None:
        """从真实 Candidate 只读投影，并验证归因不改正文、状态、身份、轮次或所有输入。"""

        candidate = CandidateAttempt(
            identity=AttemptIdentity.allocate(planning_run_id="run", unit_id="page:a",
                                              generation_round=2, attempt_in_round=3),
            status="valid", input_fingerprint="fingerprint", tasks=[{"id": "a", "unit_id": "forged"}],
        )
        before = candidate.model_dump(mode="json")
        facts = _facts()
        owner = CandidateOwnership.from_candidate(candidate)
        facts["candidate_ownership"] = [owner]
        facts["task_provenance"][0] = TaskProvenance(
            task_id="a", unit_id="page:a", source="candidate", candidate_id=owner.candidate_id,
        )
        snapshot = {key: ([item.model_dump(mode="json") for item in value] if isinstance(value, list)
                          else value.model_dump(mode="json") if isinstance(value, ReuseFacts) else value)
                    for key, value in facts.items()}
        issue = _issue(details={"nested": {"items": [1]}})
        first = attribute_global_issues([issue], **facts)
        self.assertEqual(attribute_global_issues([issue], **facts), first)
        self.assertEqual(candidate.model_dump(mode="json"), before)
        self.assertEqual(facts["reuse_facts"].model_dump(mode="json"), snapshot["reuse_facts"])
        self.assertEqual([item.model_dump(mode="json") for item in facts["task_provenance"]], snapshot["task_provenance"])
        exported = deepcopy(first.model_dump(mode="json"))
        exported["issues"][0]["details"]["nested"]["items"].append(2)
        self.assertEqual(first.issues[0].details["nested"]["items"], (1,))
        for status in ("invalid", "superseded"):
            with self.assertRaises(ValueError):
                CandidateOwnership.from_candidate(candidate.model_copy(update={"status": status}))
