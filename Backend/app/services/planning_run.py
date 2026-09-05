"""T5.1 纯状态转换：validate → 新快照 → revision+1，无 IO 或执行编排。

调用方提供平台身份、验证结论和时间，并保留返回快照作为唯一当前状态。
非法转换抛出 IllegalPlanningTransition，原状态保持不变。
"""

from collections.abc import Sequence

from app.services.planning_issues import ValidationIssue, dedupe_issues, group_issues_by_retry_unit
from app.services.planning_run_contracts import PlanningRun, UnitRoundHistory, UnitRunState
from app.services.unit_generation_contracts import AttemptIdentity, CandidateAttempt


class IllegalPlanningTransition(ValueError):
    """拒绝阶段、身份、预算或终态不允许的状态转换。"""


def _require(condition: bool, message: str) -> None:
    """在构造新状态前集中拒绝非法转换，避免部分应用。"""

    if not condition:
        raise IllegalPlanningTransition(message)


def _active(run: PlanningRun, *phases: str) -> None:
    """检查 Run 仍活动且处于允许的阶段。"""

    _require(run.status == "active", "PlanningRun 已终止，不能接受新转换或晚到结果。")
    _require(not phases or run.phase in phases, f"当前 phase={run.phase} 不允许此转换。")


def _unit(run: PlanningRun, unit_id: str) -> UnitRunState:
    """只读取当前范围内已注册的 Unit。"""

    _require(unit_id in run.unit_states, "Unit 不属于当前 PlanningRun。")
    return run.unit_states[unit_id]


def _apply(run: PlanningRun, *, at: str, unit: UnitRunState | None = None, **changes) -> PlanningRun:
    """一次性构造完整新快照；每个成功转换只增加一次 revision。"""

    if unit is not None:
        changes["unit_states"] = {**run.unit_states, unit.unit_id: unit}
    return run.model_copy(update={**changes, "revision": run.revision + 1, "updated_at": at})


def begin_generation(run: PlanningRun, *, at: str) -> PlanningRun:
    """从准备阶段显式进入生成阶段，包括无需生成 Candidate 的空集合。"""

    _active(run, "preparing")
    _require(all(unit.generation_status in {"pending", "not_required"} for unit in run.unit_states.values()), "准备阶段 Unit 状态非法。")
    return _apply(run, at=at, phase="generating_units")


def mark_unit_generating(run: PlanningRun, identity: AttemptIdentity, *, at: str) -> PlanningRun:
    """登记一次已分配的生成身份；只有模型策略递增 Local/累计预算。"""

    _active(run, "generating_units")
    identity = AttemptIdentity.model_validate(identity)
    unit = _unit(run, identity.unit_id)
    _require(unit.generation_status == "pending", "只有 pending Unit 可以开始生成。")
    is_model = unit.generation_strategy == "model"
    expected_attempt = unit.attempt_in_round + 1 if is_model else 1
    _require(not is_model or expected_attempt <= 3, "Local=3 已耗尽，必须 mark_round_exhausted。")
    _require((identity.planning_run_id, identity.generation_round, identity.attempt_in_round) == (
        run.planning_run_id, unit.generation_round, expected_attempt,
    ), "Attempt 身份与当前 Run/round/attempt 不一致。")
    used_ids = {item.identity.attempt_id for item in run.candidates.values()}
    used_ids.update(item.expected_identity.attempt_id for item in run.unit_states.values() if item.expected_identity)
    _require(identity.attempt_id not in used_ids, "不能复用已经分配的 attempt_id。")
    unit = unit.model_copy(update={
        "generation_status": "generating", "expected_identity": identity,
        "attempt_in_round": expected_attempt if is_model else 0,
        "total_attempts": unit.total_attempts + int(is_model),
    })
    return _apply(run, at=at, unit=unit)


def _expected(run: PlanningRun, identity: AttemptIdentity, *statuses: str) -> UnitRunState:
    """按完整身份拒绝旧 attempt、旧 round、跨 Run 和被 supersede 的结果。"""

    _active(run, "generating_units")
    unit = _unit(run, identity.unit_id)
    _require(unit.generation_status in statuses and unit.expected_identity == identity, "结果不是当前预期的 Attempt。")
    return unit


def mark_unit_validating(run: PlanningRun, identity: AttemptIdentity, *, at: str) -> PlanningRun:
    """将当前在途生成切换到本地校验，保留原 Attempt 身份。"""

    unit = _expected(run, identity, "generating")
    return _apply(run, at=at, unit=unit.model_copy(update={"generation_status": "validating"}))


def _record_candidate(run: PlanningRun, candidate: CandidateAttempt, *, valid: bool, at: str) -> PlanningRun:
    """在身份和结论一致后记录原始 Candidate，绝不修补任务正文。"""

    candidate = CandidateAttempt.model_validate(candidate)
    unit = _expected(run, candidate.identity, *(('validating',) if valid else ('generating', 'validating')))
    _require(candidate.input_fingerprint == run.input_fingerprint, "Candidate 输入指纹与 Run 不一致。")
    _require(candidate.candidate_id not in run.candidates, "Candidate ID 已存在，不能覆盖或恢复 superseded Candidate。")
    _require(candidate.status == ("valid" if valid else "invalid"), "Candidate status 与转换不匹配。")
    if valid:
        _require(bool(candidate.tasks) and not candidate.validation_issues, "ready Candidate 必须非空且无校验问题。")
    else:
        _require(unit.generation_strategy == "model", "deterministic 校验失败应 fail Run，不能进入模型内容重试。")
        _require(bool(candidate.validation_issues) and all(
            issue.level == "unit" and issue.category == "generation" and issue.retryable
            and set(issue.retry_unit_ids) == {unit.unit_id} for issue in candidate.validation_issues
        ), "Local invalid 只接受当前 Unit 的可重试 generation Issue；其他失败应 fail Run。")
    unit = unit.model_copy(update={
        "generation_status": "candidate_ready" if valid else "pending",
        "expected_identity": None, "latest_candidate_id": candidate.candidate_id if valid else None,
        "candidate_task_count": len(candidate.tasks) if valid else 0,
        "current_issues": candidate.validation_issues,
    })
    return _apply(run, at=at, unit=unit, candidates={**run.candidates, candidate.candidate_id: candidate})


def record_candidate_invalid(run: PlanningRun, candidate: CandidateAttempt, *, at: str) -> PlanningRun:
    """保留本次无效 Candidate 及 Issue，允许后续显式重试或标记轮次耗尽。"""

    return _record_candidate(run, candidate, valid=False, at=at)


def record_candidate_ready(run: PlanningRun, candidate: CandidateAttempt, *, at: str) -> PlanningRun:
    """仅在本地校验阶段接纳当前有效 Candidate。"""

    return _record_candidate(run, candidate, valid=True, at=at)


def mark_round_exhausted(run: PlanningRun, unit_id: str, *, at: str) -> PlanningRun:
    """三次无效模型 Candidate 后关闭本轮，保持 Run active，Unit 没有 failed 状态。"""

    _active(run, "generating_units")
    unit = _unit(run, unit_id)
    _require(unit.generation_status == "pending" and unit.attempt_in_round == 3 and bool(unit.current_issues), "只有三次内容失败后的 pending Unit 可以耗尽。")
    return _apply(run, at=at, unit=unit.model_copy(update={"generation_status": "round_exhausted"}))


def begin_global_check(run: PlanningRun, *, at: str) -> PlanningRun:
    """所有生成 Unit 到达 ready/exhausted 后进入完整性检查，不消耗 Global 额度。"""

    _active(run, "generating_units")
    _require(all(run.unit_states[key].generation_status in {"candidate_ready", "round_exhausted"} for key in run.planning_unit_ids), "生成轮次尚未完成。")
    return _apply(run, at=at, phase="global_check")


def begin_global_repair(run: PlanningRun, issues: Sequence[ValidationIssue], *, at: str) -> PlanningRun:
    """消费一次 Global 额度并原子重开所有显式归因目标，旧 valid Candidate 永久 supersede。"""

    _active(run, "global_check", "assembling", "validating")
    issues = tuple(dedupe_issues(issues))
    _require(bool(issues) and all(issue.level == "global" and issue.category == "generation" and issue.retryable for issue in issues), "必须提交全部可重试的 Global generation Issues。")
    _require(run.global_repair_round < run.global_repair_limit, "Global=2 已耗尽，应终止 Run。")
    targets = group_issues_by_retry_unit(issues)
    for key in targets:
        _require(_unit(run, key).generation_status in {"candidate_ready", "round_exhausted"}, "Global 只能重开已结束本轮的生成 Unit。")
    # 先验证全部目标，再在局部副本同时修改；任一错误不能部分 supersede 或消耗额度。
    units, candidates = dict(run.unit_states), dict(run.candidates)
    for key, feedback in targets.items():
        unit = units[key]
        if unit.latest_candidate_id:
            candidate = candidates[unit.latest_candidate_id]
            candidates[candidate.candidate_id] = candidate.model_copy(update={"status": "superseded"})
        history = UnitRoundHistory(
            generation_round=unit.generation_round, attempt_in_round=unit.attempt_in_round,
            generation_status=unit.generation_status, candidate_id=unit.latest_candidate_id, issues=unit.current_issues,
        )
        units[key] = unit.model_copy(update={
            "generation_status": "pending", "generation_round": unit.generation_round + 1,
            "attempt_in_round": 0, "latest_candidate_id": None, "candidate_task_count": 0,
            "current_issues": tuple(feedback), "round_history": (*unit.round_history, history),
        })
    return _apply(run, at=at, unit_states=units, candidates=candidates, global_issues=issues,
                  global_repair_round=run.global_repair_round + 1, phase="generating_units")


def begin_assembly(run: PlanningRun, *, at: str) -> PlanningRun:
    """只有 Candidate 齐全才允许进入 Assembly；不实际组装 DAG。"""

    _active(run, "global_check")
    _require(all(run.unit_states[key].generation_status == "candidate_ready" for key in run.planning_unit_ids), "Candidate 不齐全，不能组装。")
    return _apply(run, at=at, phase="assembling")


def begin_validation(run: PlanningRun, *, at: str) -> PlanningRun:
    """调用方完成 Assembly 后标记进入全局校验，不执行 Validator。"""

    _active(run, "assembling")
    return _apply(run, at=at, phase="validating")


def begin_pending_persistence(run: PlanningRun, *, at: str) -> PlanningRun:
    """调用方确认 Global 成功后进入写 Pending 的阶段标记，本函数不写文件。"""

    _active(run, "validating")
    return _apply(run, at=at, phase="persisting_pending", global_issues=())


def _terminate(run: PlanningRun, *, status: str, failure: ValidationIssue | None, at: str) -> PlanningRun:
    """终止 Run 并撤销所有未完成 Unit 身份；保留已完成 Candidate 和历史诊断。"""

    _active(run)
    units = {
        key: unit.model_copy(update={"generation_status": "aborted", "expected_identity": None})
        if unit.generation_status in {"pending", "generating", "validating"} else unit
        for key, unit in run.unit_states.items()
    }
    return _apply(run, at=at, status=status, failure=failure, unit_states=units)


def fail(run: PlanningRun, issue: ValidationIssue, *, at: str) -> PlanningRun:
    """记录致命失败并关闭 Run；基础设施失败不会触发或额外消耗内容重试。"""

    issue = ValidationIssue.model_validate(issue)
    _require(not issue.retryable, "致命 failure 必须是明确不可重试的 Issue。")
    return _terminate(run, status="failed", failure=issue, at=at)


def cancel(run: PlanningRun, *, at: str) -> PlanningRun:
    """只取消 active Run；重复取消和取消后的任何结果均被拒绝。"""

    return _terminate(run, status="cancelled", failure=None, at=at)
