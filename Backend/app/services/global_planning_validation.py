"""Global 归因所需的冻结事实与完整性门禁；不组装或编译 DAG。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StringConstraints, model_validator

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.planning_frozen import FrozenPlanningModel, tuple_input
from app.services.planning_issues import ValidationIssue, dedupe_issues
from app.services.planning_run_contracts import UnitRunState
from app.services.unit_generation_contracts import CandidateAttempt


_Id = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Ids = Annotated[tuple[_Id, ...], BeforeValidator(tuple_input)]
_Issues = Annotated[tuple[ValidationIssue, ...], BeforeValidator(tuple_input)]


class CandidateCompletenessResult(FrozenPlanningModel):
    """generation round Barrier 后的 Candidate 齐全性结论。"""

    complete: bool
    ready_unit_ids: _Ids
    missing_unit_ids: _Ids
    issues: _Issues

    @model_validator(mode="after")
    def validate_completeness(self) -> CandidateCompletenessResult:
        """强制 ready/missing 分离，并让每个缺失 Unit 都有唯一可归因 Issue。"""

        ready = set(self.ready_unit_ids)
        missing = set(self.missing_unit_ids)
        if (
            len(ready) != len(self.ready_unit_ids)
            or len(missing) != len(self.missing_unit_ids)
            or ready & missing
        ):
            raise ValueError("Candidate completeness 的 ready/missing Unit 必须唯一且互斥。")
        if self.complete != (not missing) or bool(self.issues) != bool(missing):
            raise ValueError("complete、missing_unit_ids 与 issues 必须表达同一完整性结论。")
        issue_targets: list[str] = []
        for issue in self.issues:
            if (
                issue.code != "GLOBAL_CANDIDATE_MISSING"
                or issue.level != "global"
                or issue.category != "generation"
                or not issue.retryable
                or len(issue.retry_unit_ids) != 1
                or issue.unit_ids != issue.retry_unit_ids
            ):
                raise ValueError("Candidate 缺失 Issue 必须可归因到唯一 generation Unit。")
            issue_targets.extend(issue.retry_unit_ids)
        if sorted(issue_targets) != sorted(missing):
            raise ValueError("Candidate 缺失 Issues 必须精确覆盖 missing_unit_ids。")
        return self


def _missing_candidate_issue(unit: UnitRunState) -> ValidationIssue:
    """把 round_exhausted 转成后续 Global 决策可按 Unit 消费的问题。"""

    return ValidationIssue(
        code="GLOBAL_CANDIDATE_MISSING",
        level="global",
        category="generation",
        unit_ids=(unit.unit_id,),
        task_ids=(),
        retry_unit_ids=(unit.unit_id,),
        retryable=True,
        message=f"Unit {unit.unit_id} 本轮未产生有效 Candidate。",
        details={
            "generation_round": unit.generation_round,
            "generation_status": unit.generation_status,
        },
    )


def check_candidate_completeness(
    unit_states: Mapping[str, UnitRunState],
) -> CandidateCompletenessResult:
    """在 generation round Barrier 后检查全部生成 Unit 的 Candidate。

    reuse_only、prerequisite_only 和 structural_only 不要求 Candidate；model 与
    deterministic Unit 都必须到达 candidate_ready。调用方若传入尚未结束本轮的生成
    Unit，说明 Barrier 尚未满足，本函数拒绝把暂时等待误报成 Candidate 缺失。
    """

    if not isinstance(unit_states, Mapping):
        raise TypeError("Candidate completeness 必须接收 UnitRunState mapping。")
    units: list[UnitRunState] = []
    for key, value in unit_states.items():
        unit = UnitRunState.model_validate(value)
        if key != unit.unit_id:
            raise ValueError("UnitRunState mapping key 必须与 unit_id 一致。")
        units.append(unit)

    planned = [
        unit
        for unit in units
        if unit.generation_strategy in {"model", "deterministic"}
    ]
    waiting = sorted(
        unit.unit_id
        for unit in planned
        if unit.generation_status not in {"candidate_ready", "round_exhausted"}
    )
    if waiting:
        raise ValueError(
            "Generation round Barrier 尚未满足：" + ", ".join(waiting)
        )

    ready = tuple(sorted(
        unit.unit_id
        for unit in planned
        if unit.generation_status == "candidate_ready"
    ))
    missing_units = tuple(sorted(
        (unit for unit in planned if unit.generation_status == "round_exhausted"),
        key=lambda unit: unit.unit_id,
    ))
    missing = tuple(unit.unit_id for unit in missing_units)
    issues = tuple(_missing_candidate_issue(unit) for unit in missing_units)
    return CandidateCompletenessResult(
        complete=not missing,
        ready_unit_ids=ready,
        missing_unit_ids=missing,
        issues=issues,
    )


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
