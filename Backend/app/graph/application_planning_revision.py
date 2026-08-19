from __future__ import annotations

from typing import Any

from app.agents.design_conversation import classify_design_conversation
from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.graph.state import ProjectState
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    restart_application_planning_lifecycle,
)


DESIGN_CHANGE_TARGET_NODES = (
    "requirements",
    "product_planning",
    "ui_confirmation",
)

DESIGN_CHANGE_APPLIED_NODES = (*DESIGN_CHANGE_TARGET_NODES, "technical_planning")


def analyze_design_intent(state: ProjectState) -> dict[str, Any]:
    """识别最早受影响产物，并把原创建生命周期回退到对应真实节点。"""

    request = str(state.get("request") or "").strip()
    if not request:
        raise ValueError("设计变更必须提供用户输入。")
    decision = classify_design_conversation(
        request,
        requirement_spec=_dict_value(state.get("requirement_spec")),
        product_plan=_dict_value(state.get("product_plan")),
        ui_designs=_dict_value(state.get("ui_designs")),
    )
    target = earliest_available_design_target(
        decision.target,
        requirement_spec=_dict_value(state.get("requirement_spec")),
        product_plan=_dict_value(state.get("product_plan")),
    )
    reason = decision.reason
    if target != decision.target:
        reason = f"{reason}；上游产物尚未确认，先回到 {target}。"
    update: dict[str, Any] = {
        "workflow_scope": "application_planning",
        "phase": "design_intent_analysis",
        "status": "completed",
        "design_change_submission": True,
        "design_change_request": request,
        "design_change_target": target,
        "design_change_reason": reason,
        "design_change_affected_page_ids": decision.affected_page_ids,
        "design_change_applied_nodes": [],
        "design_change_existing_artifacts": _existing_artifact_presence(state),
        "conversation_response": decision.response,
        "application_planning_confirmation": {},
        "timeline": ["design_intent_analysis"],
    }
    if target in DESIGN_CHANGE_TARGET_NODES:
        lifecycle = restart_application_planning_lifecycle(
            _workspace(state),
            stage={
                "requirements": ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                "product_planning": ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
                "ui_confirmation": ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
            }[target],
            active_run_id=state.get("active_run_id"),
        )
        update["lifecycle"] = application_lifecycle_payload(lifecycle)
    return update


def route_design_intent(state: ProjectState) -> str:
    """把意图结果路由到原创建 Graph 的真实产物节点。"""

    target = str(state.get("design_change_target") or "chat")
    return target if target in DESIGN_CHANGE_TARGET_NODES else "design_chat_response"


def design_chat_response(state: ProjectState) -> dict[str, Any]:
    """无需修改正式产物时在原创建 Graph 内结束本轮。"""

    return {
        "workflow_scope": "application_planning",
        "phase": "design_chat_response",
        "status": "completed",
        "conversation_response": str(state.get("conversation_response") or "").strip(),
        "timeline": ["design_chat_response"],
    }


def earliest_available_design_target(
    target: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
) -> str:
    """禁止设计意图越过尚未确认的上游正式产物。"""

    if target == "chat":
        return target
    if not requirement_spec or requirement_spec.get("confirmation_status") != "confirmed":
        return "requirements"
    if target == "ui_confirmation" and (
        not product_plan or product_plan.get("confirmation_status") != "confirmed"
    ):
        return "product_planning"
    return target


def is_design_change(state: ProjectState) -> bool:
    """判断当前 checkpoint 是否处于一次设计产物修订链路。"""

    return bool(str(state.get("design_change_request") or "").strip())


def cleared_design_change_context() -> dict[str, Any]:
    """在变更链路终结后重置设计变更上下文，避免旧变更指令在后续轮次复活。"""

    return {
        "design_change_submission": False,
        "design_change_request": "",
        "design_change_target": "",
        "design_change_reason": "",
        "design_change_affected_page_ids": [],
        "design_change_applied_nodes": [],
        "design_change_existing_artifacts": {},
    }


def design_artifact_node_state(state: ProjectState, node_name: str) -> ProjectState:
    """首次重做节点使用原始变更指令，确认轮次使用本轮确认答案。"""

    if not is_design_change(state):
        return state
    request = (
        str(state.get("request") or "")
        if design_node_was_applied(state, node_name)
        else str(state.get("design_change_request") or state.get("request") or "")
    )
    return {**state, "request": request, "workflow_scope": "application_planning"}


def design_node_update(
    state: ProjectState,
    node_name: str,
    update: dict[str, Any],
) -> dict[str, Any]:
    """记录本次变更已经应用的真实节点，避免确认时重复套用原指令。"""

    if not is_design_change(state):
        return update
    applied = [
        str(item)
        for item in state.get("design_change_applied_nodes", [])
        if str(item) in DESIGN_CHANGE_APPLIED_NODES
    ]
    if node_name not in applied:
        applied.append(node_name)
    return {
        **update,
        "workflow_scope": "application_planning",
        "design_change_submission": True,
        "design_change_request": str(state.get("design_change_request") or "").strip(),
        "design_change_target": str(state.get("design_change_target") or "").strip(),
        "design_change_reason": str(state.get("design_change_reason") or "").strip(),
        "design_change_applied_nodes": applied,
        "design_change_existing_artifacts": dict(
            state.get("design_change_existing_artifacts") or {}
        ),
    }


def design_node_was_applied(state: ProjectState, node_name: str) -> bool:
    """判断原始变更指令是否已经在指定真实节点执行过。"""

    return node_name in {
        str(item) for item in state.get("design_change_applied_nodes", [])
    }


def prepare_ui_revision_state(state: ProjectState) -> ProjectState:
    """页面集合稳定时增量调整现有 UI，变化时按新 ProductPlan 重建设计稿。"""

    existing = _dict_value(state.get("ui_designs"))
    product_plan = _dict_value(state.get("product_plan")) or {}
    expected_ids = {
        str(page.get("pageId") or "").strip()
        for page in product_plan.get("pages", [])
        if isinstance(page, dict) and str(page.get("pageId") or "").strip()
    }
    existing_pages = existing.get("pages", []) if existing else []
    existing_ids = {
        str(page.get("pageId") or "").strip()
        for page in existing_pages
        if isinstance(page, dict) and str(page.get("pageId") or "").strip()
    }
    if not existing_pages or expected_ids != existing_ids:
        return {**state, "ui_designs": None, "ui_design_action": None}
    return {
        **state,
        "ui_designs": {
            **existing,
            "confirmation_status": "pending_user_confirmation",
        },
        "ui_design_action": {
            "action": "adjust_pages",
            "pageIds": list(state.get("design_change_affected_page_ids", [])),
            "instruction": str(state.get("design_change_request") or "").strip(),
        },
        "user_interaction_submission": True,
    }


def _dict_value(value: Any) -> dict[str, Any] | None:
    """只接受字典产物，避免把无效公开状态带入意图路由。"""

    return value if isinstance(value, dict) else None


def _existing_artifact_presence(state: ProjectState) -> dict[str, bool]:
    """冻结设计变更开始前的产物存在状态，供各阶段区分首次生成与重新生成。"""

    return {
        "requirements": bool(
            _dict_value(state.get("requirement_spec"))
            or str(state.get("requirement_spec_path") or "").strip()
        ),
        "product_planning": bool(
            _dict_value(state.get("product_plan"))
            or str(state.get("product_plan_path") or "").strip()
        ),
        "ui_confirmation": bool(_dict_value(state.get("ui_designs"))),
        "technical_planning": bool(
            _dict_value(state.get("technical_plan"))
            or str(state.get("technical_plan_path") or "").strip()
        ),
    }


def _workspace(state: ProjectState) -> str:
    """校验设计修订仍绑定原创建规划工作区。"""

    workspace = str(state.get("workspace") or "").strip()
    if not workspace:
        raise ValueError("设计变更必须提供原创建规划 workspaceRoot。")
    return workspace
