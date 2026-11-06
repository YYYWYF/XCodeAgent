"""为当前分段目录协议提供受控响应，不保留旧的整目录模型输出。"""

from copy import deepcopy

from app.agents.main.technical_plan_catalog_parts_validation import catalog_registry


def catalog_responses(catalog: dict, *, entity_ids=None, contract_ids=None, page_ids=None) -> list[dict]:
    """按测试指定所有权构造身份、字段、接口目录及逐页映射响应。"""

    values = [catalog_registry(catalog)]
    values.extend({"fields": deepcopy(entity["fields"])} for entity in catalog["entities"]
        if entity_ids is None or entity["id"] in entity_ids)
    values.extend({"endpoints": deepcopy(contract["endpoints"])} for contract in catalog["contracts"]
        if contract_ids is None or contract["id"] in contract_ids)
    ordered_pages = page_ids if page_ids is not None else list(dict.fromkeys(binding["pageId"] for binding in catalog["action_bindings"]))
    values.extend({"action_bindings": [deepcopy(binding) for binding in catalog["action_bindings"] if binding["pageId"] == page_id]}
        for page_id in ordered_pages if any(binding["pageId"] == page_id for binding in catalog["action_bindings"]))
    return values


def catalog_response(prompt: str, catalog: dict) -> dict:
    """根据实际裁剪后的阶段上下文返回对应当前对象，供可编程模型夹具复用。"""

    import json
    context = json.loads(prompt.splitlines()[-1])
    stage = prompt.splitlines()[0]
    if "catalog_registry" in stage:
        return catalog_registry(catalog)
    if "catalog_entity" in stage:
        return {"fields": next(entity["fields"] for entity in catalog["entities"] if entity["id"] == context["entity"]["id"])}
    if "catalog_endpoints" in stage:
        return {"endpoints": next(contract["endpoints"] for contract in catalog["contracts"] if contract["id"] == context["contract"]["id"])}
    return {"action_bindings": [binding for binding in catalog["action_bindings"] if binding["pageId"] == context["page"]["pageId"]]}
