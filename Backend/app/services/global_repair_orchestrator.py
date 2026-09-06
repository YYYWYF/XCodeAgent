"""T6.3 单写者上的串行 Global repair 循环；检查与 Unit 执行由受信适配器注入。"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.services.global_issue_attribution import (
    GlobalRepairDecision,
    aggregate_global_repair_decision,
)
from app.services.global_planning_validation import check_candidate_completeness
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import PlanningRun, UnitRunState
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_run_events import GlobalRepairStarted, RunFailed
from app.services.unit_generation_orchestrator import complete_generation_round


GlobalValidator = Callable[[PlanningRun], Awaitable[GlobalRepairDecision]]
UnitRoundRunner = Callable[[UnitRunState], Awaitable[None]]


def _utc_timestamp() -> str:
    """提供事件时间，测试可用固定时钟替换。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def run_global_repair_loop(
    controller: PlanningRunController,
    *,
    validate_global: GlobalValidator,
    regenerate_unit: UnitRoundRunner,
    now: Callable[[], str] = _utc_timestamp,
) -> GlobalRepairDecision:
    """从已结束的生成轮开始，串行修复到 Global 无 Issue 或 Run 失败。

    每个活动 Run 只允许一个调用方驱动此循环。validate_global 只在 Candidate 齐全时
    接收只读 Run，并须返回本次完整检查经 T4.1 归因的决策；本任务可注入 mock。
    regenerate_unit 是单 Unit 编排适配器，须经同一 Controller 跑到 ready/exhausted；
    模型策略接 run_unit_generation_round，把 Unit.current_issues 作为 global_feedback。
    deterministic 策略由适配器执行确定性生成，不能进入模型 Local retry。

    成功返回空 Issue 决策，Run 保持 active/global_check，交还调用方；这里不实现
    Scope Assembly、Pending 或生产流程接入。失败提交 RunFailed 并返回完整失败决策；
    Local 基础设施异常由现有 Local 编排先终止 Run，再原样上抛。
    """

    while True:
        snapshot = controller.snapshot
        if snapshot.status != "active" or snapshot.phase not in {"generating_units", "global_check"}:
            raise ValueError("Global repair loop 只接受活动且已完成生成轮的 Run。")
        if snapshot.phase == "generating_units":
            completeness = await complete_generation_round(controller, now=now)
        else:
            completeness = check_candidate_completeness(snapshot.unit_states)

        # 缺失时不能对部分 Candidate 做 Assembly/Task 来源归因；整批缺失共同消耗一轮。
        if completeness.complete:
            decision = GlobalRepairDecision.model_validate(await validate_global(controller.snapshot))
        else:
            decision = aggregate_global_repair_decision(completeness.issues)
        if not decision.issues:
            return decision

        snapshot = controller.snapshot
        exhausted = snapshot.global_repair_round >= snapshot.global_repair_limit
        if not decision.retryable or exhausted:
            # 保留这一批的全部诊断，包括最后一次检查结果；不得从 blocker 中筛出可修复子集。
            blocker = next((issue for issue in decision.issues if not issue.retryable), None)
            await controller.apply(RunFailed(issue=ValidationIssue(
                code="GLOBAL_REPAIR_BLOCKED" if blocker else "GLOBAL_REPAIR_LIMIT_EXHAUSTED",
                level="global",
                category=blocker.category if blocker else "generation",
                unit_ids=tuple(sorted({unit for issue in decision.issues for unit in issue.unit_ids})),
                task_ids=tuple(sorted({task for issue in decision.issues for task in issue.task_ids})),
                retry_unit_ids=(),
                retryable=False,
                message="Global 检查存在不可修复问题。" if blocker else "Global=2 修复额度已耗尽。",
                details={
                    "global_repair_round": snapshot.global_repair_round,
                    "issues": [issue.model_dump(mode="json") for issue in decision.issues],
                },
            ), at=now()))
            return decision

        # 一次提交原子重开全部去重目标；第一个 Unit 开始前，所有旧有效候选都已 supersede。
        await controller.apply(GlobalRepairStarted(decision=decision, at=now()))
        for unit_id in decision.retry_unit_ids:
            current = controller.snapshot
            if current.status != "active":
                return decision
            await regenerate_unit(current.unit_states[unit_id])
            if controller.snapshot.status != "active":
                return decision
            if controller.snapshot.unit_states[unit_id].generation_status not in {"candidate_ready", "round_exhausted"}:
                raise ValueError("Unit round runner 必须完成当前 Unit 的整个 generation round。")
        # 下一轮必须重新通过完整 Barrier；保留的 unaffected Candidate 不重新生成。
