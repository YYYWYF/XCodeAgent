"""按已确认 Agent 动作归属编译页面引用，不让模型重复选择会话接口。"""

from copy import deepcopy

from app.services.agent_action_bindings import agent_action_bindings
from app.services.api_contracts import dict_items
from app.services.page_endpoint_identity import endpoint_catalog
from app.services.product_plan import project_active_agent_product_plan


def compile_agent_page_references(plan: dict, product_plan: dict) -> dict:
    """移除平台拥有动作的接口选择，仅清理由它们引入且不存在的接口依赖。"""
    owners = agent_action_bindings(project_active_agent_product_plan(product_plan))
    result = deepcopy(plan)
    if not owners:
        return result
    catalog = endpoint_catalog(result)
    for page in dict_items(result.get("pages")):
        owned = owners.get(page.get("pageId"), {})
        references = page.get("references")
        if not owned or not isinstance(references, dict):
            continue
        implementations = dict_items(references.get("action_implementations"))
        removed = [item for item in implementations if item.get("actionId") in owned]
        if not removed:
            continue
        retained = [item for item in implementations if item.get("actionId") not in owned]
        references["action_implementations"] = retained
        discarded = {(str(item.get("apiContractId") or ""), str(item.get("endpointId") or ""))
                     for item in removed if item.get("endpointId")}
        # 业务步骤共享的引用仍由原校验器处理，不能因为另一个 Agent 动作填错而删除。
        used = {str(target.get("endpointId") or "") for item in retained
                for target in (dict_items(item.get("stepBindings")) or [item])}
        dependencies = []
        for dependency in dict_items(references.get("endpoint_dependencies")):
            contract_id = str(dependency.get("api_contract_id") or "")
            endpoint_id = str(dependency.get("endpoint_id") or "")
            matched = any(endpoint == endpoint_id and (not contract or not contract_id or contract == contract_id)
                          for contract, endpoint in discarded)
            existing = any(endpoint == endpoint_id and (not contract_id or contract == contract_id)
                           for contract, endpoint in catalog)
            if not (matched and not existing and endpoint_id not in used):
                dependencies.append(dependency)
        references["endpoint_dependencies"] = dependencies
    return result
