"""构造并校验 UiDesign 使用的固定 Agent UI 组件契约。"""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any


AGENT_UI_TEMPLATE_MODULE = "@xcodeagent/agent-ui-design"
AGENT_UI_TEMPLATE_VERSION = "agent-ui.v1"

_SURFACE_COMPONENTS = {
    "standalone_page": "AgentConversationTemplate",
    "floating_panel": "AgentFloatingPanelTemplate",
}
_FIXED_COMPONENTS = tuple(_SURFACE_COMPONENTS.values())
_CUSTOM_CHAT_PATTERNS = (
    r"\b(?:function|class)\s+AgentChatCore\b",
    r"\bconst\s+AgentChatCore\s*=",
    r"\bdata-agent-part\s*=",
    r"\b(?:agent-message|x-agent-message)\b",
)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从数组值中只保留 JSON 对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_items(value: Any) -> list[str]:
    """提取去空白、去重且保序的字符串项。"""

    result: list[str] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _required_text(value: Any, field: str) -> str:
    """读取必填字符串，缺失时抛出含字段名的明确错误。"""

    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"Agent UI 配置缺少必填字段：{field}。")
    return normalized


def _canonical_config(config: dict[str, Any]) -> str:
    """以稳定键顺序序列化模板配置，供源码和摘要复用。"""

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _surface_for_page(page: dict[str, Any]) -> dict[str, Any]:
    """读取当前页唯一的 Agent Surface，并拒绝不受支持的多 Surface 输入。"""

    surfaces = _dict_items(page.get("agent_surfaces"))
    if len(surfaces) != 1:
        raise ValueError("当前页面必须且只能配置一个 Agent Surface。")
    surface = surfaces[0]
    surface_type = _required_text(surface.get("type"), "surface.type")
    if surface_type not in _SURFACE_COMPONENTS:
        raise ValueError(f"Agent UI Surface 类型无效：{surface_type}。")
    return surface


def build_agent_ui_template_config(page: dict[str, Any]) -> dict[str, Any]:
    """从 ProductPlan 单页事实确定性投影固定 Agent UI 配置。"""

    surface = _surface_for_page(page)
    action_ids = _string_items(surface.get("actionIds"))
    if len(action_ids) != 1:
        raise ValueError("每个 Agent Surface 必须且只能绑定一个 actionId。")

    item_by_id = {
        str(item.get("itemId") or "").strip(): item
        for item in _dict_items(page.get("information_items"))
        if str(item.get("itemId") or "").strip()
    }
    context_items: list[dict[str, str]] = []
    for item_id in _string_items(surface.get("contextItemIds")):
        item = item_by_id.get(item_id, {})
        label = str(item.get("label") or item.get("name") or item_id).strip()
        context_items.append(
            {"id": item_id, "label": label, "value": f"{label}示例值"}
        )

    capabilities = [
        {
            "id": _required_text(capability.get("capabilityId"), "capabilityId"),
            "label": _required_text(
                capability.get("name") or capability.get("description"),
                "capability.name",
            ),
        }
        for capability in _dict_items(surface.get("capabilities"))
    ]
    name = _required_text(surface.get("name") or surface.get("agentId"), "name")
    responsibility = _required_text(
        surface.get("purpose") or surface.get("responsibility"), "purpose"
    )
    capability_label = capabilities[0]["label"] if capabilities else responsibility.rstrip("。")
    return {
        "templateVersion": AGENT_UI_TEMPLATE_VERSION,
        "agentId": _required_text(surface.get("agentId"), "agentId"),
        "surface": _required_text(surface.get("type"), "surface"),
        "name": name,
        "responsibility": responsibility.rstrip("。"),
        "actionId": action_ids[0],
        "contextItems": context_items,
        "capabilities": capabilities,
        "suggestedQuestions": [f"请帮我{capability_label}。", f"请说明{name}当前可以完成什么。"],
        "features": {
            "attachments": False,
            "approvals": True,
            "tools": True,
            "maximize": False,
        },
        "mock": {
            "userMessage": f"请帮我{capability_label}。",
            "assistantMessage": f"我是{name}，将根据当前页面允许的上下文完成：{responsibility}",
            "toolTitle": f"执行{capability_label}",
            "toolDetail": "已完成设计预览中的模拟工具调用，未连接真实业务服务。",
            "approvalTitle": "需要你的确认",
            "approvalDetail": "这是设计预览中的模拟审批，不会提交真实业务变更。",
            "successMessage": f"{capability_label}的模拟流程已完成。",
            "errorMessage": "模拟连接暂时中断，请重试。",
        },
    }


def build_agent_ui_template_source_contract(page: dict[str, Any]) -> str:
    """生成可直接放入设计稿的固定组件静态源码契约。"""

    config = build_agent_ui_template_config(page)
    component = _SURFACE_COMPONENTS[str(config["surface"])]
    config_json = html.escape(_canonical_config(config), quote=True)
    return (
        "import React from 'react'\n"
        f"import {{ {component} }} from '{AGENT_UI_TEMPLATE_MODULE}'\n\n"
        "/** 使用平台固定 Agent UI 组件渲染当前页面。 */\n"
        "function Page(): React.ReactElement {\n"
        f'  return <{component} configJson="{config_json}" />\n'
        "}\n\n"
        "export default Page\n"
    )


def _imported_components(code: str) -> set[str]:
    """读取固定虚拟模块的命名导入，拒绝近似模块或隐式来源。"""

    imports: set[str] = set()
    pattern = re.compile(
        r"import\s*\{(?P<names>[^}]+)\}\s*from\s*['\"]"
        + re.escape(AGENT_UI_TEMPLATE_MODULE)
        + r"['\"]"
    )
    for match in pattern.finditer(code):
        for name in match.group("names").split(","):
            imported = name.strip().split(" as ", 1)[0].strip()
            if imported:
                imports.add(imported)
    return imports


def _config_constants(code: str) -> dict[str, str]:
    """提取赋给常量的静态 configJson 字符串。"""

    constants: dict[str, str] = {}
    pattern = re.compile(
        r"\bconst\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
        r"(?P<quote>['\"])(?P<value>(?:\\.|(?!\2).)*?)(?P=quote)",
        re.DOTALL,
    )
    for match in pattern.finditer(code):
        constants[match.group("name")] = match.group("value")
    return constants


def _component_config_json(attrs: str, constants: dict[str, str]) -> str:
    """读取固定组件的静态 configJson 字符串或静态常量引用。"""

    literal = re.search(
        r"\bconfigJson\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
        attrs,
        re.DOTALL,
    )
    if literal:
        return html.unescape(literal.group("value"))
    reference = re.search(
        r"\bconfigJson\s*=\s*\{\s*(?P<name>[A-Za-z_$][\w$]*)\s*\}", attrs
    )
    if reference:
        return constants.get(reference.group("name"), "")
    return ""


def inspect_agent_ui_template_usage(code: str) -> dict[str, Any]:
    """从 TSX 源码提取固定 Agent UI 用法、配置摘要和违规证据。"""

    errors: list[str] = []
    usages: list[dict[str, Any]] = []
    imported = _imported_components(code)
    constants = _config_constants(code)
    custom_chat = any(re.search(pattern, code) for pattern in _CUSTOM_CHAT_PATTERNS)
    if custom_chat:
        errors.append("页面不得自制 Agent 聊天气泡、状态机或 AgentChatCore。")

    component_pattern = "|".join(re.escape(name) for name in _FIXED_COMPONENTS)
    for match in re.finditer(
        rf"<(?P<component>{component_pattern})\b(?P<attrs>[^>]*)/?>", code, re.DOTALL
    ):
        component = match.group("component")
        if component not in imported:
            errors.append(f"固定组件 {component} 必须从 {AGENT_UI_TEMPLATE_MODULE} 精确导入。")
        config_json = _component_config_json(match.group("attrs"), constants)
        if not config_json:
            errors.append(f"固定组件 {component} 必须提供静态 configJson。")
            continue
        try:
            parsed = json.loads(config_json)
        except json.JSONDecodeError:
            errors.append(f"固定组件 {component} 的 configJson 不是有效 JSON。")
            continue
        if not isinstance(parsed, dict):
            errors.append(f"固定组件 {component} 的 configJson 根节点必须为对象。")
            continue
        canonical = _canonical_config(parsed)
        usages.append(
            {
                "module": AGENT_UI_TEMPLATE_MODULE,
                "component": component,
                "version": str(parsed.get("templateVersion") or ""),
                "config": parsed,
                "configSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            }
        )
    return {"usages": usages, "errors": errors, "custom_chat": custom_chat}


def validate_agent_ui_template_usage(
    page: dict[str, Any], inspection: dict[str, Any]
) -> dict[str, Any]:
    """校验固定组件、版本和配置与当前页面 ProductPlan 事实完全一致。"""

    errors = [str(error) for error in inspection.get("errors", []) if str(error)]
    usages = _dict_items(inspection.get("usages"))
    surfaces = _dict_items(page.get("agent_surfaces"))
    if not surfaces:
        if usages:
            errors.append("当前页面不需要 Agent UI，不得加载固定 Agent UI 组件。")
        return {"errors": errors, "bindings_passed": not errors, "required_parts_passed": not errors}
    if len(usages) != 1:
        errors.append("包含 Agent Surface 的页面必须且只能使用一个固定 Agent UI 组件。")
        return {"errors": errors, "bindings_passed": False, "required_parts_passed": False}

    try:
        expected_config = build_agent_ui_template_config(page)
    except ValueError as exc:
        errors.append(str(exc))
        return {"errors": errors, "bindings_passed": False, "required_parts_passed": False}
    expected_component = _SURFACE_COMPONENTS[str(expected_config["surface"])]
    usage = usages[0]
    if usage.get("component") != expected_component:
        errors.append(
            f"Agent Surface 必须使用固定组件 {expected_component}，实际为 {usage.get('component') or 'missing'}。"
        )
    if usage.get("module") != AGENT_UI_TEMPLATE_MODULE:
        errors.append(f"固定组件模块必须为 {AGENT_UI_TEMPLATE_MODULE}。")
    if usage.get("version") != AGENT_UI_TEMPLATE_VERSION:
        errors.append(f"固定组件版本必须为 {AGENT_UI_TEMPLATE_VERSION}。")
    if usage.get("config") != expected_config:
        errors.append("固定 Agent UI 的 configJson 必须与 ProductPlan 页面投影完全一致。")
    return {"errors": errors, "bindings_passed": not errors, "required_parts_passed": not errors}


def component_for_surface(surface_type: str) -> str:
    """返回 Agent Surface 对应的固定组件名。"""

    component = _SURFACE_COMPONENTS.get(surface_type)
    if not component:
        raise ValueError(f"Agent UI Surface 类型无效：{surface_type or 'missing'}。")
    return component
