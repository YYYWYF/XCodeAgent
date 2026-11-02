"""共享产品动作的 Agent 归属，避免在下游通过名称猜测接口需求。"""

from typing import Any


def _objects(value: Any) -> list[dict]:
    """筛选外部列表中的对象，结构错误留给产品边界校验报告。"""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def agent_behavior_targets(action: dict[str, Any]) -> list[dict[str, Any]]:
    """提取直接动作或组合步骤中显式声明的 Agent 行为。"""
    behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
    steps = behavior.get("steps", []) if behavior.get("type") == "sequence" else [behavior]
    return [step for step in _objects(steps) if step.get("type") == "agent"]


def agent_product_action_errors(agents: list[dict], pages: list[dict]) -> list[str]:
    """在产品阶段检查显式 Agent 动作的页面归属和操作语义。"""
    entries = {str(agent.get("agentId") or ""): {item for item in agent.get("entryPageIds", []) if isinstance(item, str)}
               for agent in agents if isinstance(agent.get("entryPageIds"), list)}
    owners: dict[tuple[str, str], str] = {}
    sequence_ids = {(page.get("pageId"), action.get("actionId"))
                    for page in pages for action in _objects(page.get("actions"))
                    if isinstance(action.get("behavior"), dict) and action["behavior"].get("type") == "sequence"}
    errors: list[str] = []
    for agent in agents:
        for binding in _objects(agent.get("pageActionBindings")):
            for action_id in binding.get("actionIds", []) if isinstance(binding.get("actionIds"), list) else []:
                if not isinstance(action_id, str):
                    continue
                key = (binding.get("pageId", ""), action_id)
                if key in sequence_ids:
                    continue
                previous = owners.setdefault(key, str(agent.get("agentId") or ""))
                if previous != agent.get("agentId"):
                    errors.append(f"页面 {key[0]} 的动作 {action_id} 被多个 Agent 重复认领。")
    for page in pages:
        page_id = page.get("pageId", "")
        for action in _objects(page.get("actions")):
            for target in agent_behavior_targets(action):
                agent_id = target.get("agentId", "")
                if not isinstance(agent_id, str):
                    errors.append("Agent 动作 agentId 必须为字符串。")
                    continue
                if page_id not in entries.get(agent_id, set()):
                    errors.append(f"页面 {page_id} 的动作 {action.get('actionId')} 引用了未绑定该页的 Agent {agent_id}。")
                if target.get("operation") not in {"send_message", "clear_conversation"}:
                    errors.append("Agent 动作 operation 必须为 send_message 或 clear_conversation。")
                if not isinstance(target.get("message"), str):
                    errors.append("Agent 动作 message 必须为字符串；自由输入和清空会话使用空字符串。")
                if target.get("operation") == "clear_conversation" and target.get("message"):
                    errors.append("清空当前客户端会话不能同时发送消息。")
                owner = owners.get((page_id, action.get("actionId", "")))
                if owner and owner != agent_id and (action.get("behavior") or {}).get("type") != "sequence":
                    errors.append(f"动作 {action.get('actionId')} 的 Agent 归属与页面绑定冲突。")
    return errors
