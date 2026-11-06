"""区分共享会话与页面正文，独立页仍可承载导航和普通操作。"""

from typing import Any


def business_design_page(page: dict[str, Any]) -> dict[str, Any]:
    """提取页面正文事实；独立页会话信息由官方组件承担，避免重复输入区。"""
    surfaces = page.get("agent_surfaces", [])
    owned = {action_id for surface in surfaces for action_id in surface["actionIds"]}
    standalone = any(surface["type"] == "standalone_page" for surface in surfaces)
    return {**page, "agent_surfaces": [],
            "actions": [action for action in page.get("actions", []) if action["actionId"] not in owned],
            "information_items": [] if standalone else page.get("information_items", [])}


def has_business_body(page: dict[str, Any]) -> bool:
    """浮窗页保留业务正文，独立页仅在声明会话外操作时生成页面控件。"""
    return any(surface["type"] == "floating_panel" for surface in page.get("agent_surfaces", [])) or bool(
        business_design_page(page)["actions"])
