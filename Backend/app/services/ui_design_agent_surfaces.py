"""投影当前产品 Surface，并隔离单文件与共享工程的设计来源。"""

from __future__ import annotations

from typing import Any, Iterable

from app.services.product_plan import project_active_agent_product_plan, requirement_spec_sha256
from app.services.agent_action_bindings import agent_action_bindings


_REQUIRED_PARTS = {
    "standalone_page": ("messages", "status", "composer"),
    "floating_panel": ("launcher", "panel"),
}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从列表中只保留 JSON 对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_items(value: Any) -> list[str]:
    """从数组中提取去空白、去重且保序的真实字符串。"""

    result: list[str] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def expected_agent_surfaces(page: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 UiDesign 单页输入中由 ProductPlan 投影的 Agent Surface。"""

    return [
        {
            "agentId": str(surface.get("agentId") or "").strip(),
            "type": str(surface.get("type") or "").strip(),
            "actionIds": _string_items(surface.get("actionIds")),
            "contextItemIds": _string_items(surface.get("contextItemIds")),
        }
        for surface in _dict_items(page.get("agent_surfaces"))
    ]


def project_ui_design_pages(product_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """按 pageId 把 Agent 事实与页面绑定投影为 UiDesign 单页输入。"""

    product_plan = project_active_agent_product_plan(product_plan)
    action_owners = agent_action_bindings(product_plan)
    surfaces_by_page: dict[str, list[dict[str, Any]]] = {}
    for agent in _dict_items(product_plan.get("agents")):
        agent_id = str(agent.get("agentId") or "").strip()
        agent_name = str(agent.get("name") or agent_id).strip()
        purpose = str(agent.get("purpose") or agent.get("responsibility") or "").strip()
        interaction_mode = str(agent.get("interactionMode") or "").strip()
        capabilities = _dict_items(agent.get("capabilities"))
        for binding in _dict_items(agent.get("pageActionBindings")):
            page_id = str(binding.get("pageId") or "").strip()
            surface = binding.get("surface") if isinstance(binding.get("surface"), dict) else {}
            if not page_id:
                continue
            surfaces_by_page.setdefault(page_id, []).append(
                {
                    "agentId": agent_id,
                    "type": str(surface.get("type") or "").strip(),
                    "actionIds": list(dict.fromkeys([*_string_items(binding.get("actionIds")),
                        *(action_id for action_id, owner in action_owners.get(page_id, {}).items() if owner == agent_id)])),
                    "contextItemIds": _string_items(surface.get("contextItemIds")),
                    "name": agent_name,
                    "purpose": purpose,
                    "interactionMode": interaction_mode,
                    "capabilities": capabilities,
                }
            )
    return [
        {
            **page,
            "agent_surfaces": list(
                surfaces_by_page.get(str(page.get("pageId") or "").strip(), [])
            ),
        }
        for page in _dict_items(product_plan.get("pages"))
    ]


def project_agent_design_pages(
    requirement_spec: dict[str, Any], product_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """为包消费原型读取已联合确认的单 Agent 页面事实，不消费历史设计稿。"""
    if any(document.get("confirmation_status") != "confirmed"
           for document in (requirement_spec, product_plan)):
        raise ValueError("请先联合确认当前 RequirementSpec 和 ProductPlan。")
    if product_plan.get("requirement_spec_sha256") != requirement_spec_sha256(requirement_spec):
        raise ValueError("ProductPlan 与当前已确认 RequirementSpec 摘要不一致。")
    # 普通页面仍由原生成池处理；独立生产者只拥有含 Agent Surface 的页面。
    pages = [page for page in project_ui_design_pages(product_plan) if page.get("agent_surfaces")]
    page_ids = [str(page.get("pageId") or "").strip() for page in pages]
    if not pages or any(not value for value in page_ids) or len(set(page_ids)) != len(pages):
        raise ValueError("包消费设计需要非空且唯一的正式页面身份。")
    agent_ids: set[str] = set()
    for page in pages:
        surfaces = expected_agent_surfaces(page)
        if len(surfaces) > 1:
            raise ValueError("当前包消费试点每页最多支持一个 Agent Surface。")
        actions = {str(action.get("actionId") or "") for action in _dict_items(page.get("actions"))}
        items = {str(item.get("itemId") or "") for item in _dict_items(page.get("information_items"))}
        for surface in surfaces:
            if (not surface["agentId"] or surface["type"] not in _REQUIRED_PARTS
                    or not surface["actionIds"] or not set(surface["actionIds"]) <= actions
                    or not set(surface["contextItemIds"]) <= items):
                raise ValueError("Agent Surface 身份、类型或页面绑定与正式产品事实不一致。")
            agent_ids.add(surface["agentId"])
    if len(agent_ids) != 1:
        raise ValueError("当前包消费试点必须且只能包含一个启用的 Agent。")
    return pages


def inspect_agent_surface_bindings(
    _tags: Iterable[tuple[str, str, bool, bool]], code: str = ""
) -> dict[str, Any]:
    """单文件稿只检查源码边界，不从旧组件或配置推导会话证据。"""
    forbidden = ("@xcodeagent/agent-ui-design", "AgentConversationTemplate",
                 "AgentFloatingPanelTemplate", "AgentConversationAdapter", "AgentChatCore",
                 "data-agent-part")
    errors = ["旧 Agent UI 固定模板已退出当前设计，请重新生成包消费工程。"] if any(
        marker in code for marker in forbidden) else []
    return {"surfaces": [], "invalid_markers": errors}


def validate_agent_surface_bindings(
    page: dict[str, Any], inspection: dict[str, Any]
) -> dict[str, Any]:
    """含 Agent 的正式页面必须由多文件 owner 确认，单文件不能自证共享来源。"""
    errors = list(inspection.get("invalid_markers") or [])
    if expected_agent_surfaces(page):
        errors.append("Agent UI 页面必须使用隔离多文件设计工程并重新确认。")
    return {"errors": errors, "bindings_passed": not errors, "required_parts_passed": not errors}


def manifest_agent_surface_bindings(
    page: dict[str, Any], _inspection: dict[str, Any]
) -> list[dict[str, Any]]:
    """只投影产品绑定；共享源码、实际包和确认凭据由多文件 owner 持有。"""
    return expected_agent_surfaces(page)
