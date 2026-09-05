"""T5.1 测试夹具：使用显式固定时间和可辨认身份推进真实转换。"""

from itertools import count

from app.services import planning_run as sm
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import AttemptIdentity, CandidateAttempt


AT = "2026-09-05T10:00:00Z"
UNIT = "page:orders"
_ids = count(1)


def unit(unit_id=UNIT, strategy="model", retained=()) -> sm.UnitRunState:
    """创建已完成生成策略判定的 Unit 初态。"""

    return sm.UnitRunState.create(
        unit_id=unit_id, kind="application" if unit_id in {"application:root", "app:integration"} else (
            "page" if unit_id.startswith("page:") else "frontend"
        ),
        generation_strategy=strategy, retained_task_ids=retained,
    )


def run(*units) -> sm.PlanningRun:
    """创建冻结 Run；调用方可显式传入复用、结构或 deterministic Unit。"""

    units = units or (unit(),)
    return sm.PlanningRun(
        planning_run_id="run-1", workflow_run_id="workflow-1", thread_id="thread-1",
        build_execution_scope={"type": "page", "targetId": "orders"},
        input_fingerprint="frozen-input", base_confirmed_plan_digest="confirmed-digest",
        required_unit_ids=tuple(item.unit_id for item in units),
        planning_unit_ids=tuple(item.unit_id for item in units if item.generation_status == "pending"),
        unit_states={item.unit_id: item for item in units}, started_at=AT, updated_at=AT,
    )


def identity(state, unit_id=UNIT) -> AttemptIdentity:
    """在测试调用边界分配独立身份；deterministic 身份序号不作为模型预算。"""

    current = state.unit_states[unit_id]
    return AttemptIdentity(
        planning_run_id=state.planning_run_id, unit_id=unit_id,
        generation_round=current.generation_round,
        attempt_in_round=current.attempt_in_round + 1 if current.generation_strategy == "model" else 1,
        attempt_id=f"attempt-{next(_ids):032x}",
    )


def issue(*targets, level="unit", category="generation", retryable=True) -> ValidationIssue:
    """创建带显式重试目标的结构化测试问题，文本不承担路由职责。"""

    return ValidationIssue(
        code="TEST_CONTENT_INVALID" if retryable else "TEST_FATAL",
        level=level, category=category, unit_ids=("page:unrelated",),
        retry_unit_ids=(targets or (UNIT,)) if retryable else (), retryable=retryable,
        message="测试校验问题", details={"nested": [{"source": "fixture"}]},
    )


def candidate(state, *, valid=True, unit_id=UNIT, attempt=None) -> CandidateAttempt:
    """用平台预期身份包装候选，不自动修改任务 ID 或归属。"""

    attempt = attempt or state.unit_states[unit_id].expected_identity
    return CandidateAttempt(
        candidate_id=f"candidate-{next(_ids):032x}", identity=attempt,
        input_fingerprint=state.input_fingerprint, status="valid" if valid else "invalid",
        tasks=({"id": f"task:{unit_id}", "unit_id": unit_id, "details": {"items": [1]}},),
        validation_issues=() if valid else (issue(unit_id),),
    )


def start(state, unit_id=UNIT):
    """通过公开转换进入一次生成，返回新状态及本次身份。"""

    attempt = identity(state, unit_id)
    return sm.mark_unit_generating(state, attempt, at=AT), attempt


def ready(state, unit_id=UNIT):
    """推进一次真实生成、本地校验和有效 Candidate 接纳。"""

    state, attempt = start(state, unit_id)
    state = sm.mark_unit_validating(state, attempt, at=AT)
    return sm.record_candidate_ready(state, candidate(state, unit_id=unit_id), at=AT)


def invalid(state, unit_id=UNIT):
    """模拟 parser 直接返回内容问题的路径，保留失败 Candidate。"""

    state, _ = start(state, unit_id)
    return sm.record_candidate_invalid(state, candidate(state, valid=False, unit_id=unit_id), at=AT)


def exhausted(state, unit_id=UNIT):
    """耗尽当前轮的剩余 Local 尝试后显式关闭轮次。"""

    while state.unit_states[unit_id].attempt_in_round < 3:
        state = invalid(state, unit_id)
    return sm.mark_round_exhausted(state, unit_id, at=AT)


def phases():
    """沿正常转换链生成覆盖全部 Run phase 的合法快照。"""

    preparing = run()
    generating = ready(sm.begin_generation(preparing, at=AT))
    checking = sm.begin_global_check(generating, at=AT)
    assembling = sm.begin_assembly(checking, at=AT)
    validating = sm.begin_validation(assembling, at=AT)
    persisting = sm.begin_pending_persistence(validating, at=AT)
    return {state.phase: state for state in (preparing, generating, checking, assembling, validating, persisting)}
