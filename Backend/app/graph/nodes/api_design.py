"""Endpoint API 字段映射与开发就绪检查节点。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.graph.state import ProjectState
from app.services.api_design import (
    ApiDesignError,
    api_design_readiness,
    confirm_api_design,
    initial_api_design_payload,
)
from app.services.artifact_invalidation import mark_artifact_document_stale
from app.services.frontend_page_tree import project_plan_page_records
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload


def api_design(state: ProjectState) -> dict[str, Any]:
    """按 Endpoint 运行 API 字段映射交互，确认后续接完整开发链路。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    project_plan = state.get("project_plan")
    api_contract_id = str(state.get("selected_api_contract_id") or "").strip()
    endpoint_id = str(state.get("selected_endpoint_id") or "").strip()
    if not workspace:
        raise ApiDesignError("API 设计缺少工作区路径。")
    if not isinstance(project_plan, dict):
        raise ApiDesignError("缺少已确认 TechnicalPlan，无法设计 API。")
    if not api_contract_id or not endpoint_id:
        raise ApiDesignError("API 设计必须提供完整的 API Contract 和 Endpoint 标识。")

    action = state.get("api_design_action")
    action = action if isinstance(action, dict) else {}
    if action:
        if (
            str(action.get("apiContractId") or "") != api_contract_id
            or str(action.get("endpointId") or "") != endpoint_id
        ):
            raise ApiDesignError("API 设计动作与当前 Endpoint 不一致。")
        action_name = str(action.get("action") or "")
        if action_name == "confirm":
            result = confirm_api_design(workspace, project_plan, action)
            _invalidate_build_task_plan(workspace)
            return {
                "phase": "api_design",
                "status": "completed",
                "api_design_action": {},
                "api_design_result": result,
                "api_design_draft": result.get("design", {}),
                "clarification": {},
                "message": "API 设计已确认，正在继续当前 Endpoint 的 API 开发流程。",
                "timeline": ["api_design"],
            }

    payload = initial_api_design_payload(
        workspace,
        project_plan,
        api_contract_id,
        endpoint_id,
    )
    if action.get("draft") and isinstance(action.get("draft"), dict):
        payload["draft"] = action["draft"]
    clarification = build_ask_user_payload(
        [
            AskUserQuestion(
                header="API 字段映射",
                question="请为当前 Endpoint 配置请求与返回映射，并确认 API 设计。",
                type="text",
                placeholder="请在 API 设计面板中完成字段映射配置。",
            )
        ]
    )
    clarification.update(
        {
            "mode": "api_design",
            "status": "requires_user_input",
            "message": "API 设计草稿已准备，请完成字段映射后确认。",
            "apiDesign": payload,
        }
    )
    return {
        "phase": "api_design",
        "status": "requires_user_input",
        "api_design_action": {},
        "api_design_draft": payload.get("draft", {}),
        "clarification": clarification,
        "timeline": ["api_design"],
    }


def api_design_readiness_gate(state: ProjectState) -> dict[str, Any]:
    """在页面或 API 开发前检查关联 Endpoint 的当前版设计是否全部确认。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    project_plan = state.get("project_plan")
    if not workspace or not isinstance(project_plan, dict):
        raise ApiDesignError("缺少工作区或已确认 TechnicalPlan，无法检查 API 设计。")
    target_type = "endpoint" if str(state.get("selected_endpoint_id") or "").strip() else "page"
    target_id = (
        str(state.get("selected_endpoint_id") or "").strip()
        if target_type == "endpoint"
        else str(state.get("selectedPageId") or "").strip()
    )
    if not target_id:
        raise ApiDesignError("请选择要开始开发的页面或 API。")
    readiness = api_design_readiness(
        workspace,
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=str(state.get("selected_api_contract_id") or "").strip() or None,
    )
    if readiness["ready"]:
        return {
            "phase": "api_design_readiness_gate",
            "status": "completed",
            "api_design_readiness": readiness,
            "clarification": {},
            "timeline": ["api_design_readiness_gate"],
        }
    target_label = _development_target_label(project_plan, target_type, target_id)
    missing = readiness["missing_api_designs"]
    labels = "、".join(
        f"{item.get('method')} {item.get('path')}" for item in missing
    )
    clarification = build_ask_user_payload(
        [
            AskUserQuestion(
                header="API 设计前置",
                question=f"当前目标依赖的 API 尚未完成设计：{labels}。请分别完成设计后重新发起开发。",
                type="text",
                placeholder="请从应用大纲进入对应 API 的设计。",
            )
        ]
    )
    clarification.update(
        {
            "mode": "api_design_required",
            "status": "requires_user_input",
            "message": "存在未完成或已失效的 API 设计，当前开发目标已暂停。",
            "missingApiDesigns": missing,
            "developmentTarget": {
                "type": target_type,
                "id": target_id,
                "label": target_label,
                "apiContractId": readiness.get("api_contract_id"),
            },
        }
    )
    return {
        "phase": "api_design_readiness_gate",
        "status": "requires_user_input",
        "api_design_readiness": readiness,
        "clarification": clarification,
        "timeline": ["api_design_readiness_gate"],
    }


def _invalidate_build_task_plan(workspace: str) -> None:
    """API 设计确认后将既有 Build DAG 标记为失效，避免复用旧绑定上下文。"""

    path = Path(workspace).expanduser() / ".xcodeagent" / "plans" / "build-task-plan.json"
    if path.is_file():
        mark_artifact_document_stale(path)


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
