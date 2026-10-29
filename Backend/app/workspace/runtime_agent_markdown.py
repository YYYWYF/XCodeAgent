"""把内部 Agent 七模块事实投射为同一技术规划内可审核、可编辑的 Markdown。"""

from typing import Any

from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES
from app.services.runtime_agent_contracts import MODULE_LABELS, agent_invocation


def _setting_lines(value: Any, depth: int = 0) -> list[str]:
    """保留模块配置的层次和类型，用 Markdown 列表表达而非新增 JSON 用户产物。"""

    prefix = "  " * depth
    if isinstance(value, dict):
        return [line for key, item in value.items() for line in (
            [f"{prefix}- **{key}**："] + _setting_lines(item, depth + 1)
            if isinstance(item, (dict, list)) else [f"{prefix}- **{key}**：{item}"]
        )] or [f"{prefix}- 无额外设置"]
    if isinstance(value, list):
        return [line for item in value for line in _setting_lines(item, depth + 1)] or [f"{prefix}- 无"]
    return [f"{prefix}- {value}"]


def runtime_agent_invocations_markdown(plan: dict) -> str:
    """在 API 章节列出每个正式智能体的唯一接口，详细模块仍在智能体章节。"""

    lines = []
    for contract in plan.get("agent_contracts", []):
        invocation = agent_invocation(contract["agentId"])
        lines.append(f"- {contract['name']}：`{invocation['method']} {invocation['path']}`"
                     "（AG-UI / SSE；详细调用契约见智能体章节）")
    return "\n".join(lines)


def runtime_agents_markdown(plan: dict) -> str:
    """每个 Agent 展示业务绑定和全部七模块，修改仍须原 TechnicalPlan 确认。"""

    sections = []
    for contract in plan.get("agent_contracts", []):
        invocation = agent_invocation(contract["agentId"])
        lines = [f"### {contract['name']}（`{contract['agentId']}`）", "",
                 f"- 职责：{contract['purpose']}", f"- 页面：{'、'.join(contract['pageIds'])}", "",
                 "#### 公开调用接口", "",
                 f"- 接口：`{invocation['method']} {invocation['path']}`",
                 f"- 接口标识：`{invocation['endpointId']}`",
                 f"- 协议：AG-UI；传输：SSE；服务：`{invocation['serviceId']}`",
                 f"- 请求：`{invocation['requestType']}`；响应：`{invocation['responseType']}` 事件流",
                 "- 身份：Runtime 匿名会话提供可信 Principal，按 Principal/thread 隔离。",
                 "- 所有关联页面共用此接口；调用契约不代表接口已实现或真实集成已完成。", ""]
        for module in AGENT_RUNTIME_MODULES:
            lines.extend([f"#### {MODULE_LABELS[module]}（{module}）", "",
                          *_setting_lines(contract["agentSettings"][module]), ""])
        sections.append("\n".join(lines))
    return "\n\n".join(sections) or "- 无可执行 Agent"
