"""编译正式 Agent UI 的固定组件及当前 Runtime 会话合同。"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.ui_design_agent_template import build_agent_ui_template_config


AGENT_UI_BUILD_CONTRACT_VERSION = "agent-ui-build.v1"
AGENT_UI_COMPONENT_MODULE = "@/components/AgentConversation"
AGENT_UI_CONFIG_TYPE_MODULE = "@/typings/agentConversation"
AGENT_UI_RUNTIME_ADAPTER_PATH = "frontend/src/apis/agentConversationRuntime.ts"
AGENT_UI_SKILL_PATH = "/.devagentstudio/builtin-skills/agent-ui-surface-template/SKILL.md"

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




def project_agent_ui_build_contracts(
    product_plan: dict[str, Any],
    technical_plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """从已确认产品及 Runtime 技术合同投影真实会话组件和精确同源路径。"""

    from app.services.runtime_agent_contracts import agent_contract_index
    contracts = agent_contract_index(technical_plan)
    result: dict[str, dict[str, Any]] = {}
    for page in project_ui_design_pages(product_plan):
        page_id = _text(page.get("pageId"))
        surfaces = _dict_items(page.get("agent_surfaces"))
        if not page_id or not surfaces:
            continue
        config = build_agent_ui_template_config(page)
        agent_id = _text(config.get("agentId"))
        if agent_id not in contracts:
            raise ValueError(f"Agent {agent_id} 缺少当前 Runtime 合同。")
        config["runtime"] = {"path": f"/agents/{agent_id}/run", "pageId": page_id}
        surface = _text(config.get("surface"))
        component = _COMPONENTS.get(surface)
        if component is None:
            raise ValueError(f"Agent UI Surface 类型无效：{surface}。")
        payload = {
            "version": AGENT_UI_BUILD_CONTRACT_VERSION,
            "mode": "runtime",
            "pageId": page_id,
            "agentId": agent_id,
            "surface": surface,
            "componentModule": AGENT_UI_COMPONENT_MODULE,
            "component": component[0],
            "componentPath": component[1],
            "configTypeModule": AGENT_UI_CONFIG_TYPE_MODULE,
            "runtimeAdapterPath": AGENT_UI_RUNTIME_ADAPTER_PATH,
            "skillPath": AGENT_UI_SKILL_PATH,
            "runtimeAgentId": agent_id,
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
    """读取任务上由平台注入的正式 Agent UI 合同。"""

    return agent_ui_contract_from_source_refs(task.get("source_refs"))


