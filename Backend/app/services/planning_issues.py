"""Planning 规则层的结构化问题契约及纯集合操作，不执行校验归因或重试。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


IssueLevel = Literal["pre_generation", "unit", "global", "system"]
IssueCategory = Literal["input", "generation", "platform", "infrastructure", "persistence"]
_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_IssueIdentity = tuple[
    str, IssueLevel, IssueCategory, frozenset[str], frozenset[str], frozenset[str], bool,
]


class ValidationIssue(BaseModel):
    """表示规则命中时产生的问题；message/details 仅供展示和诊断。

    unit_ids/task_ids 表示涉及对象，retry_unit_ids 表示规则明确指定的重试目标。
    两组 Unit 可以相同，也可以不同；本契约不推导目标或施加子集关系。
    使用 model_dump(mode="json") / model_dump_json() 序列化，model_validate_json()
    恢复时重新校验契约；不提供字符串错误到 Issue 的兼容解析。
    """

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    code: _Identifier
    level: IssueLevel
    category: IssueCategory
    unit_ids: list[_Identifier] = Field(default_factory=list)
    task_ids: list[_Identifier] = Field(default_factory=list)
    retry_unit_ids: list[_Identifier] = Field(default_factory=list)
    retryable: bool
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_retry_contract(self) -> ValidationIssue:
        """拒绝缺失重试目标或不可重试问题暗带目标，不自动修正调用方输入。"""

        if self.retryable and not self.retry_unit_ids:
            raise ValueError("retryable=True 必须提供非空 retry_unit_ids。")
        if not self.retryable and self.retry_unit_ids:
            raise ValueError("retryable=False 不得携带 retry_unit_ids。")
        return self


def assert_issue_invariants(issue: ValidationIssue) -> None:
    """校验完整 Issue 契约，包含构造后列表修改及未验证复制导致的非法状态。"""

    if not isinstance(issue, ValidationIssue):
        raise TypeError("必须提供 ValidationIssue；规则层应显式构造结构化问题。")
    # Pydantic 默认跳过现有实例；本模型显式开启重新验证，避免集合操作路由非法对象。
    ValidationIssue.model_validate(issue)


def _issue_identity(issue: ValidationIssue) -> _IssueIdentity:
    """以结构化事实和路由字段确定身份，忽略展示文本、诊断详情及 ID 顺序。"""

    return (
        issue.code, issue.level, issue.category,
        frozenset(issue.unit_ids), frozenset(issue.task_ids),
        frozenset(issue.retry_unit_ids), issue.retryable,
    )


def dedupe_issues(issues: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    """按结构化身份稳定去重，保留首条完整 Issue，不合并或覆盖 message/details。"""

    seen: set[_IssueIdentity] = set()
    result: list[ValidationIssue] = []
    for issue in issues:
        assert_issue_invariants(issue)
        identity = _issue_identity(issue)
        if identity not in seen:
            seen.add(identity)
            result.append(issue)
    return result


def group_issues_by_retry_unit(
    issues: Iterable[ValidationIssue],
) -> dict[str, list[ValidationIssue]]:
    """去重后仅按显式 retry_unit_ids 分组，多目标问题进入每个目标且每组只出现一次。"""

    groups: dict[str, list[ValidationIssue]] = {}
    for issue in dedupe_issues(issues):
        if not issue.retryable:
            continue
        # 同一条 Issue 可能重复列出目标；保留首次出现顺序且不修改原始字段。
        for unit_id in dict.fromkeys(issue.retry_unit_ids):
            groups.setdefault(unit_id, []).append(issue)
    return groups
