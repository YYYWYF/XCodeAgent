"""技术规划重试的输入整理，继续复用原 begin/generate/commit 节点。"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
    parse_application_planning_boundary,
)
from app.workspace.plan_documents import load_project_plan_json, technical_plan_json_path

TECHNICAL_PLANNING_RETRY_NODES = frozenset({
    "technical_planning_begin", "technical_planning_generate", "technical_planning_commit",
})


@dataclass(frozen=True)
class TechnicalPlanningMessageRevision:
    """绑定只读分类后仍未变化的规划 checkpoint 与用户明确的新修改要求。"""

    request: str
    checkpoint_id: str


def prepare_technical_planning_retry(
    state: dict[str, Any], *, target_node: str = "technical_planning_begin",
    new_request: str | None = None,
) -> dict[str, Any]:
    """保留上次业务要求，以当前已有技术规划为基线重新进入原生成链路。"""

    interaction = state.get("application_planning_interaction") or {}
    request = str(
        interaction.get("request")
        or state.get("design_change_generation_request")
        or state.get("design_change_request")
        or state.get("request")
        or ""
    ).strip()
    if not request:
        raise ValueError("技术规划重试缺少上次用户要求。")
    # 已落盘的正式/待确认计划优先；无落盘计划时保留当前事务已有计划。
    path = technical_plan_json_path(state)
    baseline = load_project_plan_json(path, hydrate_detail_designs=True) if path.is_file() else state.get("technical_plan")
    baseline = deepcopy(baseline) if isinstance(baseline, dict) and baseline else {}
    if new_request is not None:
        if not baseline:
            raise ValueError("当前尚无技术规划，请使用上方重试入口重新生成。")
        request = new_request.strip()
        if not request:
            raise ValueError("技术规划修改要求不能为空。")
    previous = parse_application_planning_boundary(state.get("application_planning_recovery_boundary"))
    formal_revision = bool(state.get("change_id"))
    operation = (
        ApplicationPlanningOperation.REVISE if formal_revision and new_request is None
        else ApplicationPlanningOperation.REPAIR
    )
    source_run_id = str(state.get("active_run_id") or "")
    if previous and previous.boundary is ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED:
        # 仅保留尚未完成 begin 交接的来源；已进入生成的上一轮归属以该轮 Run 为准。
        source_run_id = str(state.get("technical_plan_repair_source_run_id") or source_run_id)
    return {
        "request": request,
        **({"design_change_request": request, "design_change_generation_request": request} if new_request is not None else {}),
        "technical_plan": baseline,
        "application_planning_recovery_boundary": application_planning_boundary_payload(
            operation_id=f"technical-plan-{uuid4().hex}", operation=operation,
            boundary=(
                ApplicationPlanningRecoveryBoundary.GENERATION_READY
                if target_node == "technical_planning_generate"
                else ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED
            ),
            request=request, baseline=baseline or None,
            gate_id=previous.gate_id if previous else None,
            artifact_revision=previous.artifact_revision if previous else None,
        ),
        "application_planning_interaction": {},
        "technical_plan_candidate": {},
        "technical_plan_candidate_sha256": "",
        "technical_plan_repair_candidate": {},
        "technical_plan_repair_errors": [],
        "technical_plan_repair_source_run_id": source_run_id,
        "clarification": {},
        "error": None,
        "status": "running",
    }
