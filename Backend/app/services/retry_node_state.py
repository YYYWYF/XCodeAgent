"""以完整失败现场和当前磁盘输入构造一次新的节点执行。"""

from copy import deepcopy
from typing import Any


def retry_node_state(values: dict[str, Any], node: str, *, planning: bool) -> dict[str, Any]:
    """保留业务上下文，撤销旧运行控制和一次性确认，避免重试替用户批准新产物。"""
    state = deepcopy(values)
    state.update(
        phase=node, status="running" if planning else "in_progress", error=None,
        clarification={}, resume_from=node, resume_execution_run_id="", workflow_action="",
        application_planning_interaction={}, application_planning_review_route="",
        lifecycle_interaction_submission={}, revision_interaction={},
        application_planning_confirmation={}, build_task_plan_confirmation={},
        test_phase_confirmation={}, review_phase_confirmation={}, acceptance_phase_confirmation={},
        code_review_repair_confirmation={}, api_design_gate_action={}, ui_design_action={},
        small_task_handoff_submission={}, acceptance_decision="", accepted=False,
    )
    # 失败证据属于上一轮运行；返修报告、候选和真实变更集仍完整保留。
    state.pop("failure_diagnostic", None)
    state.pop("failureDiagnostic", None)
    if node.endswith("_review") or node == "planning_stage_entry":
        # 原审阅门重新要求用户作答；展示载荷不等于已经提交的确认动作。
        state["clarification"] = deepcopy(values.get("clarification") or {})
    if node in {"unit_test_repair", "small_task_repair", "direct_modification_repair"}:
        for key in ("repair_tasks", "small_task_tasks"):
            if isinstance(state.get(key), list):
                state[key] = [
                    {**task, "status": "pending"} if isinstance(task, dict) and task.get("status") in {"failed", "running", "in_progress"} else task
                    for task in state[key]
                ]
    return state


def refresh_retry_node_inputs(state: dict[str, Any], node: str) -> dict[str, Any]:
    """只覆盖节点的磁盘输入，未落盘候选和调用上下文继续来自失败 checkpoint。"""
    from app.services.stage_artifacts import load_stage_artifacts

    # 审阅节点复用对应产物的读取声明，但保持原审阅位置，不调用生成器。
    input_node = {
        "requirements_review": "requirements", "requirement_document_review": "product_planning",
        "ui_confirmation_review": "ui_confirmation", "planning_stage_entry": "ui_confirmation",
        "technical_planning_review": "technical_planning_generate",
    }.get(node, node)
    state.update(load_stage_artifacts(state, input_node))
    if node in {"technical_planning_begin", "technical_planning_generate"}:
        from app.domain.application_planning_recovery import application_planning_sha256

        # 基线可被用户更新，生成事务仍保留原请求及操作身份，并绑定当前基线。
        boundary = deepcopy(state.get("application_planning_recovery_boundary") or {})
        baseline = state.get("technical_plan")
        if baseline:
            boundary["baselineSha256"] = application_planning_sha256(baseline)
        else:
            boundary.pop("baselineSha256", None)
        state["application_planning_recovery_boundary"] = boundary
    return state
