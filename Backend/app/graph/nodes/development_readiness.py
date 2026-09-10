"""开发目标进入任务拆解前的实体绑定门禁。"""

from __future__ import annotations

from app.graph.state import ProjectState
from app.services.agent_development_readiness import (
    agent_entity_binding_bypass_matches,
    inspect_agent_development_readiness,
)
from app.services.development_readiness import development_readiness
from app.services.frontend_page_tree import project_plan_page_records
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload


def development_readiness_gate(state: ProjectState) -> dict:
    """阻止前置条件未满足的页面、API 或 Agent 进入任务拆解。"""

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError("缺少已确认 TechnicalPlan，无法检查开发前置条件。")
    selected_agent_id = str(state.get("selected_agent_id") or "").strip()
    target_type = (
        "agent"
        if selected_agent_id
        else "endpoint"
        if str(state.get("selected_endpoint_id") or "").strip()
        else "page"
    )
    target_id = (
        selected_agent_id
        if target_type == "agent"
        else str(state.get("selected_endpoint_id") or "").strip()
        if target_type == "endpoint"
        else str(state.get("selectedPageId") or "").strip()
    )
    if not target_id:
        raise ValueError("请选择要开始开发的页面、API 或智能体。")
    readiness = (
        inspect_agent_development_readiness(
            str(state.get("workspace") or state.get("workspace_path") or ""),
            target_id,
        )
        if target_type == "agent"
        else development_readiness(
            project_plan,
            target_type=target_type,
            target_id=target_id,
            api_contract_id=str(state.get("selected_api_contract_id") or "").strip() or None,
        )
    )
    blockers = readiness.get("blockers") or []
    agent_binding_bypassed = (
        target_type == "agent"
        and agent_entity_binding_bypass_matches(
            state.get("agent_entity_binding_bypass"),
            target_id,
        )
        and not any(
            isinstance(item, dict) and item.get("type") != "entity_source_binding"
            for item in blockers
        )
    )
    if readiness["ready"] or agent_binding_bypassed:
        effective_readiness = (
            {
                **readiness,
                "ready": True,
                "blockers": [],
                "entity_binding_bypassed": True,
            }
            if agent_binding_bypassed
            else readiness
        )
        return {
            "phase": "development_readiness_gate",
            "status": "completed",
            "development_readiness": effective_readiness,
            "clarification": {},
            "timeline": ["development_readiness_gate"],
        }
    missing = readiness["missing_entities"]
    if target_type == "agent" and any(
        isinstance(item, dict) and item.get("type") != "entity_source_binding"
        for item in blockers
    ):
        messages = [
            str(item.get("message") or "")
            for item in blockers
            if isinstance(item, dict) and str(item.get("message") or "").strip()
        ]
        clarification = build_ask_user_payload(
            [
                AskUserQuestion(
                    header="智能体开发前置",
                    question="当前 Agent Contract、模板或页面入口尚未就绪，请先修复正式规划或模板。",
                    type="text",
                    placeholder="修复并重新确认正式产物后，再次开始开发智能体。",
                )
            ]
        )
        clarification.update(
            {
                "mode": "agent_development_not_ready",
                "status": "requires_user_input",
                "message": "智能体开发前置条件未满足。",
                "blockers": blockers,
                "errors": messages,
                "development_target": {
                    "type": "agent",
                    "id": target_id,
                    "label": _agent_label(project_plan, target_id),
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
    # 门禁保存可展示的原目标名称，独立实体运行结束时无需靠当前大纲选择补齐。
    target_label = target_id
    if target_type == "page":
        page = next((item for item in project_plan_page_records(project_plan)
                     if str(item.get("pageId") or item.get("id") or "") == target_id), {})
        target_label = str(page.get("name") or page.get("label") or target_id)
    elif target_type == "endpoint":
        for contract in project_plan.get("api_contracts") or []:
            if str(contract.get("id") or "") != str(readiness.get("api_contract_id") or ""):
                continue
            endpoint = next((item for item in contract.get("endpoints") or []
                             if str(item.get("id") or "") == target_id), {})
            target_label = f"{endpoint.get('method') or 'API'} {endpoint.get('path') or target_id}"
    else:
        target_label = _agent_label(project_plan, target_id)
    labels = "、".join(
        str(item.get("entity_name") or item.get("entity_id") or "") for item in missing
    )
    clarification = build_ask_user_payload(
        [
            AskUserQuestion(
                header="实体绑定前置",
                question=(
                    f"当前目标依赖实体 {labels}，尚未完成 EntitySourceBinding。"
                    "请在当前会话进入对应实体的数据源绑定；确认后通过续接卡恢复当前开发目标。"
                ),
                type="text",
                placeholder="完成实体数据源绑定后，通过续接卡继续开发。",
            )
        ]
    )
    clarification.update(
        {
            "mode": "entity_source_binding_required",
            "status": "requires_user_input",
            "message": "存在未完成的数据源绑定实体，当前开发目标已暂停。",
            "missing_entities": missing,
            "can_skip_agent_entity_binding": (
                target_type == "agent"
                and bool(blockers)
                and all(
                    isinstance(item, dict)
                    and item.get("type") == "entity_source_binding"
                    for item in blockers
                )
            ),
            "development_target": {
                "type": target_type,
                "id": target_id,
                "label": target_label,
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


def _agent_label(project_plan: dict, agent_id: str) -> str:
    """从当前完整 Contract 获取 Agent 展示名称。"""

    for contract in project_plan.get("agent_contracts") or []:
        if not isinstance(contract, dict) or str(contract.get("agentId") or "") != agent_id:
            continue
        identity = contract.get("identity") if isinstance(contract.get("identity"), dict) else {}
        return str(identity.get("name") or agent_id)
    return agent_id
