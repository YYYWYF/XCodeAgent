"""Endpoint API 字段映射与开发就绪检查节点。"""

from __future__ import annotations

from typing import Any

from app.graph.state import ProjectState
from app.services.api_design import (
    ApiDesignError,
    api_design_gate_result,
    api_design_readiness,
)
from app.services.frontend_page_tree import project_plan_page_records
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.topologies.queries import serves_agent_runtime_public_edge


def api_design_readiness_gate(state: ProjectState) -> dict[str, Any]:
    """在页面或接口开发前检查全部目标映射，并等待用户确认当前版本。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    project_plan = state.get("project_plan")
    if not workspace or not isinstance(project_plan, dict):
        raise ApiDesignError("缺少工作区或已确认 TechnicalPlan，无法检查 API 设计。")
    if serves_agent_runtime_public_edge(project_plan):
        # Direct Runtime 直接按 TechnicalPlan API Contract/Schema 生成 DTO、Service 与
        # FastAPI Endpoint，不建立数据库/外部来源字段映射，也不进入配置确认交互。
        return {
            "phase": "api_design_readiness_gate",
            "status": "completed",
            "api_design_gate_action": {},
            "api_design_readiness": {
                "ready": True,
                "target_type": "direct_runtime",
                "target_id": "agent-runtime",
                "api_contract_id": None,
                "endpoint_ids": [],
                "missing_api_designs": [],
                "bypassed_by_topology": "agent_runtime_direct",
            },
            "api_design_result": {},
            "clarification": {},
            "timeline": ["api_design_readiness_gate"],
        }
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
    readiness = api_design_readiness(
        workspace,
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=api_contract_id,
    )
    action_name = str(action.get("action") or "")
    if readiness["ready"] and not readiness["endpoint_ids"]:
        return {
            "phase": "api_design_readiness_gate",
            "status": "completed",
            "api_design_gate_action": {},
            "api_design_readiness": readiness,
            "api_design_result": {},
            "clarification": {},
            "timeline": ["api_design_readiness_gate"],
        }
    target_label = _development_target_label(project_plan, target_type, target_id)
    if readiness["ready"]:
        result = api_design_gate_result(
            workspace,
            project_plan,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            api_contract_id=api_contract_id,
        )
        if action_name == "confirm" and _gate_versions_match(action, result):
            confirmed_result = {**result, "status": "confirmed", "confirmedForDevelopment": True}
            return {
                "phase": "api_design_readiness_gate",
                "status": "completed",
                "api_design_gate_action": {},
                "api_design_readiness": readiness,
                "api_design_result": confirmed_result,
                "clarification": {},
                "message": "API 映射已确认，正在继续当前开发流程。",
                "timeline": ["api_design_readiness_gate"],
            }
        version_changed = action_name == "confirm"
        return {
            "phase": "api_design_readiness_gate",
            "status": "requires_user_input",
            "api_design_gate_action": {},
            "api_design_readiness": readiness,
            "api_design_result": result,
            "clarification": {
                "mode": "api_design_confirmation",
                "status": "requires_user_input",
                "message": (
                    "API 映射版本已变化，请核对当前结果后重新确认。"
                    if version_changed
                    else "字段映射检测已通过，请确认本次开发使用当前版本。"
                ),
                "apiDesignResult": result,
                "developmentTarget": {
                    "type": target_type,
                    "id": target_id,
                    "label": target_label,
                    "apiContractId": api_contract_id,
                },
            },
            "timeline": ["api_design_readiness_gate"],
        }
    missing = readiness["missing_api_designs"]
    message, question = _missing_mapping_copy(missing)
    clarification = build_ask_user_payload(
        [
            AskUserQuestion(
                header="字段映射前置",
                question=question,
                type="text",
                placeholder="请通过门禁卡片配置映射并保存，全部完成后点击确认。",
            )
        ]
    )
    clarification.update(
        {
            "mode": "api_design_required",
            "status": "requires_user_input",
            "message": message,
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
        "api_design_result": {},
        "clarification": clarification,
        "timeline": ["api_design_readiness_gate"],
    }


def _missing_mapping_copy(missing: list[Any]) -> tuple[str, str]:
    """按 pending/stale 生成门禁文案，避免把尚未配置说成设计失效。"""

    statuses = {
        str(item.get("status") or "pending")
        for item in missing
        if isinstance(item, dict)
    }
    labels = "、".join(
        f"{item.get('method')} {item.get('path')}"
        for item in missing
        if isinstance(item, dict)
    )
    if statuses <= {"pending"}:
        return (
            "当前开发目标还缺字段映射，开发已暂停。",
            (
                f"当前目标依赖的接口尚未完成字段映射：{labels}。"
                "请逐项配置并保存，全部完成后点击确认统一检测。"
            ),
        )
    if statuses <= {"stale"}:
        return (
            "当前开发目标的字段映射已失效，需要重新配置后继续开发。",
            (
                f"当前目标依赖的接口字段映射已失效：{labels}。"
                "请逐项重新配置并保存，全部完成后点击确认统一检测。"
            ),
        )
    return (
        "存在未完成或已失效的字段映射，当前开发目标已暂停。",
        (
            f"当前目标依赖的接口字段映射未完成或已失效：{labels}。"
            "请逐项配置并保存，全部完成后点击确认统一检测。"
        ),
    )


def _gate_versions_match(action: dict[str, Any], result: dict[str, Any]) -> bool:
    """比较用户确认的全部映射版本与本次重新读取结果，拒绝遗漏、重复或变更。"""

    expected = {
        (
            str(item.get("apiContractId") or ""),
            str(item.get("endpointId") or ""),
            str(item.get("artifactRevision") or ""),
        )
        for item in action.get("versions") or []
        if isinstance(item, dict)
    }
    actual = {
        (
            str(item.get("apiContractId") or ""),
            str(item.get("endpointId") or ""),
            str(item.get("artifactRevision") or ""),
        )
        for item in result.get("designs") or []
        if isinstance(item, dict)
    }
    return len(expected) == len(action.get("versions") or []) and expected == actual


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
