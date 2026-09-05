"""Global 归因所需的冻结事实与完整性门禁；不组装或编译 DAG。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StringConstraints, model_validator

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.planning_frozen import FrozenPlanningModel, tuple_input
from app.services.planning_issues import ValidationIssue, dedupe_issues
from app.services.unit_generation_contracts import CandidateAttempt


_Id = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Ids = Annotated[tuple[_Id, ...], BeforeValidator(tuple_input)]


class TaskProvenance(FrozenPlanningModel):
    """记录平台掌握的 Task 来源；同名 Task 必须分别保留，不能用 ID 字典覆盖。"""

    task_id: _Id
    unit_id: _Id
    source: Literal["retained", "candidate", "platform"]
    candidate_id: _Id | None = None

    @model_validator(mode="after")
    def validate_candidate_identity(self) -> TaskProvenance:
        """只有 Candidate 来源必须携带 Candidate ID，其他来源不得冒充 Candidate。"""

        if (self.source == "candidate") != (self.candidate_id is not None):
            raise ValueError("candidate 来源必须且只能携带 candidate_id。")
        return self


class CandidateOwnership(FrozenPlanningModel):
    """当前有效 Candidate 的平台身份投影，不承载正文、生命周期或重试计数。

    调用方负责选取当前 Run 的有效 Candidate；归因层不做旧 Attempt 恢复或选择。
    """

    candidate_id: _Id
    unit_id: _Id
    task_ids: Annotated[_Ids, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_task_ids(self) -> CandidateOwnership:
        """拒绝单 Candidate 内重复 Task ID，不做 rename 或静默合并。"""

        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("有效 Candidate 的 task_ids 不得重复。")
        return self

    @classmethod
    def from_candidate(cls, candidate: CandidateAttempt) -> CandidateOwnership:
        """只读提取已有有效 Candidate 的身份，原始 Task.unit_id 不作为归属权威。"""

        candidate = CandidateAttempt.model_validate(candidate)
        if candidate.status != "valid" or candidate.validation_issues:
            raise ValueError("Global 归因只接受无校验问题的 valid Candidate。")
        return cls(
            candidate_id=candidate.candidate_id, unit_id=candidate.identity.unit_id,
            task_ids=tuple(task.get("id") for task in candidate.tasks),
        )


class GlobalPlanningFacts(FrozenPlanningModel):
    """将归因输入冻结为同一个当前规划快照；不读取文件或其他 Unit 的生成上下文。"""

    planning_unit_ids: _Ids
    candidate_ownership: Annotated[tuple[CandidateOwnership, ...], BeforeValidator(tuple_input)]
    task_provenance: Annotated[tuple[TaskProvenance, ...], BeforeValidator(tuple_input)]
    reuse_facts: ReuseFacts


def global_input_issue(
    code: str, message: str, *, unit_ids: tuple[str, ...] = (),
) -> ValidationIssue:
    """产生不会进入 Global 内容重试的结构化平台门禁问题。"""

    return ValidationIssue(
        code=code, level="global", category="platform", unit_ids=unit_ids,
        retryable=False, message=message,
    )


def validate_global_planning_inputs(facts: GlobalPlanningFacts) -> list[ValidationIssue]:
    """先检查 Candidate 齐全与来源一致性，失败时不得开始 Issue 归因。

    Task 来源必须覆盖全部 retained/Candidate 身份；平台任务只作诊断，永不重试。
    这里只校验元数据，不执行 Assembly、业务合同校验或依赖图编译。
    """

    facts = GlobalPlanningFacts.model_validate(facts)
    issues = [issue.model_copy(update={"retryable": False, "retry_unit_ids": ()})
              for issue in facts.reuse_facts.issues]
    if any(issue.retryable for issue in facts.reuse_facts.issues):
        issues.append(global_input_issue(
            "GLOBAL_REUSE_FACTS_INVALID", "ReuseFacts 前置问题不得携带内容重试目标。",
        ))
    planning = set(facts.planning_unit_ids)
    units = [item.unit_id for item in facts.candidate_ownership]
    candidates = [item.candidate_id for item in facts.candidate_ownership]
    if (len(planning) != len(facts.planning_unit_ids) or len(set(units)) != len(units)
            or len(set(candidates)) != len(candidates) or set(units) - planning):
        issues.append(global_input_issue(
            "GLOBAL_CANDIDATE_OWNERSHIP_INVALID",
            "planning Unit 必须唯一，且每个 Unit 只能有一个当前 Candidate，不能超出本轮范围。",
        ))
    missing = tuple(sorted(planning - set(units)))
    if missing:
        issues.append(global_input_issue(
            "GLOBAL_CANDIDATE_MISSING", "Candidate 尚未齐全，不能进行 Global 归因。",
            unit_ids=missing,
        ))

    retained_pairs = [
        (task_id, unit_id) for unit_id, task_ids in facts.reuse_facts.retained_task_ids_by_unit.items()
        for task_id in task_ids
    ]
    retained_ids = [task_id for task_id, _ in retained_pairs]
    if len(set(retained_ids)) != len(retained_ids):
        issues.append(global_input_issue(
            "GLOBAL_RETAINED_OWNERSHIP_INVALID", "retained Task ID 必须有唯一正式归属。",
        ))
    endpoint_owners: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for owner in facts.reuse_facts.retained_endpoint_owners:
        endpoint_owners.setdefault((owner.api_contract_id, owner.endpoint_id), set()).add(
            (owner.owner_task_id, owner.owner_unit_id),
        )
    if any(len(owners) != 1 or not owners <= set(retained_pairs) for owners in endpoint_owners.values()):
        issues.append(global_input_issue(
            "GLOBAL_RETAINED_OWNERSHIP_INVALID", "retained Endpoint owner 冲突或不属于正式 Task 集合。",
        ))

    # 以四元组比较来源，保留跨 Candidate/retained 的同名 Task，而非覆盖 ID。
    expected = {(task, unit, "retained", None) for task, unit in retained_pairs}
    expected.update(
        (task, owner.unit_id, "candidate", owner.candidate_id)
        for owner in facts.candidate_ownership for task in owner.task_ids
    )
    actual = [(item.task_id, item.unit_id, item.source, item.candidate_id)
              for item in facts.task_provenance]
    non_platform = {record for record in actual if record[2] != "platform"}
    if non_platform != expected or len(set(actual)) != len(actual):
        issues.append(global_input_issue(
            "GLOBAL_TASK_PROVENANCE_INVALID",
            "Task provenance 必须完整且精确匹配 retained/Candidate 身份，不得覆盖或重复来源。",
        ))
    return dedupe_issues(issues)
