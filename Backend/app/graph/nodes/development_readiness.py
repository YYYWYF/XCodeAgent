"""开发目标进入任务拆解前的实体绑定门禁。"""

from __future__ import annotations

from app.graph.state import ProjectState
from app.services.development_readiness import development_readiness
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload


def development_readiness_gate(state: ProjectState) -> dict:
    """阻止依赖实体尚未完成 EntitySourceBinding 的页面/API进入任务拆解。"""

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError("缺少已确认 TechnicalPlan，无法检查开发前置条件。")
    target_type = "endpoint" if str(state.get("selected_endpoint_id") or "").strip() else "page"
    target_id = (
        str(state.get("selected_endpoint_id") or "").strip()
        if target_type == "endpoint"
        else str(state.get("selectedPageId") or "").strip()
    )
    if not target_id:
        raise ValueError("请选择要开始开发的页面或 API。")
    readiness = development_readiness(
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=str(state.get("selected_api_contract_id") or "").strip() or None,
    )
    if readiness["ready"]:
        return {
            "phase": "development_readiness_gate",
            "status": "completed",
            "development_readiness": readiness,
            "clarification": {},
            "timeline": ["development_readiness_gate"],
        }
    missing = readiness["missing_entities"]
    labels = "、".join(
        str(item.get("entity_name") or item.get("entity_id") or "") for item in missing
    )
    clarification = build_ask_user_payload(
        [
            AskUserQuestion(
                header="实体绑定前置",
                question=(
                    f"当前目标依赖实体 {labels}，尚未完成 EntitySourceBinding。"
                    "请从左侧实体列表手动进入对应实体的数据源绑定，确认后重新发起当前页面/API开发。"
                ),
                type="text",
                placeholder="完成实体数据源绑定后，重新点击开始开发。",
            )
        ]
    )
    clarification.update(
        {
            "mode": "entity_source_binding_required",
            "status": "requires_user_input",
            "message": "存在未完成的数据源绑定实体，当前开发目标已暂停。",
            "missing_entities": missing,
            "development_target": {
                "type": target_type,
                "id": target_id,
                "api_contract_id": readiness.get("api_contract_id"),
            },
        }
    )
    return {
        "phase": "development_readiness_gate",
        "status": "requires_user_input",
        "development_readiness": readiness,
        "clarification": clarification,
        "timeline": ["development_readiness_gate"],
    }
