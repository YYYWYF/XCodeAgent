"""把 PlanningRun 只读事实投影为安全、无预测值的 DAG 生成进度。"""

from __future__ import annotations

from typing import Literal, TypedDict

from app.domain.models import BuildUnitKind
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import (
    GenerationStatus,
    Participation,
    PlanningRunProjection,
    RunPhase,
    UnitRunState,
)
from app.services.unit_generation_requirements_contracts import GenerationStrategy


DAG_GENERATION_SNAPSHOT_SCHEMA_VERSION = "dag-generation.v1"
LOCAL_ATTEMPT_LIMIT = 3
_READY_UNIT_STATUSES = frozenset({"not_required", "candidate_ready"})
_ACTIVE_UNIT_STATUSES = frozenset({"generating", "validating"})


class DagGenerationIssue(TypedDict):
    """公开进度中的安全 Issue 摘要。"""

    code: str
    level: str
    category: str
    unitIds: list[str]
    taskIds: list[str]
    retryUnitIds: list[str]
    retryable: bool
    message: str


class DagGenerationUnit(TypedDict):
    """一个 Unit 的当前只读生成进度。"""

    id: str
    kind: BuildUnitKind
    participation: Participation
    generationStrategy: GenerationStrategy
    status: GenerationStatus
    generationRound: int
    attemptInRound: int
    localAttemptLimit: int
    totalAttempts: int
    retainedTaskCount: int
    reusableCapabilityCount: int
    candidateTaskCount: int
    issues: list[DagGenerationIssue]


class DagGenerationSummary(TypedDict):
    """仅由当前离散事实组成的 Run 汇总。"""

    unitCount: int
    readyUnitCount: int
    pendingUnitCount: int
    activeUnitCount: int
    roundExhaustedUnitCount: int
    abortedUnitCount: int
    retainedTaskCount: int
    candidateTaskCount: int
    unitIssueCount: int
    globalIssueCount: int
    failureIssueCount: int


class DagGenerationSnapshot(TypedDict):
    """PlanningRun 对外发布的完整 DAG 生成进度快照。"""

    schemaVersion: str
    planningRunId: str
    revision: int
    status: Literal["active", "failed", "cancelled"]
    phase: RunPhase
    globalRepairRound: int
    globalRepairLimit: int
    units: list[DagGenerationUnit]
    globalIssues: list[DagGenerationIssue]
    summary: DagGenerationSummary


def project_planning_run_progress(
    snapshot: PlanningRunProjection,
) -> DagGenerationSnapshot:
    """从冻结 PlanningRun 快照派生完整进度，不读取或修改 Candidate 正文。"""

    if not isinstance(snapshot, PlanningRunProjection):
        raise TypeError("PlanningRun progress projector 必须接收只读 PlanningRun snapshot。")

    units = [
        _project_unit(snapshot.unit_states[unit_id])
        for unit_id in snapshot.required_unit_ids
    ]
    global_issues = [_project_issue(issue) for issue in snapshot.global_issues]
    return {
        "schemaVersion": DAG_GENERATION_SNAPSHOT_SCHEMA_VERSION,
        "planningRunId": snapshot.planning_run_id,
        "revision": snapshot.revision,
        "status": snapshot.status,
        "phase": snapshot.phase,
        "globalRepairRound": snapshot.global_repair_round,
        "globalRepairLimit": snapshot.global_repair_limit,
        "units": units,
        "globalIssues": global_issues,
        "summary": _project_summary(units, global_issues, snapshot.failure),
    }


def _project_unit(unit: UnitRunState) -> DagGenerationUnit:
    """投影单个 Unit 的可展示事实，仅暴露 Candidate 数量而不暴露身份或正文。"""

    return {
        "id": unit.unit_id,
        "kind": unit.kind,
        "participation": unit.participation,
        "generationStrategy": unit.generation_strategy,
        "status": unit.generation_status,
        "generationRound": unit.generation_round,
        "attemptInRound": unit.attempt_in_round,
        "localAttemptLimit": (
            LOCAL_ATTEMPT_LIMIT if unit.generation_strategy == "model" else 0
        ),
        "totalAttempts": unit.total_attempts,
        "retainedTaskCount": len(unit.retained_task_ids),
        "reusableCapabilityCount": len(unit.reusable_capabilities),
        "candidateTaskCount": unit.candidate_task_count,
        "issues": [_project_issue(issue) for issue in unit.current_issues],
    }


def _project_issue(issue: ValidationIssue) -> DagGenerationIssue:
    """投影结构化路由与展示信息，省略可能携带内部上下文的 details。"""

    return {
        "code": issue.code,
        "level": issue.level,
        "category": issue.category,
        "unitIds": list(issue.unit_ids),
        "taskIds": list(issue.task_ids),
        "retryUnitIds": list(issue.retry_unit_ids),
        "retryable": issue.retryable,
        "message": issue.message,
    }


def _project_summary(
    units: list[DagGenerationUnit],
    global_issues: list[DagGenerationIssue],
    failure: ValidationIssue | None,
) -> DagGenerationSummary:
    """按真实离散状态汇总数量，不生成百分比或推测完成度。"""

    statuses = [str(unit["status"]) for unit in units]
    return {
        "unitCount": len(units),
        "readyUnitCount": sum(status in _READY_UNIT_STATUSES for status in statuses),
        "pendingUnitCount": statuses.count("pending"),
        "activeUnitCount": sum(status in _ACTIVE_UNIT_STATUSES for status in statuses),
        "roundExhaustedUnitCount": statuses.count("round_exhausted"),
        "abortedUnitCount": statuses.count("aborted"),
        "retainedTaskCount": sum(int(unit["retainedTaskCount"]) for unit in units),
        "candidateTaskCount": sum(int(unit["candidateTaskCount"]) for unit in units),
        "unitIssueCount": sum(len(unit["issues"]) for unit in units),
        "globalIssueCount": len(global_issues),
        "failureIssueCount": int(failure is not None),
    }
