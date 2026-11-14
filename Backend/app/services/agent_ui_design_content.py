"""区分共享会话与页面正文，独立页仍可承载导航和普通操作。"""

from typing import Any

from app.services.agent_product_actions import AGENT_OPERATIONS


def agent_design_owned_action_ids(page: dict[str, Any]) -> set[str]:
    """按同页 Agent 身份和明确操作语义认领控件，不从按钮文案推断归属。"""
    surfaces = page.get('agent_surfaces', [])
    agents = {surface['agentId'] for surface in surfaces}
    owned = {action_id for surface in surfaces for action_id in surface['actionIds']}
    for action in page.get('actions', []):
        behavior = action.get('behavior') or {}
        operation = behavior.get('operation')
        if (behavior.get('type') == 'agent' and behavior.get('agentId') in agents
                and isinstance(operation, str) and operation in AGENT_OPERATIONS):
            owned.add(action['actionId'])
    return owned


def agent_launcher_actions(page: dict[str, Any]) -> list[dict[str, Any]]:
    """仅保留声明的会话入口；停止、复制及清空操作不能变成浮窗启动按钮。"""
    owned = agent_design_owned_action_ids(page)
    return [action for action in page.get('actions', []) if action['actionId'] in owned
            and ((action.get('behavior') or {}).get('type') != 'agent'
                 or action['behavior'].get('operation') == 'send_message')]


def business_design_page(page: dict[str, Any]) -> dict[str, Any]:
    """提取页面正文事实；独立页会话信息由官方组件承担，避免重复输入区。"""
    surfaces = page.get("agent_surfaces", [])
    owned = agent_design_owned_action_ids(page)
    standalone = any(surface["type"] == "standalone_page" for surface in surfaces)
    return {**page, "agent_surfaces": [],
            "actions": [action for action in page.get("actions", []) if action["actionId"] not in owned],
            "information_items": [] if standalone else page.get("information_items", [])}


def has_business_body(page: dict[str, Any]) -> bool:
    """浮窗页保留业务正文，独立页仅在声明会话外操作时生成页面控件。"""
    return any(surface["type"] == "floating_panel" for surface in page.get("agent_surfaces", [])) or bool(
        business_design_page(page)["actions"])
