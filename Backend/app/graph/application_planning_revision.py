from __future__ import annotations

from typing import Any

from app.agents.design_conversation import (
    DesignConversationDecision,
    classify_design_conversation,
)
from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.graph.state import ProjectState
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    load_application_lifecycle,
    restart_application_planning_lifecycle,
)


DESIGN_CHANGE_TARGET_NODES = (
    "requirements",
    "product_planning",
    "ui_confirmation",
)

DESIGN_CHANGE_NEXT_NODE = {
    "requirements": "product_planning",
    "product_planning": "ui_confirmation",
    "ui_confirmation": "technical_planning",
    "technical_planning": "",
}

_FORMAL_REVISION_DESIGN_TARGETS = {
    "requirement-spec": "requirements",
    "product-plan": "product_planning",
    "ui-design": "ui_confirmation",
}


def analyze_design_intent(state: ProjectState) -> dict[str, Any]:
    """识别最早受影响产物，并把原创建生命周期回退到对应真实节点。"""

    request = str(state.get("request") or "").strip()
    if not request:
        raise ValueError("设计变更必须提供用户输入。")
    authoritative_target, authoritative_page_ids = _formal_revision_context(state)
    if authoritative_target is not None:
        # 正式二次修改已经在影响确认阶段固定最早产物和目标资源；用户点击
        # “确认并返回设计阶段”后必须立即进入真实生成节点，不能再调用一次
        # 设计分类模型形成额外等待、失败点或目标漂移。
        target = authoritative_target
        decision = DesignConversationDecision(
            target=target,
            reason=(
                f"formal revision 起点由 lifecycle.currentArtifact 固定为 {target}，"
                "直接进入对应正式产物生成节点。"
            ),
            affected_page_ids=authoritative_page_ids,
            response="",
        )
    else:
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
    if authoritative_target is None and target != decision.target:
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
        "design_change_generation_target": target,
        "design_change_generation_request": request,
        "design_change_existing_artifacts": existing_artifact_presence(state),
        "conversation_response": decision.response,
        "application_planning_confirmation": {},
        "timeline": ["design_intent_analysis"],
    }
    if target in DESIGN_CHANGE_TARGET_NODES:
        lifecycle = restart_application_planning_lifecycle(
            _workspace(state),
            stage={
                "requirements": ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                "product_planning": ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                "ui_confirmation": ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
            }[target],
            active_run_id=state.get("active_run_id"),
        )
        update["lifecycle"] = application_lifecycle_payload(lifecycle)
        if target == "requirements":
            # 需求层变更立即撤销旧确认和旧路径，避免旧 Markdown 在新分析期间继续可见。
            update["requirements_confirmed"] = False
            update["requirement_spec_path"] = ""
            update["requirement_spec_json_path"] = ""
        elif target == "product_planning":
            # 产品层变更期间只展示新一版草稿，避免旧正式规划被误当成当前候选。
            update["product_plan_path"] = ""
            update["product_plan_json_path"] = ""
    return update


def _formal_revision_context(state: ProjectState) -> tuple[str | None, list[str]]:
    """读取 formal revision 的生命周期起点和服务端目标页面上下文。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    if not workspace:
        return None, []
    lifecycle = load_application_lifecycle(workspace)
    active = lifecycle.active_formal_revision if lifecycle is not None else None
    if active is None or active.formal_branch.value != "design_stage_revision":
        return None, []
    artifact = str(active.current_artifact or "").strip()
    target = _FORMAL_REVISION_DESIGN_TARGETS.get(artifact)
    if target is None:
        raise ValueError(
            "design_stage_revision 的 lifecycle.currentArtifact 无法映射到当前设计节点。"
        )
    page_ids: list[str] = []
    revision_target = active.target
    if revision_target.type == "page" and str(revision_target.page_id or "").strip():
        page_ids.append(str(revision_target.page_id).strip())
    return target, page_ids


def route_design_intent(state: ProjectState) -> str:
    """把意图结果路由到原创建 Graph 的真实产物节点。"""

    target = str(state.get("design_change_target") or "chat")
    return target if target in DESIGN_CHANGE_TARGET_NODES else "design_chat_response"


def design_chat_response(state: ProjectState) -> dict[str, Any]:
    """无需修改正式产物时回复用户，并返回原正式产物审阅门。"""

    return {
        **cleared_design_change_context(),
        "workflow_scope": "application_planning",
        "phase": "design_chat_response",
        "status": "completed",
        "conversation_response": str(state.get("conversation_response") or "").strip(),
        "design_interaction_origin": str(
            state.get("design_interaction_origin") or "requirements"
        ),
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
        "design_change_generation_target": "",
        "design_change_generation_request": "",
        "design_change_existing_artifacts": {},
        "design_interaction_origin": "",
    }


def design_artifact_node_state(state: ProjectState, node_name: str) -> ProjectState:
    """仅让服务端指定的首个修订节点消费一次原始设计变更指令。"""

    if not is_design_change(state):
        return state
    request = str(state.get("request") or "")
    interaction = state.get("application_planning_interaction")
    if (
        not isinstance(interaction, dict)
        or not interaction
    ) and state.get("design_change_generation_target") == node_name:
        request = str(
            state.get("design_change_generation_request")
            or state.get("design_change_request")
            or request
        )
    return {**state, "request": request, "workflow_scope": "application_planning"}


def design_node_update(
    state: ProjectState,
    node_name: str,
    update: dict[str, Any],
) -> dict[str, Any]:
    """保留修订展示上下文，并在目标生成节点完成后消费一次修改指令。"""

    normalized_update = {
        # 新一轮设计修订复用原 planning checkpoint；新 TechnicalPlan 尚未确认前，
        # 上一轮已签发的 continuation 不再有效，必须由每个设计节点显式清空。
        "revision_continuation": {},
        **update,
        "application_planning_interaction": {},
        "design_interaction_origin": "",
    }
    if not is_design_change(state):
        return normalized_update
    generation_target = str(state.get("design_change_generation_target") or "")
    generation_request = str(state.get("design_change_generation_request") or "")
    if generation_target == node_name and update.get("status") == "completed":
        generation_target = DESIGN_CHANGE_NEXT_NODE.get(node_name, "")
        generation_request = _downstream_regeneration_request(node_name, generation_target)
    return {
        **normalized_update,
        "workflow_scope": "application_planning",
        "design_change_submission": True,
        "design_change_request": str(state.get("design_change_request") or "").strip(),
        "design_change_target": str(state.get("design_change_target") or "").strip(),
        "design_change_reason": str(state.get("design_change_reason") or "").strip(),
        "design_change_generation_target": generation_target,
        "design_change_generation_request": generation_request,
        "design_change_existing_artifacts": dict(
            state.get("design_change_existing_artifacts") or {}
        ),
    }


def begin_current_artifact_revision(
    state: ProjectState,
    *,
    node_name: str,
    request: str,
) -> dict[str, Any]:
    """由当前审阅门创建修订事务，杜绝前端残留字段伪造“重新生成”。"""

    instruction = request.strip()
    if not instruction:
        raise ValueError("修订当前设计产物必须提供修改意见。")
    update = {
        "application_planning_confirmation": {},
        "design_change_submission": True,
        "design_change_request": instruction,
        "design_change_target": node_name,
        "design_change_reason": instruction,
        "design_change_affected_page_ids": [],
        "design_change_generation_target": node_name,
        "design_change_generation_request": instruction,
        "design_change_existing_artifacts": existing_artifact_presence(state),
    }
    if node_name == "requirements":
        # 需求开始修订时立即撤销旧确认，避免旧文档在新一轮分析期间继续被前端或恢复逻辑当成正式版本。
        update.update(
            {
                "requirements_confirmed": False,
                "requirement_spec_path": "",
                "requirement_spec_json_path": "",
            }
        )
    elif node_name == "product_planning":
        # 产品开始修订时清空旧路径，待新候选写入 drafts/plans 后再公开给前端。
        update.update(
            {
                "product_plan_path": "",
                "product_plan_json_path": "",
            }
        )
    return update


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
        "application_planning_interaction": {
            "action": "ui_action",
        },
    }


def _downstream_regeneration_request(current_node: str, next_node: str) -> str:
    """为下游产物生成依赖更新指令，避免重复套用用户原始修改文本。"""

    if not next_node:
        return ""
    labels = {
        "requirements": "RequirementSpec",
        "product_planning": "ProductPlan",
        "ui_confirmation": "UiDesign",
        "technical_planning": "TechnicalPlan",
    }
    return (
        f"上游 {labels.get(current_node, current_node)} 已更新为正式新版本。"
        f"请严格依据最新上游产物重新生成 {labels.get(next_node, next_node)}，"
        "不要把上一阶段的用户修改原文当作当前阶段的独立新增需求。"
    )


def _dict_value(value: Any) -> dict[str, Any] | None:
    """只接受字典产物，避免把无效公开状态带入意图路由。"""

    return value if isinstance(value, dict) else None


def existing_artifact_presence(state: ProjectState) -> dict[str, bool]:
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
