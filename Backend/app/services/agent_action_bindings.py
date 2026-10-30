"""从已确认产品入口解析 Agent 会话动作，不为聊天创建业务 Endpoint。"""

from typing import Any
from app.services.agent_product_actions import agent_behavior_targets


def agent_action_bindings(product_plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    """按页面和动作索引已启用的唯一 Agent，拒绝多个智能体争用同一动作。"""

    result: dict[str, dict[str, str]] = {}
    sequence_ids = {(page.get("pageId"), action.get("actionId"))
                    for page in product_plan.get("pages", []) for action in page.get("actions", [])
                    if (action.get("behavior") or {}).get("type") == "sequence"}
    for agent in product_plan.get("agents", []):
        agent_id = agent["agentId"]
        for binding in agent.get("pageActionBindings", []):
            if binding.get("surface", {}).get("enabled") is not True:
                continue
            page_id = binding["pageId"]
            actions = result.setdefault(page_id, {})
            for action_id in binding.get("actionIds", []):
                if (page_id, action_id) in sequence_ids:
                    continue
                if action_id in actions and actions[action_id] != agent_id:
                    raise ValueError(f"页面 {page_id} 的操作 {action_id} 不能绑定多个智能体。")
                actions[action_id] = agent_id
    # 显式行为本身就是权威归属，不要求模型在两处重复声明同一直接动作。
    enabled = {(binding["pageId"], agent["agentId"])
               for agent in product_plan.get("agents", [])
               for binding in agent.get("pageActionBindings", [])
               if binding.get("surface", {}).get("enabled") is True}
    for page in product_plan.get("pages", []):
        for action in page.get("actions", []):
            if (action.get("behavior") or {}).get("type") != "agent":
                continue
            target = agent_behavior_targets(action)[0]
            if (page["pageId"], target["agentId"]) in enabled:
                result.setdefault(page["pageId"], {})[action["actionId"]] = target["agentId"]
    return result
