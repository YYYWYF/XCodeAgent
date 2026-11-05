"""技术候选只编辑业务接口，Agent 运行入口由平台按当前能力重新派生。"""
from copy import deepcopy


def editable_runtime_candidate(plan: dict) -> dict:
    """按正式 Agent 身份排除独占的派生入口，不根据路径或错误 kind 猜测业务类型。"""
    result = deepcopy(plan)
    identities = {f"agent.{agent['agentId']}": f"agent.{agent['agentId']}.run"
                  for agent in result.get('agent_contracts', [])
                  if isinstance(agent, dict) and isinstance(agent.get('agentId'), str)}
    retained = []
    for contract in result.get('api_contracts', []):
        expected = identities.get(contract.get('id')) if isinstance(contract, dict) else None
        if expected is not None:
            endpoints = contract.get('endpoints')
            if (not isinstance(endpoints, list) or len(endpoints) != 1
                    or not isinstance(endpoints[0], dict) or endpoints[0].get('id') != expected):
                raise ValueError(f"派生 Agent Contract {contract.get('id')} 不能混入其他业务接口。")
            continue
        retained.append(contract)
    result['api_contracts'] = retained
    return result


def rebuild_candidate_runtime_endpoints(plan: dict) -> dict:
    """仅在候选装配时重建派生接口；不选择架构、不确认产物或批准工具执行。"""
    if not plan.get('agent_contracts'):
        return plan
    from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
    return materialize_runtime_endpoints(editable_runtime_candidate(plan), require_topology=False)
