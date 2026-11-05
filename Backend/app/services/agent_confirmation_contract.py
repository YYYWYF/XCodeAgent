"""在技术规划候选阶段补齐写入工具必需的确认能力，不批准实际操作。"""
from copy import deepcopy


def complete_tool_confirmation(plan: dict) -> dict:
    """只为已绑定写接口且缺少确认能力的候选追加 confirmation，其余合同交原校验。"""
    result = deepcopy(plan)
    write_keys = {(api.get("id"), endpoint.get("id"))
                  for api in result.get("api_contracts", []) if isinstance(api, dict)
                  for endpoint in api.get("endpoints", []) if isinstance(endpoint, dict)
                  if endpoint.get("method") in {"POST", "PUT", "PATCH", "DELETE"}}
    for agent in result.get("agent_contracts", []):
        if not isinstance(agent, dict):
            continue
        interaction = agent.get("interactionContract")
        settings = agent.get("agentSettings")
        if not isinstance(interaction, dict) or not isinstance(settings, dict):
            continue
        tools = settings.get("tools")
        kinds = interaction.get("interactionKinds")
        if not isinstance(tools, dict) or not isinstance(tools.get("bindings"), list) or not isinstance(kinds, list):
            continue
        # 非法声明不自动修复；保留原校验错误，避免掩盖其他合同问题。
        if any(not isinstance(kind, str) for kind in kinds):
            continue
        if {"confirmation", "approval_required"}.intersection(kinds):
            continue
        if any(isinstance(binding, dict) and (binding.get("apiContractId"), binding.get("endpointId")) in write_keys
               for binding in tools["bindings"]):
            interaction["interactionKinds"] = [*kinds, "confirmation"]
    return result
