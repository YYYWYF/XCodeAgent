"""按当前 PlanningRun 的结构化校验事实生成中文诊断，不读取模型原文。"""

from collections.abc import Mapping

from app.domain.execution_recovery import ExecutionFailureEvidence, ExecutionFailureOrigin
from app.services.planning_run_contracts import PlanningRun, PlanningRunProjection
from app.services.unit_model_failure import is_unit_model_failure_issue


def _issue_reason(code: str) -> str:
    """按已定义的错误码分类说明，未知码不猜测具体原因。"""

    if code == "UNIT_CANDIDATE_OUTPUT_TRUNCATED":
        return "模型输出达到长度限制，被截断。"
    if code == "RAW_CANDIDATE_TASK_ID_MISSING":
        return "生成的任务缺少必要的任务标识。"
    if code in {"RAW_CANDIDATE_TASKS_MISSING", "CANDIDATE_TASK_FIELD_MISSING", "CANDIDATE_TASK_FIELD_EMPTY"}:
        return "生成的任务缺少必填信息。"
    if code in {"RAW_CANDIDATE_TASK_ID_DUPLICATE", "CANDIDATE_TASK_ID_DUPLICATE"}:
        return "生成的任务标识重复。"
    if code.startswith("RAW_CANDIDATE_"):
        return "模型返回的任务数据格式不符合要求。"
    if code in {"CANDIDATE_ENDPOINT_OWNER_CONFLICT", "CANDIDATE_RETAINED_ENDPOINT_OWNER_CONFLICT", "CANDIDATE_MANAGED_FILE_CONFLICT"}:
        return "生成的任务与现有接口或文件的归属冲突。"
    if code in {"CANDIDATE_PATH_OUTSIDE_ALLOWED_SCOPE", "CANDIDATE_DELIVERABLE_PATH_OUTSIDE_SCOPE"}:
        return "生成的任务涉及允许修改范围之外的文件。"
    if code.startswith("CANDIDATE_"):
        return "生成的任务内容未通过检查。"
    if code.startswith("UNIT_VALIDATION_"):
        return "任务校验所需的上下文或约束信息不符合要求。"
    if code == "GLOBAL_CANDIDATE_MISSING":
        return "部分任务未生成通过检查的执行计划。"
    return "执行计划未通过检查，未能确定具体原因。"


def planning_failure_evidence(
    snapshot: PlanningRun | PlanningRunProjection,
    *,
    source_run_id: str,
    source_thread_id: str,
) -> ExecutionFailureEvidence | None:
    """只投影同 Run、同 Thread 的失败事实，主失败与每轮原因分别保留。"""

    if (snapshot.workflow_run_id != source_run_id or snapshot.thread_id != source_thread_id
            or snapshot.status != "failed" or snapshot.failure is None):
        return None
    failure = snapshot.failure
    # 模型 SDK 失败继续使用原有主失败归因门禁，本模块只处理规划内容问题。
    if is_unit_model_failure_issue(failure):
        return None
    exhausted = failure.code == "GLOBAL_REPAIR_LIMIT_EXHAUSTED"
    missing = any(issue.code == "GLOBAL_CANDIDATE_MISSING" for issue in snapshot.global_issues)
    # 耗尽时主失败保留最后一批问题，不从错误文本解析原因。
    missing = missing or any(
        issue.get("code") == "GLOBAL_CANDIDATE_MISSING"
        for issue in failure.details.get("issues", ()) if isinstance(issue, Mapping)
    )
    if exhausted and missing:
        summary = "生成执行计划失败：AI 生成的任务信息不完整，自动修复后仍未通过检查。请重试。"
    elif exhausted:
        summary = "生成执行计划失败：AI 生成的任务未通过检查，自动修复次数已用完。请重试。"
    else:
        summary = "生成执行计划失败：" + _issue_reason(failure.code)
    reasons: list[str] = []
    for unit_id in failure.unit_ids:
        unit = snapshot.unit_states.get(unit_id)
        if unit is None:
            continue
        rounds = [(item.generation_round, item.issues) for item in unit.round_history]
        rounds.append((unit.generation_round, unit.current_issues))
        for round_number, issues in rounds:
            for issue in issues:
                reason = f"{unit_id}，第 {round_number} 轮：{_issue_reason(issue.code)}（{issue.code}）"
                if reason not in reasons:
                    reasons.append(reason)
    diagnostic = "\n".join([summary, *reasons])[:2048]
    return ExecutionFailureEvidence(
        origin=ExecutionFailureOrigin.UNKNOWN,
        code=failure.code,
        operation="prepare_build_tasks",
        replay_compatible=False,
        diagnostic_message=diagnostic,
        user_message=summary,
    )
