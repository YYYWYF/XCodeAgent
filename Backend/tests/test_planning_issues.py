"""T1.1 ValidationIssue 序列化、身份、路由与非法契约回归。"""

import json
import unittest

from pydantic import ValidationError

from app.services.planning_issues import (
    ValidationIssue, assert_issue_invariants, dedupe_issues, group_issues_by_retry_unit,
)


def _payload(**changes: object) -> dict:
    """构造涉及历史 Unit、但只重试当前 Unit 的合法问题。"""

    return {
        "code": "duplicate_task_id", "level": "global", "category": "generation",
        "unit_ids": ["page:retained", "page:current"], "task_ids": ["task:conflict"],
        "retry_unit_ids": ["page:current"], "retryable": True,
        "message": "当前候选与历史任务 ID 冲突。",
        "details": {"path": "frontend/src/pages/Current/index.tsx", "evidence": [1, True, None]},
        **changes,
    }


class PlanningIssueTests(unittest.TestCase):
    def test_issue_serialization_round_trips_all_contract_fields(self) -> None:
        """JSON 往返保留九个合同字段、中文信息及嵌套诊断数据。"""

        payload = _payload()
        issue = ValidationIssue.model_validate(payload)
        self.assertEqual(issue.model_dump(mode="json"), payload)
        serialized = issue.model_dump_json()
        self.assertEqual(json.loads(serialized), payload)
        self.assertEqual(ValidationIssue.model_validate_json(serialized), issue)
        self.assertIsNone(assert_issue_invariants(issue))

    def test_level_and_category_use_declared_design_values(self) -> None:
        """仅接受设计定义的层级和类别，不让展示消息代替机器分类。"""

        for level in ("pre_generation", "unit", "global", "system"):
            for category in ("input", "generation", "platform", "infrastructure", "persistence"):
                with self.subTest(level=level, category=category):
                    issue = ValidationIssue.model_validate(_payload(
                        level=level, category=category, retryable=False, retry_unit_ids=[],
                    ))
                    self.assertEqual((issue.level, issue.category), (level, category))

    def test_invalid_retryable_contract_is_rejected_on_construction_and_deserialization(self) -> None:
        """可重试无目标、不可重试带目标、非布尔标记及空白目标全部拒绝。"""

        for changes in (
            {"retryable": True, "retry_unit_ids": []},
            {"retryable": False, "retry_unit_ids": ["page:current"]},
            {"retryable": "false"}, {"retryable": 1},
            {"retry_unit_ids": [""]}, {"retry_unit_ids": ["  "]},
            {"retry_unit_ids": "page:current"}, {"retry_unit_ids": [None]},
        ):
            with self.subTest(changes=changes):
                payload = _payload(**changes)
                with self.assertRaises(ValidationError):
                    ValidationIssue.model_validate(payload)
                with self.assertRaises(ValidationError):
                    ValidationIssue.model_validate_json(json.dumps(payload))

    def test_malformed_fields_and_unknown_contract_fields_are_rejected(self) -> None:
        """拒绝非法分类、空身份、非 JSON 详情和未知字段，避免静默丢弃规则事实。"""

        for changes in (
            {"code": " "}, {"level": "warning"}, {"category": "unknown"},
            {"unit_ids": [7]}, {"task_ids": [""]}, {"message": 7},
            {"details": {"object": object()}}, {"unexpected": "value"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ValidationIssue.model_validate(_payload(**changes))
        for missing_field in ("code", "level", "category", "retryable", "message"):
            with self.subTest(missing_field=missing_field), self.assertRaises(ValidationError):
                payload = _payload()
                payload.pop(missing_field)
                ValidationIssue.model_validate(payload)

    def test_dedupe_preserves_first_issue_and_input_order(self) -> None:
        """去重稳定保留首次出现的完整问题，不合并后续诊断也不修改输入。"""

        first = ValidationIssue.model_validate(_payload())
        second = ValidationIssue.model_validate(_payload(code="missing_dependency"))
        duplicate = ValidationIssue.model_validate(_payload(message="另一描述", details={"later": True}))
        issues = [first, second, duplicate, second]
        before = [issue.model_dump(mode="json") for issue in issues]
        result = dedupe_issues(iter(issues))
        self.assertEqual(result, [first, second])
        self.assertIs(result[0], first)
        self.assertEqual([issue.model_dump(mode="json") for issue in issues], before)

    def test_details_do_not_affect_issue_identity(self) -> None:
        """不同诊断路径和嵌套详情不产生新身份。"""

        first = ValidationIssue.model_validate(_payload(details={"nested": {"items": [1, 2]}}))
        second = ValidationIssue.model_validate(_payload(details={"different": [False, None, {"path": "other"}]}))
        self.assertEqual(dedupe_issues([first, second]), [first])

    def test_id_order_and_repetitions_do_not_affect_identity(self) -> None:
        """三组 ID 按集合语义参与身份，不因顺序或重复项重复报告问题。"""

        first = ValidationIssue.model_validate(_payload(
            task_ids=["task:a", "task:b"], retry_unit_ids=["page:a", "page:b"],
        ))
        second = ValidationIssue.model_validate(_payload(
            unit_ids=["page:current", "page:retained", "page:current"],
            task_ids=["task:b", "task:a", "task:a"], retry_unit_ids=["page:b", "page:a", "page:b"],
        ))
        self.assertEqual(dedupe_issues([first, second]), [first])

    def test_structural_identity_keeps_distinct_issues_and_retry_routes(self) -> None:
        """每个机器字段变化都保留独立问题，尤其不能合并不同重试目标。"""

        first = ValidationIssue.model_validate(_payload())
        for changes in (
            {"code": "other_rule"}, {"level": "unit"}, {"category": "platform"},
            {"unit_ids": ["page:other"]}, {"task_ids": ["task:other"]},
            {"retry_unit_ids": ["page:other"]}, {"retryable": False, "retry_unit_ids": []},
        ):
            with self.subTest(changes=changes):
                second = ValidationIssue.model_validate(_payload(**changes))
                self.assertEqual(dedupe_issues([first, second]), [first, second])

    def test_retry_grouping_uses_only_explicit_targets_and_deduplicates_each_group(self) -> None:
        """多目标问题进入对应组，涉及的历史 Unit 不成为重试目标，不可重试项不分组。"""

        first = ValidationIssue.model_validate(_payload(retry_unit_ids=["page:b", "page:a", "page:b"]))
        second = ValidationIssue.model_validate(_payload(code="missing_capability", retry_unit_ids=["page:a"]))
        blocked = ValidationIssue.model_validate(_payload(retryable=False, retry_unit_ids=[]))
        self.assertEqual(group_issues_by_retry_unit(iter([first, second, first, blocked])), {
            "page:b": [first], "page:a": [first, second],
        })
        self.assertEqual(list(group_issues_by_retry_unit([first])), ["page:b", "page:a"])

    def test_unit_ids_and_retry_unit_ids_are_independent(self) -> None:
        """涉及对象与重试对象可相同、相交或分离，不增加未规定的子集约束。"""

        for involved in ([], ["page:current"], ["page:retained"], ["page:retained", "page:current"]):
            with self.subTest(unit_ids=involved):
                issue = ValidationIssue.model_validate(_payload(unit_ids=involved))
                self.assertEqual(group_issues_by_retry_unit([issue]), {"page:current": [issue]})
                self.assertEqual(issue.unit_ids, tuple(involved))

    def test_message_changes_do_not_change_identity_or_retry_routing(self) -> None:
        """消息含重试指令、否定词、其他 Unit ID 或任意语言均不改变路由。"""

        first = ValidationIssue.model_validate(_payload())
        for message in ("禁止重试", "retry page:retained only", "infrastructure fatal", "", "请重试当前单元"):
            with self.subTest(message=message):
                issue = ValidationIssue.model_validate(_payload(message=message))
                self.assertEqual(dedupe_issues([first, issue]), [first])
                self.assertEqual(group_issues_by_retry_unit([issue]), {"page:current": [issue]})
                blocked = ValidationIssue.model_validate(_payload(message=message, retryable=False, retry_unit_ids=[]))
                self.assertEqual(group_issues_by_retry_unit([blocked]), {})

    def test_helpers_revalidate_unvalidated_issue_instances(self) -> None:
        """即使绕过构造校验，集合操作仍拒绝非法字段和重试目标。"""

        for change in (
            {"retry_unit_ids": ()}, {"retryable": False},
            {"retry_unit_ids": (" ",)}, {"retryable": "false"},
        ):
            issue = ValidationIssue.model_construct(**{**ValidationIssue.model_validate(_payload()).__dict__, **change})
            with self.assertRaises(ValidationError):
                assert_issue_invariants(issue)
            for operation in (dedupe_issues, group_issues_by_retry_unit):
                with self.assertRaises(ValidationError):
                    operation([issue])
        with self.assertRaises(TypeError):
            assert_issue_invariants("retry page:current")
        with self.assertRaises(ValidationError):
            ValidationIssue.model_validate(_payload()).model_copy(update={"retryable": False})

    def test_issue_is_deeply_frozen_and_json_projections_are_detached(self) -> None:
        """Issue 字段、目标与嵌套详情均只读，输入和导出修改不影响快照。"""

        payload = _payload()
        issue = ValidationIssue.model_validate(payload)
        before = issue.model_dump(mode="json")
        with self.assertRaises(ValidationError):
            issue.message = "changed"
        for ids in (issue.unit_ids, issue.task_ids, issue.retry_unit_ids):
            with self.assertRaises(AttributeError):
                ids.append("page:other")
        with self.assertRaises(TypeError):
            issue.details["path"] = "changed"
        with self.assertRaises(AttributeError):
            issue.details["evidence"].append("changed")
        payload["retry_unit_ids"].clear()
        payload["details"]["evidence"].clear()
        exported = issue.model_dump(mode="json")
        exported["retry_unit_ids"].clear()
        exported["details"]["evidence"].clear()
        self.assertEqual(issue.model_dump(mode="json"), before)
        self.assertEqual(issue.model_copy(deep=True), issue)

    def test_empty_collections_and_defaults_do_not_infer_targets(self) -> None:
        """空输入与无目标问题返回空分组；默认序列和详情也是只读对象。"""

        self.assertEqual(dedupe_issues(iter([])), [])
        self.assertEqual(group_issues_by_retry_unit(iter([])), {})
        issue = ValidationIssue(code="input_missing", level="pre_generation", category="input", retryable=False, message="输入缺失")
        self.assertEqual(issue.unit_ids, ())
        self.assertEqual(issue.details, {})
        with self.assertRaises(TypeError):
            issue.details["path"] = "changed"
        self.assertEqual(group_issues_by_retry_unit([issue]), {})
