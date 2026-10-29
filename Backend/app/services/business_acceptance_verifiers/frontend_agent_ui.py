"""验证生成应用页面对固定 Agent UI Mock 组件的受控组合。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.business_acceptance_verifiers.common import (
    strip_comments,
    verification_result,
)


_NETWORK_PATTERNS = (
    r"\bfetch\s*\(",
    r"\baxios\b",
    r"\bXMLHttpRequest\b",
    r"\bWebSocket\b",
    r"\bEventSource\b",
    r"\bsendBeacon\s*\(",
)
_CUSTOM_CHAT_PATTERNS = (
    r"\b(?:function|class)\s+AgentChatCore\b",
    r"\bconst\s+AgentChatCore\s*=",
    r"\bAgentConversationAdapter\b",
    r"\bagentConversationMock\b",
)


def _extract_json_config(source: str) -> dict[str, Any] | None:
    """读取固定名称 AGENT_UI_CONFIG 后的纯 JSON 对象字面量。"""

    match = re.search(
        r"\bconst\s+AGENT_UI_CONFIG(?:\s*:\s*AgentUiTemplateConfig)?\s*=\s*\{",
        source,
    )
    if not match:
        return None
    start = source.find("{", match.start())
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(source[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _component_imported(source: str, component: str, module: str) -> bool:
    """确认固定组件通过唯一模块的具名导入进入页面。"""

    for match in re.finditer(
        rf"import\s*\{{(?P<names>[^}}]+)\}}\s*from\s*['\"]{re.escape(module)}['\"]",
        source,
        re.DOTALL,
    ):
        names = {
            item.strip().split(" as ", 1)[0].strip()
            for item in match.group("names").split(",")
        }
        if component in names:
            return True
    return False


def _component_attributes(source: str, component: str) -> str:
    """提取固定组件首个 JSX 用法的属性文本。"""

    match = re.search(rf"<{re.escape(component)}\b(?P<attrs>[^>]*)/?>", source, re.DOTALL)
    return match.group("attrs") if match else ""


def _floating_business_body_exists(source: str, component: str) -> bool:
    """确认浮窗页面除固定组件外仍渲染至少一个真实业务 JSX 元素。"""

    tags = [
        tag
        for tag in re.findall(r"<([A-Za-z][A-Za-z0-9.]*)\b", source)
        if tag not in {component, "React.Fragment"}
    ]
    return bool(tags)


def verify_agent_ui_mock_source(
    files: dict[str, str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """验证页面精确组合固定组件，并拒绝 Adapter 覆盖和直连网络。"""

    contract = expected.get("agent_ui")
    contract = contract if isinstance(contract, dict) else {}
    component = str(contract.get("component") or "").strip()
    module = str(contract.get("componentModule") or "").strip()
    expected_config = contract.get("config")
    expected_config = expected_config if isinstance(expected_config, dict) else {}
    if not component or not module or not expected_config:
        return verification_result("blocked", "Agent UI Mock 检查缺少固定组件或配置合同。")
    combined = "\n".join(files.values())
    clean = strip_comments(combined)
    errors: list[str] = []
    if not _component_imported(clean, component, module):
        errors.append(f"页面未从 {module} 精确导入 {component}。")
    attrs = _component_attributes(clean, component)
    if not attrs:
        errors.append(f"页面未渲染固定组件 {component}。")
    elif not re.search(r"\bconfig\s*=\s*\{\s*AGENT_UI_CONFIG\s*\}", attrs):
        errors.append("固定组件必须使用 config={AGENT_UI_CONFIG}。")
    if attrs and (
        re.search(r"\badapter\s*=", attrs)
        or re.search(r"\{\s*\.\.\.", attrs)
    ):
        errors.append("固定组件不得传入 adapter 或 JSX spread。")
    actual_config = _extract_json_config(clean)
    if actual_config != expected_config:
        errors.append("AGENT_UI_CONFIG 与平台投影配置不完全一致。")
    if "data-preview-only" in clean:
        errors.append("生成应用页面不得保留 data-preview-only 评审控制器。")
    if any(re.search(pattern, clean) for pattern in _CUSTOM_CHAT_PATTERNS):
        errors.append("页面不得自制 AgentChatCore、Adapter 或 Mock 数据层。")
    network_hits = [pattern for pattern in _NETWORK_PATTERNS if re.search(pattern, clean)]
    if network_hits:
        errors.append("Agent UI 页面不得直连 fetch、axios、SSE、WebSocket 或 sidecar。")
    if contract.get("surface") == "floating_panel" and not _floating_business_body_exists(
        clean, component
    ):
        errors.append("floating_panel 页面必须保留并渲染普通业务页面主体。")
    if errors:
        return verification_result(
            "failed",
            "；".join(errors),
            facts={"component": component, "network_pattern_count": len(network_hits)},
        )
    return verification_result(
        "passed",
        f"已验证 {component} 使用精确平台配置和默认 Mock Adapter，且页面无 Agent 网络直连。",
        facts={
            "component": component,
            "page_id": str(contract.get("pageId") or ""),
            "agent_id": str(contract.get("agentId") or ""),
            "gateway_endpoint_id": str(contract.get("gatewayEndpointId") or ""),
        },
    )
