from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
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
    "planning_stage_entry": "ui_designs",
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
    clarification = state.get("clarification")
    if node_name == "planning_stage_entry":
        ui_designs = artifact_value if isinstance(artifact_value, dict) else {}
        skipped = ui_designs.get("confirmation_status") == "skipped"
        clarification = {
            "mode": "planning_stage_entry_confirmation",
            "status": "requires_user_input",
            "question_schema": "xcodeagent.planning-stage-entry.v1",
            "questions": [],
            "assumptions": [],
            "message": (
                "UI 设计已跳过。请确认是否进入规划阶段并开始生成技术规划。"
                if skipped
                else "UI 设计已确认。请确认是否进入规划阶段并开始生成技术规划。"
            ),
            "ui_design_skipped": skipped,
        }
    revision_source = {
        "artifact": artifact_value,
        "clarification": clarification,
    }
    revision = application_planning_artifact_revision(revision_source)
    return {
        "type": "application_planning_review",
        "gateId": application_planning_gate_id(artifact, revision),
        "artifact": artifact,
        "artifactRevision": revision,
        "phase": node_name,
        "clarification": clarification or {},
    }


def validate_application_planning_review_action(
    state: ProjectState,
    node_name: str,
    submission: ApplicationPlanningInteraction,
) -> None:
    """按当前审阅门的产物和 clarification 状态校验动作组合。"""

    artifact = ARTIFACT_BY_NODE[node_name]
    # 校验必须使用当前 interrupt 对外展示的同一份载荷。规划阶段入口的 clarification
    # 是根据已确认/跳过的 UI 状态动态合成的，不会写回 checkpoint；若继续读取 state，
    # 就会把上一阶段遗留状态误判为“当前门禁未等待用户输入”。
    clarification = application_planning_review_payload(state, node_name).get("clarification")
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

    if node_name == "planning_stage_entry":
        if mode != "planning_stage_entry_confirmation" or submission.action != "enter_planning":
            raise ValueError(
                "等待进入规划阶段门禁只允许 action=enter_planning 或设计变更。"
            )
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
    config: RunnableConfig | None = None,
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
    runtime_update = _application_planning_runtime_update(config)

    if submission.action == "design_change" or (
        submission.action == "revise" and node_name == "requirement_document"
    ):
        # 需求+产品规划合并确认门上的“修改”可能涉及任一产物，统一走设计意图分析，
        # 由分类器路由到最早受影响产物并级联重新生成下游。
        if not submission.request.strip():
            raise ValueError("设计变更必须提供明确的修改要求。")
        return Command(
            update={
                **runtime_update,
                # design_change 会提前跳转到意图分析；在跳转前就消费旧 START
                # 指令，避免意图分析失败时 checkpoint 继续保留错误入口。
                "resume_from": "",
                "request": submission.request.strip(),
                "design_interaction_origin": node_name,
                "application_planning_interaction": {},
            },
            goto="design_intent_analysis",
        )

    update: dict[str, Any] = {
        **runtime_update,
        # 原生 interrupt 已精确决定恢复节点，本轮开始后必须消费旧 START 指令。
        "resume_from": "",
        "request": submission.request.strip(),
        "application_planning_interaction": submission.model_dump(
            by_alias=False,
            exclude_none=True,
        ),
    }
    if node_name == "planning_stage_entry":
        return Command(update=update, goto="technical_planning")
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


def _application_planning_runtime_update(
    config: RunnableConfig | None,
) -> dict[str, Any]:
    """门禁校验通过后再提取本轮运行元数据，避免失败恢复污染 pending writes。"""

    if not isinstance(config, dict):
        return {}
    metadata = config.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    configurable = config.get("configurable")
    configurable = configurable if isinstance(configurable, dict) else {}
    thread_id = str(
        metadata.get("thread_id") or configurable.get("thread_id") or ""
    ).strip()
    run_id = str(metadata.get("run_id") or "").strip()
    update: dict[str, Any] = {}
    if thread_id:
        update["active_thread_id"] = thread_id
    if run_id:
        update["active_run_id"] = run_id
    for metadata_key, state_key in (
        ("selected_skill_names", "selected_skill_names"),
        ("workspace", "workspace"),
        ("project_id", "project_id"),
        ("editor_mode", "editor_mode"),
        ("workflow_scope", "workflow_scope"),
    ):
        if metadata_key in metadata and metadata[metadata_key] is not None:
            update[state_key] = metadata[metadata_key]
    return update


def requirements_review(
    state: ProjectState,
    config: RunnableConfig,
) -> Command[Literal["requirements", "design_intent_analysis"]]:
    """暂停 RequirementSpec 审阅并恢复到需求处理或设计意图分析。"""

    return resume_application_planning_review(state, "requirements", config)


def requirement_document_review(
    state: ProjectState,
    config: RunnableConfig,
) -> Command[Literal["product_planning", "design_intent_analysis"]]:
    """暂停联合需求文档审阅并恢复到产品规划或设计意图分析。"""

    return resume_application_planning_review(state, "requirement_document", config)


def ui_confirmation_review(
    state: ProjectState,
    config: RunnableConfig,
) -> Command[Literal["ui_confirmation", "design_intent_analysis"]]:
    """暂停 UiDesign 审阅并恢复到 UI 动作或设计意图分析。"""

    return resume_application_planning_review(state, "ui_confirmation", config)


def technical_planning_review(
    state: ProjectState,
    config: RunnableConfig,
) -> Command[Literal["technical_planning", "design_intent_analysis"]]:
    """暂停 TechnicalPlan 审阅并恢复到技术规划或设计意图分析。"""

    return resume_application_planning_review(state, "technical_planning", config)


def planning_stage_entry(
    state: ProjectState,
    config: RunnableConfig,
) -> Command[Literal["technical_planning", "design_intent_analysis"]]:
    """暂停在规划阶段入口，只有显式进入动作才能开始 TechnicalPlan。"""

    return resume_application_planning_review(state, "planning_stage_entry", config)


def pending_review_node(state: ProjectState) -> str:
    """返回设计闲聊结束后需要重新挂起的原审阅门节点。"""

    origin = str(state.get("design_interaction_origin") or "requirements")
    if origin == "product_planning":
        return "requirement_document_review"
    if origin == "planning_stage_entry":
        return "planning_stage_entry"
    return (
        f"{origin}_review"
        if origin in ARTIFACT_BY_NODE
        else "requirements_review"
    )
