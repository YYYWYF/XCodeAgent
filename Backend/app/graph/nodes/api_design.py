"""Endpoint API 字段映射与开发就绪检查节点。"""

from __future__ import annotations

from typing import Any

from app.graph.state import ProjectState
from app.services.api_design import (
    ApiDesignError,
    api_design_readiness,
)
from app.services.frontend_page_tree import project_plan_page_records
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload


def api_design_readiness_gate(state: ProjectState) -> dict[str, Any]:
    """在页面或接口开发前检查全部目标映射，通过后直接继续开发流程。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    project_plan = state.get("project_plan")
    if not workspace or not isinstance(project_plan, dict):
        raise ApiDesignError("缺少工作区或已确认 TechnicalPlan，无法检查 API 设计。")
    action = state.get("api_design_gate_action")
    action = action if isinstance(action, dict) else {}
    state_target_type = (
        "endpoint" if str(state.get("selected_endpoint_id") or "").strip() else "page"
    )
    state_target_id = (
        str(state.get("selected_endpoint_id") or "").strip()
        if state_target_type == "endpoint"
        else str(state.get("selectedPageId") or "").strip()
    )
    action_target_type = str(action.get("targetType") or "").strip()
    target_type = action_target_type if action_target_type in {"page", "endpoint"} else (
        state_target_type
    )
    target_id = str(action.get("targetId") or "").strip() or (
        str(state.get("selected_endpoint_id") or "").strip()
        if target_type == "endpoint"
        else str(state.get("selectedPageId") or "").strip()
    )
    if not target_id:
        raise ApiDesignError("请选择要开始开发的页面或 API。")
    if action and state_target_id and (target_type != state_target_type or target_id != state_target_id):
        raise ApiDesignError("API 映射门禁动作与原开发目标不一致。")
    api_contract_id = str(action.get("apiContractId") or "").strip() or (
        str(state.get("selected_api_contract_id") or "").strip() or None
    )
    state_contract_id = str(state.get("selected_api_contract_id") or "").strip()
    if target_type == "endpoint" and state_contract_id and api_contract_id != state_contract_id:
        raise ApiDesignError("API 映射门禁动作与原 API Contract 不一致。")
    if action and str(action.get("action") or "") != "refresh":
        raise ApiDesignError("API 映射门禁仅支持重新检测当前字段映射。")
    if target_type == "endpoint":
        agent = _gateway_owner(project_plan, api_contract_id, target_id)
        if agent is not None:
            agent_id = str(agent.get("agentId") or "").strip()
            identity = agent.get("identity") if isinstance(agent.get("identity"), dict) else {}
            agent_label = str(identity.get("name") or agent_id).strip()
            message = f"此接口是智能体「{agent_label}」的 Gateway 接口，请先进入智能体开发。"
            return {
                "phase": "api_design_readiness_gate",
                "status": "requires_user_input",
                "api_design_gate_action": {},
                "api_design_readiness": {},
                "api_design_result": {},
                "clarification": {
                    "mode": "agent_gateway_dependency_required",
                    "status": "requires_user_input",
                    "message": message,
                    "agentId": agent_id,
                    "agentLabel": agent_label,
                    "developmentTarget": {
                        "type": "endpoint",
                        "id": target_id,
                        "apiContractId": api_contract_id,
                    },
                },
                "timeline": ["api_design_readiness_gate"],
            }
    readiness = api_design_readiness(
        workspace,
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=api_contract_id,
    )
    # 首次进入时即使全部 Endpoint 已完成，也要展示门禁卡供用户查看或修改；
    # 只有用户明确提交 refresh 后，检测通过才允许继续后续开发节点。
    has_endpoint_designs = bool(readiness["api_designs"])
    is_refresh = str(action.get("action") or "") == "refresh"
    if (not has_endpoint_designs) or (readiness["ready"] and is_refresh):
        return {
            "phase": "api_design_readiness_gate",
            "status": "completed",
            "api_design_gate_action": {},
            "api_design_readiness": readiness,
            "clarification": {},
            "message": "API 字段映射检测已通过，正在继续当前开发流程。",
            "timeline": ["api_design_readiness_gate"],
        }
    target_label = _development_target_label(project_plan, target_type, target_id)
    missing = readiness["missing_api_designs"]
    all_ready = readiness["ready"]
    labels = "、".join(f"{item.get('method')} {item.get('path')}" for item in missing)
    if all_ready:
        question = "当前目标关联的 API 字段映射已全部准备，可查看或修改；确认后将重新检测并继续开发。"
        message = "当前目标的字段映射已准备，请确认检测后继续开发。"
        placeholder = "可先查看或修改右侧字段映射，完成后点击确认并检测。"
    else:
        question = f"当前目标依赖的 API 尚未完成设计：{labels}。请分别配置后重新检测。"
        message = "存在未完成或已失效的 API 设计，当前开发目标已暂停。"
        placeholder = "请通过门禁卡片配置映射并重新检测。"
    clarification = build_ask_user_payload(
        [
            AskUserQuestion(
                header="API 设计前置",
                question=question,
                type="text",
                placeholder=placeholder,
            )
        ]
    )
    clarification.update(
        {
            "mode": "api_design_required",
            "status": "requires_user_input",
            "message": message,
            "apiDesigns": readiness["api_designs"],
            "missingApiDesigns": missing,
            "developmentTarget": {
                "type": target_type,
                "id": target_id,
                "label": target_label,
                "apiContractId": api_contract_id,
            },
        }
    )
    return {
        "phase": "api_design_readiness_gate",
        "status": "requires_user_input",
        "api_design_gate_action": {},
        "api_design_readiness": readiness,
        "clarification": clarification,
        "timeline": ["api_design_readiness_gate"],
    }


def _gateway_owner(
    project_plan: dict[str, Any], api_contract_id: str | None, endpoint_id: str
) -> dict[str, Any] | None:
    """仅在当前 TechnicalPlan 中唯一绑定的真实 Gateway Endpoint 上识别智能体。"""

    contracts = project_plan.get("api_contracts")
    contracts = contracts if isinstance(contracts, list) else []
    endpoint_matches = [
        endpoint
        for contract in contracts
        if isinstance(contract, dict) and str(contract.get("id") or "").strip() == api_contract_id
        for endpoint in (contract.get("endpoints") or [])
        if isinstance(endpoint, dict) and str(endpoint.get("id") or "").strip() == endpoint_id
    ]
    if len(endpoint_matches) != 1:
        return None
    agents = project_plan.get("agent_contracts")
    agents = agents if isinstance(agents, list) else []
    owners = [
        agent
        for agent in agents
        if isinstance(agent, dict)
        and str(agent.get("agentId") or "").strip()
        and isinstance(agent.get("invocation"), dict)
        and str(agent["invocation"].get("gatewayEndpointId") or "").strip() == endpoint_id
    ]
    return owners[0] if len(owners) == 1 else None


def _development_target_label(
    project_plan: dict[str, Any],
    target_type: str,
    target_id: str,
) -> str:
    """读取开发目标展示名称，缺失时回退到稳定 ID。"""

    if target_type == "page":
        page = next(
            (
                item
                for item in project_plan_page_records(project_plan)
                if str(item.get("pageId") or item.get("id") or "") == target_id
            ),
            {},
        )
        return str(page.get("name") or page.get("label") or target_id)
    for contract in project_plan.get("api_contracts") or []:
        if not isinstance(contract, dict):
            continue
        for endpoint in contract.get("endpoints") or []:
            if isinstance(endpoint, dict) and str(endpoint.get("id") or "") == target_id:
                return f"{endpoint.get('method') or 'API'} {endpoint.get('path') or target_id}"
    return target_id
