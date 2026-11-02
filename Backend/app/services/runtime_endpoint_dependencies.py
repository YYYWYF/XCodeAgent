"""设计、开发与验收共用的有界目标解析，所有 Endpoint 使用复合身份。"""
from collections.abc import Mapping

from app.services.runtime_agent_contracts import agent_contract_index
from app.services.page_endpoint_identity import page_endpoint_keys


def resolve_runtime_target(plan: Mapping, target_type: str, target_id: str,
                           api_contract_id: str | None = None) -> dict:
    """统一解析页面、运行接口和 Agent，不反向拉入消费页面或其他 Agent。"""
    agents = agent_contract_index(plan)
    endpoints = {}
    for contract in plan.get("api_contracts", ()):
        for endpoint in contract.get("endpoints", ()):
            key = (contract["id"], endpoint["id"])
            if key in endpoints:
                raise ValueError(f"Endpoint 复合身份重复：{key}。")
            endpoints[key] = (contract, endpoint)
    selected, agent_ids = set(), []
    if target_type == "application":
        selected.update(endpoints)
        agent_ids = list(agents)
    elif target_type == "agent":
        if target_id not in agents:
            raise ValueError(f"正式技术规划不存在智能体 {target_id}。")
        agent_ids = [target_id]
    elif target_type == "endpoint":
        key = (api_contract_id, target_id)
        if key not in endpoints:
            raise ValueError(f"正式技术规划不存在 Endpoint {key}。")
        selected.add(key)
        endpoint = endpoints[key][1]
        if endpoint.get("kind") == "agent_runtime":
            agent_ids = [endpoint["agent_id"]]
    elif target_type == "page":
        from app.services.frontend_page_tree import flatten_frontend_pages
        records = plan["pages"] if "pages" in plan else flatten_frontend_pages(plan.get("frontend_pages"))
        page = next((page for page in records if (page.get("pageId") or page.get("id")) == target_id), None)
        if page is None:
            raise ValueError(f"正式技术规划不存在页面 {target_id}。")
        page_contract = next((item for item in plan.get("page_implementation_contracts", ())
                              if item.get("pageId") == target_id), {})
        selected.update(page_endpoint_keys(plan, page_contract or page))
        agent_ids = [key for key, agent in agents.items() if target_id in agent["pageIds"]]
    else:
        raise ValueError(f"不支持的开发目标：{target_type}。")
    for key in selected:
        endpoint = endpoints[key][1]
        if endpoint.get("kind") == "agent_runtime":
            identity = endpoint["agent_id"]
            if target_type == "page" and (identity not in agents or target_id not in agents[identity]["pageIds"]):
                raise ValueError(f"页面 {target_id} 不能消费未绑定自身的 Agent {identity}。")
            if identity not in agent_ids:
                agent_ids.append(identity)
    for agent_id in agent_ids:
        if agent_id not in agents:
            raise ValueError(f"运行接口引用未知 Agent：{agent_id}。")
        key = (f"agent.{agent_id}", f"agent.{agent_id}.run")
        if key not in endpoints or endpoints[key][1].get("agent_id") != agent_id:
            raise ValueError(f"Agent {agent_id} 缺少唯一正式运行 Endpoint。")
        selected.add(key)
        for binding in agents[agent_id]["agentSettings"]["tools"]["bindings"]:
            dependency = (binding["apiContractId"], binding["endpointId"])
            if dependency not in endpoints or endpoints[dependency][1].get("kind") != "business":
                raise ValueError(f"Agent {agent_id} 的 Tool 缺少业务 Endpoint 前置实现。")
            selected.add(dependency)
    targets = [endpoints[key] for key in sorted(selected)]
    return {"agent_ids": agent_ids, "endpoint_keys": sorted(selected), "endpoints": targets,
            "required_unit_ids": [*(f"agent:{key}" for key in agent_ids),
                                  *(f"backend:endpoint:{api}:{endpoint}" for api, endpoint in sorted(selected))]}
