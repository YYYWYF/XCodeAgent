"""投影并校验 UiDesign 中的固定 Agent UI Surface 证据。"""

from __future__ import annotations

from typing import Any, Iterable

from app.services.product_plan import project_active_agent_product_plan
from app.services.ui_design_agent_template import (
    inspect_agent_ui_template_usage,
    validate_agent_ui_template_usage,
)


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
                    "actionIds": _string_items(binding.get("actionIds")),
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


def inspect_agent_surface_bindings(
    _tags: Iterable[tuple[str, str, bool, bool]], code: str = ""
) -> dict[str, Any]:
    """从 TSX 固定组件和静态配置提取 Surface 与模板证据。"""

    template_inspection = inspect_agent_ui_template_usage(code)
    surfaces: list[dict[str, Any]] = []
    for usage in _dict_items(template_inspection.get("usages")):
        config = usage.get("config") if isinstance(usage.get("config"), dict) else {}
        agent_id = str(config.get("agentId") or "").strip()
        surface_type = str(config.get("surface") or "").strip()
        parts = _REQUIRED_PARTS.get(surface_type, ())
        surfaces.append(
            {
                "agentId": agent_id,
                "type": surface_type,
                "actionIds": [str(config.get("actionId") or "").strip()],
                "contextItemIds": [
                    str(item.get("id") or "").strip()
                    for item in _dict_items(config.get("contextItems"))
                    if str(item.get("id") or "").strip()
                ],
                "controlIds": [f"{agent_id}-{part}" for part in parts],
                "parts": [
                    {"part": part, "controlId": f"{agent_id}-{part}"} for part in parts
                ],
                "template": {
                    "module": str(usage.get("module") or ""),
                    "component": str(usage.get("component") or ""),
                    "version": str(usage.get("version") or ""),
                    "configSha256": str(usage.get("configSha256") or ""),
                },
            }
        )
    return {
        "surfaces": surfaces,
        "invalid_markers": list(template_inspection.get("errors") or []),
        "duplicate_control_ids": [],
        "template_inspection": template_inspection,
    }


def validate_agent_surface_bindings(
    page: dict[str, Any], inspection: dict[str, Any]
) -> dict[str, Any]:
    """校验固定组件用法及其 action、上下文与 ProductPlan 完全一致。"""

    template_inspection = (
        inspection.get("template_inspection")
        if isinstance(inspection.get("template_inspection"), dict)
        else {"usages": [], "errors": list(inspection.get("invalid_markers") or [])}
    )
    return validate_agent_ui_template_usage(page, template_inspection)


def manifest_agent_surface_bindings(
    page: dict[str, Any], inspection: dict[str, Any]
) -> list[dict[str, Any]]:
    """按 ProductPlan 顺序构造 UiManifest v5 的固定模板证据。"""

    actual_by_key = {
        (str(item.get("agentId") or ""), str(item.get("type") or "")): item
        for item in _dict_items(inspection.get("surfaces"))
    }
    result: list[dict[str, Any]] = []
    for expected in expected_agent_surfaces(page):
        key = (str(expected.get("agentId") or ""), str(expected.get("type") or ""))
        actual = actual_by_key.get(key, {})
        parts = _REQUIRED_PARTS.get(key[1], ())
        result.append(
            {
                "agentId": key[0],
                "type": key[1],
                "actionIds": _string_items(expected.get("actionIds")),
                "contextItemIds": _string_items(expected.get("contextItemIds")),
                "controlIds": _string_items(actual.get("controlIds")),
                "parts": [
                    {"part": part, "controlId": f"{key[0]}-{part}"} for part in parts
                ] if actual else [],
                "template": actual.get(
                    "template",
                    {
                        "module": "",
                        "component": (
                            "AgentConversationTemplate"
                            if key[1] == "standalone_page"
                            else "AgentFloatingPanelTemplate"
                            if key[1] == "floating_panel"
                            else ""
                        ),
                        "version": "",
                        "configSha256": "",
                    },
                ),
            }
        )
    return result
