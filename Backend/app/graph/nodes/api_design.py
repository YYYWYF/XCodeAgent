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
    if state.get("completed_development_analysis") is True:
        # 正式修订续接只分析已完成目标，不进入映射门禁或任何任务生成/Build。
        from app.services.completed_development_analysis import analyze_completed_development

        summary = analyze_completed_development(workspace, change_id=str(state.get("change_id") or ""))
        message = (
            f"已检查 {summary['checked']} 个已完成页面/API，{summary['changed']} 个有变更"
            f"，{summary['unavailable']} 个暂无法判断。你想开发哪个页面或 API？"
        )
        return {
            **development_target_selection({}),
            "completed_development_analysis": False,
            "clarification": {"mode": "development_target_selection", "message": message},
            "message": message,
        }
    project_plan = state.get("project_plan")
    if not workspace or not isinstance(project_plan, dict):
        raise ApiDesignError("缺少工作区或已确认 TechnicalPlan，无法检查 API 设计。")
    action = state.get("api_design_gate_action")
    action = action if isinstance(action, dict) else {}
    scope = state.get("build_execution_scope") or {}
    state_target_type = (
        "agent" if scope.get("type") == "agent"
        else "endpoint" if str(state.get("selected_endpoint_id") or "").strip()
        else "page" if str(state.get("selectedPageId") or "").strip()
        else "application"
    )
    state_target_id = (
        str(scope.get("targetId") or "") if state_target_type == "agent"
        else str(state.get("selected_endpoint_id") or "").strip()
        if state_target_type == "endpoint"
        else "application" if state_target_type == "application"
        else str(state.get("selectedPageId") or "").strip()
    )
    scope = state.get("build_execution_scope") or {}
    if scope.get("type") == "agent":
        state_target_type, state_target_id = "agent", str(scope.get("targetId") or "")
    action_target_type = str(action.get("targetType") or "").strip()
    target_type = action_target_type if action_target_type in {"application", "page", "endpoint", "agent"} else (
        state_target_type
    )
    target_id = str(action.get("targetId") or "").strip() or state_target_id
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
    readiness = api_design_readiness(
        workspace,
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=api_contract_id,
    )
    # 当前磁盘映射有效时直接通过；无具体目标只完成配置检查，不授权全量 DAG。
    if readiness["ready"]:
        if target_type == "application":
            return development_target_selection(readiness)
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
    labels = "、".join(f"{item.get('method')} {item.get('path')}" for item in missing)
    question = f"当前目标依赖的 API 尚未完成设计：{labels}。请分别配置后重新检测。"
    message = "部分接口配置需要重新确认，请检查并保存后继续。"
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


def development_target_selection(readiness: dict[str, Any]) -> dict[str, Any]:
    """结束无具体目标的配置交接，交给已有页面/API入口逐个启动开发。"""

    return {
        "phase": "api_design_readiness_gate",
        "status": "completed",
        "api_design_gate_action": {},
        "api_design_readiness": readiness,
        "clarification": {
            "mode": "development_target_selection",
            "message": "API 字段映射检测已通过，你想开发哪个页面或 API？",
        },
        "message": "API 字段映射检测已通过，请选择一个页面或 API 开始开发。",
        "timeline": ["api_design_readiness_gate"],
    }


def _development_target_label(
    project_plan: dict[str, Any],
    target_type: str,
    target_id: str,
) -> str:
    """读取开发目标展示名称，缺失时回退到稳定 ID。"""

    if target_type == "agent":
        from app.services.runtime_agent_contracts import agent_contract_index
        return agent_contract_index(project_plan)[target_id]["name"]
    if target_type == "application":
        return "整个应用"
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
