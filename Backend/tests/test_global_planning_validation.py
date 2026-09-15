"""T4.1 Global 归因输入事实完整性与不可信路由阻断测试。"""

import unittest

from pydantic import ValidationError

from app.services.global_issue_attribution import attribute_global_issues
from app.services.global_planning_validation import CandidateOwnership, TaskProvenance
from tests.test_global_issue_attribution import _facts, _issue


class GlobalPlanningValidationTests(unittest.TestCase):
    def test_ownership_and_provenance_contracts_reject_malformed_values(self) -> None:
        """身份、来源和 Candidate Task 列表必须严格有效，不能修剪、转换或静默丢弃。"""

        for tasks in ([], ["a", "a"], [" "], [1]):
            with self.subTest(tasks=tasks), self.assertRaises(ValidationError):
                CandidateOwnership(candidate_id="candidate-a", unit_id="page:a", task_ids=tasks)
        for changes in ({"candidate_id": None}, {"source": "retained"}, {"source": "unknown"}, {"unit_id": " page:a"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                TaskProvenance(**{
                    "task_id": "a", "unit_id": "page:a", "source": "candidate",
                    "candidate_id": "candidate-a", **changes,
                })

    def test_missing_forged_duplicate_and_overwritten_provenance_block(self) -> None:
        """缺失、伪造、重复来源均失败；同名 Task 被字典覆盖也不能通过。"""

        facts = _facts({"page:a": ("same",)}, {"page:retained": ("same",)})
        candidate, retained = facts["task_provenance"]
        for records in (
            [candidate], [retained], [candidate, retained, retained],
            [candidate.model_copy(update={"candidate_id": "wrong"}), retained],
            [candidate.model_copy(update={"unit_id": "page:other"}), retained],
        ):
            with self.subTest(records=records):
                result = attribute_global_issues([], **{**facts, "task_provenance": records})
                self.assertFalse(result.retryable)
                self.assertEqual(result.issues[0].code, "GLOBAL_TASK_PROVENANCE_INVALID")

    def test_candidate_missing_extra_or_duplicated_is_not_selected_arbitrarily(self) -> None:
        """只接受本轮每 Unit 唯一 Candidate，不任选多份记录、不扩大 planning 范围。"""

        facts = _facts()
        candidate = facts["candidate_ownership"][0]
        for updates in (
            {"candidate_ownership": []},
            {"candidate_ownership": [candidate, candidate]},
            {"planning_unit_ids": ()},
            {"planning_unit_ids": ("page:a", "page:a")},
        ):
            result = attribute_global_issues([_issue()], **{**facts, **updates})
            self.assertFalse(result.retryable)
            self.assertEqual(result.retry_unit_ids, ())
            self.assertTrue(any(issue.category == "platform" for issue in result.issues))

    def test_malformed_input_becomes_structured_nonretryable_issue(self) -> None:
        """冻结事实契约错误返回平台问题，不泄漏异常对象或转为模型内容错误。"""

        facts = _facts()
        for updates in ({"planning_unit_ids": "page:a"}, {"reuse_facts": {}},
                        {"candidate_ownership": [{"unit_id": "page:a"}]}):
            result = attribute_global_issues([_issue()], **{**facts, **updates})
            self.assertEqual(result.issues[0].code, "GLOBAL_ATTRIBUTION_INPUT_INVALID")
            self.assertFalse(result.retryable)
            self.assertTrue(all(not issue.retry_unit_ids for issue in result.issues))

    def test_baseline_issue_and_retained_owner_inconsistency_block(self) -> None:
        """正式基线问题、Endpoint owner 不一致和重复 retained ID 都不能靠模型修复。"""

        facts = _facts()
        reuse = facts["reuse_facts"]
        owner = reuse.retained_endpoint_owners[0]
        for updates in (
            {"issues": [_issue("CONFIRMED_BASELINE_INVALID", category="platform")]},
            {"issues": [_issue(retryable=True, retry_unit_ids=["page:a"])]},
            {"retained_task_ids_by_unit": {"page:retained": ["old", "old"]}},
            {"retained_endpoint_owners": [owner.model_copy(update={"owner_task_id": "unknown"})]},
            {"retained_endpoint_owners": [owner, owner.model_copy(update={"owner_unit_id": "page:other"})]},
        ):
            result = attribute_global_issues([_issue()], **{**facts, "reuse_facts": reuse.model_copy(update=updates)})
            self.assertFalse(result.retryable)
            self.assertEqual(result.retry_unit_ids, ())
            self.assertTrue(all(not issue.retryable for issue in result.issues))

    def test_platform_task_provenance_cannot_be_candidate_retry_target(self) -> None:
        """平台产生的 Task 参与问题时不得把平台故障伪装成某个 Candidate 的责任。"""

        facts = _facts()
        facts["task_provenance"].append(TaskProvenance(task_id="compiled", unit_id="page:a", source="platform"))
        issue = _issue("GLOBAL_DEPENDENCY_CYCLE", task_ids=["compiled", "a"],
                       retryable=True, retry_unit_ids=["page:a"])
        self.assertFalse(attribute_global_issues([issue], **facts).retryable)
