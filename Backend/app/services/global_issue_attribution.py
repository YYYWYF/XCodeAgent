"""Global Issue 到 Unit 重试目标的确定性归因；不调用模型或执行重试。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import BeforeValidator, ValidationError, model_validator

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.global_planning_validation import (
    CandidateOwnership, GlobalPlanningFacts, TaskProvenance, global_input_issue,
    validate_global_planning_inputs,
)
from app.services.planning_frozen import FrozenPlanningModel, tuple_input
from app.services.planning_issues import ValidationIssue, dedupe_issues


# 仅这些规则的冲突参与者具有对称语义，可用 retained 优先和唯一 Candidate Unit 归因。
_COLLISION_CODES = frozenset({
    "GLOBAL_TASK_ID_COLLISION", "GLOBAL_ENDPOINT_OWNERSHIP_CONFLICT",
    "GLOBAL_AUTH_CAPABILITY_PROVIDER_CONFLICT",
})
# 此类问题必须由确定性校验规则显式填写 retry_unit_ids，不能从环成员等诊断反推责任。
_RULE_CODES = frozenset({
    "GLOBAL_DEPENDENCY_CYCLE", "GLOBAL_CROSS_UNIT_DEPENDENCY_INVALID",
    "GLOBAL_REQUIRED_UNIT_INCOMPLETE", "GLOBAL_CONTRACT_VIOLATION",
})


class GlobalRepairDecision(FrozenPlanningModel):
    """完整检查的聚合决策；无问题或任一问题不可修复时不启动 Global repair。

    被阻断时总目标为空，但 issues 保留逐项归因证据供展示；调用方不能只看子问题
    就执行局部修复。集合使用排序且去重的 tuple，可通过标准 model_dump 导出 JSON。
    """

    retryable: bool
    retry_unit_ids: Annotated[tuple[str, ...], BeforeValidator(tuple_input)]
    issues: Annotated[tuple[ValidationIssue, ...], BeforeValidator(tuple_input)]

    @model_validator(mode="after")
    def validate_aggregation(self) -> GlobalRepairDecision:
        """强制总开关及目标严格等于所有子问题的可修复性与 Unit 并集。"""

        retryable = bool(self.issues) and all(issue.retryable for issue in self.issues)
        targets = tuple(sorted({unit for issue in self.issues for unit in issue.retry_unit_ids})) if retryable else ()
        if self.retryable != retryable or self.retry_unit_ids != targets:
            raise ValueError("GlobalRepairDecision 必须按全部 issues 聚合，不能部分重试或重复目标。")
        return self


def _decision(issues: Sequence[ValidationIssue]) -> GlobalRepairDecision:
    """按完整 Issue 集合生成一次纯决策，不接收或消耗 Global round。"""

    unique = dedupe_issues(issues)
    retryable = bool(unique) and all(issue.retryable for issue in unique)
    targets = tuple(sorted({unit for issue in unique for unit in issue.retry_unit_ids})) if retryable else ()
    return GlobalRepairDecision(retryable=retryable, retry_unit_ids=targets, issues=unique)


def _reject_conflicting_attributions(issues: Sequence[ValidationIssue]) -> list[ValidationIssue]:
    """同一规则和 Task 集合若声称不同修复目标则阻断，不能用并集掩盖归因矛盾。"""

    targets_by_fact: dict[tuple[str, frozenset[str]], set[tuple[str, ...]]] = {}
    for issue in issues:
        key = (issue.code, frozenset(issue.task_ids))
        targets_by_fact.setdefault(key, set()).add(issue.retry_unit_ids)
    return [
        issue.model_copy(update={"retryable": False, "retry_unit_ids": ()})
        if len(targets_by_fact[(issue.code, frozenset(issue.task_ids))]) > 1 else issue
        for issue in issues
    ]


def _attribute_issue(
    issue: ValidationIssue, by_task: dict[str, list[TaskProvenance]],
) -> ValidationIssue:
    """按结构化规则、完整 Task 来源及平台 Candidate ownership 计算单问题责任。

    输入 Issue 的显式目标是受信确定性规则给出的责任声明，绝不是模型意见。
    message/details/unit_ids 都不作为重试证据；声明仍须通过当前 Candidate 来源核验。
    """

    targets: tuple[str, ...] = ()
    known = issue.code in _COLLISION_CODES | _RULE_CODES
    if (issue.level == "global" and issue.category == "generation" and known
            and issue.task_ids and all(task_id in by_task for task_id in issue.task_ids)):
        records = [record for task_id in set(issue.task_ids) for record in by_task[task_id]]
        candidate_units = {record.unit_id for record in records if record.source == "candidate"}
        retained = [record for record in records if record.source == "retained"]
        platform = any(record.source == "platform" for record in records)
        explicit = set(issue.retry_unit_ids)
        if not platform and candidate_units:
            if issue.code in _COLLISION_CODES:
                # 对称冲突至少两个来源。正式 owner 永远保留，多 retained 已冲突不能靠候选修复。
                identity_matches = issue.code != "GLOBAL_TASK_ID_COLLISION" or len(set(issue.task_ids)) == 1
                if identity_matches and len(records) >= 2 and len(retained) <= 1:
                    if retained or len(candidate_units) == 1:
                        targets = tuple(sorted(candidate_units))
                    elif explicit and explicit < candidate_units and len(candidate_units - explicit) == 1:
                        # 多 Candidate 的对称冲突至少保留一个规则指定的 owner，不能全量撒网重试。
                        targets = tuple(sorted(explicit))
            elif explicit and explicit <= candidate_units:
                targets = tuple(sorted(explicit))
            # 声明与确定性 ownership 结论冲突时阻断，不暗中修正错误规则的路由。
            if explicit and explicit != set(targets):
                targets = ()
    return issue.model_copy(update={"retryable": bool(targets), "retry_unit_ids": targets})


def attribute_global_issues(
    issues: Sequence[ValidationIssue], *, task_provenance: Sequence[TaskProvenance],
    reuse_facts: ReuseFacts, candidate_ownership: Sequence[CandidateOwnership],
    planning_unit_ids: Sequence[str],
) -> GlobalRepairDecision:
    """先验证快照完整性，再归因并聚合全部问题；失败封闭且不改变任何输入。

    新校验规则必须输出受信的结构化 Issue；不适配旧字符串错误或解析诊断 details。
    此接口为内部服务，不新增 HTTP/AG-UI 产品入口，也不接入旧规划重生成循环。
    """

    checked_issues = dedupe_issues(issues)
    try:
        facts = GlobalPlanningFacts(
            planning_unit_ids=planning_unit_ids, candidate_ownership=candidate_ownership,
            task_provenance=task_provenance, reuse_facts=reuse_facts,
        )
        blockers = validate_global_planning_inputs(facts)
    except (ValidationError, TypeError, ValueError):
        blockers = [global_input_issue(
            "GLOBAL_ATTRIBUTION_INPUT_INVALID", "Global 归因输入不满足冻结事实契约。",
        )]
    if blockers:
        # 输入无法验证时原有目标也失去可信身份，保留诊断并清空这些目标。
        return _decision([*blockers, *[
            issue.model_copy(update={"retryable": False, "retry_unit_ids": ()})
            for issue in checked_issues
        ]])
    by_task: dict[str, list[TaskProvenance]] = {}
    for record in facts.task_provenance:
        by_task.setdefault(record.task_id, []).append(record)
    attributed = [_attribute_issue(issue, by_task) for issue in checked_issues]
    return _decision(_reject_conflicting_attributions(attributed))
