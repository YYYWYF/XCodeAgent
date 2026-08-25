from __future__ import annotations

from typing import Any, Literal

from langgraph.types import Command, interrupt

from app.domain.application_planning_interaction import (
    ApplicationPlanningArtifact,
    ApplicationPlanningInteraction,
    application_planning_artifact_revision,
    application_planning_gate_id,
)
from app.graph.application_planning_revision import (
    begin_current_artifact_revision,
)
from app.graph.state import ProjectState


ARTIFACT_BY_NODE: dict[str, ApplicationPlanningArtifact] = {
    "requirements": "requirement_spec",
    "requirement_document": "requirement_document",
    "ui_confirmation": "ui_designs",
    "technical_planning": "technical_plan",
}

CONFIRMATION_MODE_BY_ARTIFACT: dict[ApplicationPlanningArtifact, str] = {
    "requirement_document": "requirement_document_confirmation",
    "ui_designs": "ui_design_confirmation",
    "technical_plan": "technical_plan_confirmation",
}


def application_planning_review_payload(
    state: ProjectState,
    node_name: str,
) -> dict[str, Any]:
    """从 checkpoint 中的待确认产物构造原生 interrupt 公开载荷。"""

    artifact = ARTIFACT_BY_NODE[node_name]
    artifact_value = (
        {"requirement_spec": state.get("requirement_spec"), "product_plan": state.get("product_plan")}
        if artifact == "requirement_document"
        else state.get(artifact)
    )
    revision_source = {
        "artifact": artifact_value,
        "clarification": state.get("clarification"),
    }
    revision = application_planning_artifact_revision(revision_source)
    return {
        "type": "application_planning_review",
        "gateId": application_planning_gate_id(artifact, revision),
        "artifact": artifact,
        "artifactRevision": revision,
        "phase": node_name,
        "clarification": state.get("clarification") or {},
    }


def validate_application_planning_review_action(
    state: ProjectState,
    node_name: str,
    submission: ApplicationPlanningInteraction,
) -> None:
    """按当前审阅门的产物和 clarification 状态校验动作组合。"""

    artifact = ARTIFACT_BY_NODE[node_name]
    clarification = state.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    mode = str(clarification.get("mode") or "")
    status = str(clarification.get("status") or "")
    questions = clarification.get("questions")
    has_questions = isinstance(questions, list) and bool(questions)
    if status != "requires_user_input":
        raise ValueError(f"当前 {node_name} 审阅门不在等待用户输入状态，拒绝 action={submission.action}。")

    if submission.action == "design_change":
        # 底部设计聊天可以从任意正式产物审阅门进入统一的设计意图路由。
        return

    if submission.action == "answer":
        if artifact != "requirement_spec" or not has_questions:
            raise ValueError(
                f"当前 {artifact} 审阅门不允许 action=answer；澄清问题必须存在且不能提交确认卡答案。"
            )
        if not submission.answers and not submission.request.strip():
            raise ValueError("需求澄清 action=answer 必须提供回答内容。")
        return

    expected_mode = CONFIRMATION_MODE_BY_ARTIFACT.get(artifact)
    if submission.action == "revise" and mode in {
        "technical_plan_generation_error",
        "project_plan_dependency_validation_error",
        "project_plan_revision_required",
    }:
        # 技术计划生成或依赖校验失败时，修订动作表示用户授权重新生成当前产物。
        return
    if not expected_mode or mode != expected_mode:
        raise ValueError(
            f"当前 {artifact} clarification.mode={mode or 'unknown'} 不允许 action={submission.action}。"
        )
    if submission.action == "ui_action" and artifact != "ui_designs":
        raise ValueError("只有 ui_designs 审阅门允许 action=ui_action。")
    if submission.action not in {"confirm", "revise", "ui_action"}:
        raise ValueError(
            f"当前 {artifact} 审阅门不允许 action={submission.action}。"
        )


def resume_application_planning_review(
    state: ProjectState,
    node_name: str,
) -> Command[Any]:
    """暂停在正式产物审阅门，并把显式恢复动作路由到确定节点。"""

    payload = application_planning_review_payload(state, node_name)
    submission = ApplicationPlanningInteraction.model_validate(interrupt(payload))
    if submission.gate_id != payload["gateId"]:
        raise ValueError("提交的创建规划确认卡已经过期，请刷新后重试。")
    if submission.artifact != payload["artifact"]:
        raise ValueError("创建规划交互与当前待确认产物不匹配。")
    if submission.artifact_revision != payload["artifactRevision"]:
        raise ValueError("待确认产物已经更新，请基于最新版本重新提交。")

    validate_application_planning_review_action(state, node_name, submission)

    if submission.action == "design_change" or (
        submission.action == "revise" and node_name == "requirement_document"
    ):
        # 需求+产品规划合并确认门上的“修改”可能涉及任一产物，统一走设计意图分析，
        # 由分类器路由到最早受影响产物并级联重新生成下游。
        if not submission.request.strip():
            raise ValueError("设计变更必须提供明确的修改要求。")
        return Command(
            update={
                "request": submission.request.strip(),
                "design_interaction_origin": node_name,
                "application_planning_interaction": {},
            },
            goto="design_intent_analysis",
        )

    update: dict[str, Any] = {
        "request": submission.request.strip(),
        "application_planning_interaction": submission.model_dump(
            by_alias=False,
            exclude_none=True,
        ),
    }
    if submission.edited_requirement_spec is not None:
        update["edited_requirement_spec"] = submission.edited_requirement_spec
    if submission.requirement_spec_feedback:
        update["requirement_spec_feedback"] = submission.requirement_spec_feedback
    if submission.ui_action is not None:
        update["ui_design_action"] = submission.ui_action
    if submission.action == "revise":
        update.update(
            begin_current_artifact_revision(
                state,
                node_name=node_name,
                request=submission.request,
            )
        )
        if node_name == "ui_confirmation":
            update["ui_design_action"] = {
                "action": "adjust_pages",
                "pageIds": [],
                "instruction": submission.request.strip(),
            }
    target_node = "product_planning" if node_name == "requirement_document" else node_name
    return Command(update=update, goto=target_node)


def requirements_review(
    state: ProjectState,
) -> Command[Literal["requirements", "design_intent_analysis"]]:
    """暂停 RequirementSpec 审阅并恢复到需求处理或设计意图分析。"""

    return resume_application_planning_review(state, "requirements")


def requirement_document_review(
    state: ProjectState,
) -> Command[Literal["product_planning", "design_intent_analysis"]]:
    """暂停联合需求文档审阅并恢复到产品规划或设计意图分析。"""

    return resume_application_planning_review(state, "requirement_document")


def ui_confirmation_review(
    state: ProjectState,
) -> Command[Literal["ui_confirmation", "design_intent_analysis"]]:
    """暂停 UiDesign 审阅并恢复到 UI 动作或设计意图分析。"""

    return resume_application_planning_review(state, "ui_confirmation")


def technical_planning_review(
    state: ProjectState,
) -> Command[Literal["technical_planning", "design_intent_analysis"]]:
    """暂停 TechnicalPlan 审阅并恢复到技术规划或设计意图分析。"""

    return resume_application_planning_review(state, "technical_planning")


def pending_review_node(state: ProjectState) -> str:
    """返回设计闲聊结束后需要重新挂起的原审阅门节点。"""

    origin = str(state.get("design_interaction_origin") or "requirements")
    if origin == "product_planning":
        return "requirement_document_review"
    return (
        f"{origin}_review"
        if origin in ARTIFACT_BY_NODE
        else "requirements_review"
    )
