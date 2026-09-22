"""编译 Agent UI 开发阶段的固定组件、Mock 与交付边界合同。"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.ui_design_agent_template import build_agent_ui_template_config
from app.topologies import serves_agent_runtime_public_edge


AGENT_UI_BUILD_CONTRACT_VERSION = "agent-ui-build.v1"
AGENT_UI_COMPONENT_MODULE = "@/components/AgentConversation"
AGENT_UI_CONFIG_TYPE_MODULE = "@/typings/agentConversation"
AGENT_UI_MOCK_ADAPTER_PATH = "frontend/src/apis/agentConversationMock.ts"
AGENT_UI_SKILL_PATH = "/.xcodeagent/builtin-skills/agent-ui-surface-template/SKILL.md"

_COMPONENTS = {
    "standalone_page": (
        "AgentConversationPage",
        "frontend/src/components/AgentConversation/AgentConversationPage.tsx",
    ),
    "floating_panel": (
        "AgentFloatingPanel",
        "frontend/src/components/AgentConversation/AgentFloatingPanel.tsx",
    ),
}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从数组中仅保留对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    """把任意值规范为去空白字符串。"""

    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    """为平台编译的 Agent UI 合同生成稳定摘要。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _gateway_endpoint_by_agent(technical_plan: dict[str, Any]) -> dict[str, str]:
    """按 Agent ID 提取 TechnicalPlan 中唯一的 Gateway Endpoint 引用。"""

    result: dict[str, str] = {}
    for contract in _dict_items(technical_plan.get("agent_contracts")):
        agent_id = _text(contract.get("agentId"))
        invocation = contract.get("invocation")
        invocation = invocation if isinstance(invocation, dict) else {}
        endpoint_id = _text(invocation.get("gatewayEndpointId"))
        if agent_id and endpoint_id:
            if agent_id in result and result[agent_id] != endpoint_id:
                raise ValueError(f"Agent {agent_id} 存在多个 Gateway Endpoint。")
            result[agent_id] = endpoint_id
    return result


def project_agent_ui_build_contracts(
    product_plan: dict[str, Any],
    technical_plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """从已确认 ProductPlan 与 TechnicalPlan 投影逐页 Mock 组合合同。"""

    # 公开入口由 Agent Runtime 自身承担时，前端直连 Public Edge，不再走 Gateway Mock。
    direct = serves_agent_runtime_public_edge(technical_plan)
    gateway_by_agent = _gateway_endpoint_by_agent(technical_plan)
    invocation_by_agent = {
        _text(contract.get("agentId")): dict(contract.get("invocation"))
        for contract in _dict_items(technical_plan.get("agent_contracts"))
        if _text(contract.get("agentId")) and isinstance(contract.get("invocation"), dict)
    }
    result: dict[str, dict[str, Any]] = {}
    for page in project_ui_design_pages(product_plan):
        page_id = _text(page.get("pageId"))
        surfaces = _dict_items(page.get("agent_surfaces"))
        if not page_id or not surfaces:
            continue
        config = build_agent_ui_template_config(page)
        agent_id = _text(config.get("agentId"))
        gateway_endpoint_id = gateway_by_agent.get(agent_id, "")
        invocation = invocation_by_agent.get(agent_id, {})
        public_path = _text(invocation.get("path"))
        if direct and not public_path:
            raise ValueError(f"Agent {agent_id} 缺少 Runtime Public Edge path。")
        if not direct and not gateway_endpoint_id:
            raise ValueError(f"Agent {agent_id} 缺少 TechnicalPlan Gateway Endpoint。")
        surface = _text(config.get("surface"))
        component = _COMPONENTS.get(surface)
        if component is None:
            raise ValueError(f"Agent UI Surface 类型无效：{surface}。")
        payload = {
            "version": AGENT_UI_BUILD_CONTRACT_VERSION,
            "mode": "direct" if direct else "mock",
            "pageId": page_id,
            "agentId": agent_id,
            "surface": surface,
            "componentModule": AGENT_UI_COMPONENT_MODULE,
            "component": component[0],
            "componentPath": component[1],
            "configTypeModule": AGENT_UI_CONFIG_TYPE_MODULE,
            "mockAdapterPath": AGENT_UI_MOCK_ADAPTER_PATH,
            "skillPath": AGENT_UI_SKILL_PATH,
            "gatewayEndpointId": gateway_endpoint_id,
            "publicPath": public_path or None,
            "serviceId": _text(invocation.get("serviceId")) or None,
            "mockExemptEndpointIds": [] if direct else [gateway_endpoint_id],
            "config": config,
        }
        result[page_id] = {**payload, "sha256": _stable_hash(payload)}
    return result


def agent_ui_contract_from_source_refs(
    source_refs: Any,
    *,
    page_id: str = "",
) -> dict[str, Any]:
    """从 Unit 来源中读取直接合同，或按 pageId 选择应用级合同。"""

    refs = source_refs if isinstance(source_refs, dict) else {}
    direct = refs.get("agent_ui")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    by_page = refs.get("agent_ui_by_page")
    if isinstance(by_page, dict):
        selected = by_page.get(page_id)
        if isinstance(selected, dict):
            return dict(selected)
    return {}


def task_agent_ui_contract(task: dict[str, Any]) -> dict[str, Any]:
    """读取任务上由平台注入的 Agent UI Mock 合同。"""

    return agent_ui_contract_from_source_refs(task.get("source_refs"))


def apply_agent_ui_delivery_boundary(
    build_summary: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """把含 Agent UI Mock 的完成态收敛为真实集成待办态。"""

    contracts = [task_agent_ui_contract(task) for task in tasks]
    contracts = [contract for contract in contracts if contract.get("mode") == "mock"]
    if not contracts or build_summary.get("status") != "completed":
        return dict(build_summary)
    gateway_endpoint_ids = list(
        dict.fromkeys(
            _text(contract.get("gatewayEndpointId"))
            for contract in contracts
            if _text(contract.get("gatewayEndpointId"))
        )
    )
    return {
        **build_summary,
        "status": "mock_completed",
        "delivery_boundary": {
            "agent_ui": "mock_completed",
            "real_integration": "pending",
            "gateway_endpoint_ids": gateway_endpoint_ids,
            "message": "Agent UI Mock 已完成，可用于页面预览；真实 Gateway/Runtime 集成仍待完成。",
        },
    }


def agent_ui_integration_pending(state: dict[str, Any]) -> bool:
    """从服务端任务合同或构建摘要判断真实 Agent UI 集成是否仍待完成。"""

    tasks = _dict_items(state.get("tasks"))
    if any(task_agent_ui_contract(task).get("mode") == "mock" for task in tasks):
        return True
    summary = state.get("build_summary")
    summary = summary if isinstance(summary, dict) else {}
    boundary = summary.get("delivery_boundary")
    return isinstance(boundary, dict) and boundary.get("real_integration") == "pending"


def agent_ui_integration_pending_result() -> dict[str, Any]:
    """生成无确认动作的真实集成待办状态，阻止继续进入 Launch/Acceptance。"""

    message = "Agent UI Mock 已完成，可用于页面预览；真实 Gateway/Runtime 集成仍待完成。"
    return {
        "phase": "build",
        "status": "requires_user_input",
        "accepted": False,
        "message": message,
        "clarification": {
            "mode": "agent_ui_integration_pending",
            "status": "requires_user_input",
            "message": message,
            "questions": [],
            "actionValues": [],
        },
        "timeline": ["build"],
    }
