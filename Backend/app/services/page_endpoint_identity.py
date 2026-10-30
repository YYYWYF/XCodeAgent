"""按页面正式依赖解析 Endpoint 复合身份，短标识仅在当前作用域唯一时使用。"""

from collections.abc import Mapping


def endpoint_catalog(plan: Mapping) -> dict[tuple[str, str], tuple[dict, dict]]:
    """索引完整身份并拒绝覆盖同一契约中的重复 Endpoint。"""
    result = {}
    contracts = plan.get("api_contracts", [])
    if not isinstance(contracts, (list, tuple)):
        raise ValueError("api_contracts 必须是接口契约数组。")
    for contract in contracts:
        if not isinstance(contract, Mapping) or not isinstance(contract.get("endpoints", []), (list, tuple)):
            raise ValueError("接口契约必须包含 Endpoint 数组。")
        for endpoint in contract.get("endpoints", []):
            if not isinstance(endpoint, Mapping):
                raise ValueError("Endpoint 必须是对象。")
            key = (str(contract.get("id") or ""), str(endpoint.get("id") or ""))
            if key in result:
                raise ValueError(f"Endpoint 复合身份重复：{key}。")
            result[key] = (contract, endpoint)
    return result


def resolve_endpoint_key(catalog: Mapping, endpoint_id: str, contract_id: str = "") -> tuple[str, str]:
    """解析显式契约或当前作用域内唯一短标识，歧义不取第一条。"""
    matches = [key for key in catalog if key[1] == endpoint_id and (not contract_id or key[0] == contract_id)]
    if len(matches) != 1:
        raise ValueError(f"Endpoint 缺失或有歧义：{contract_id}/{endpoint_id}，请声明 apiContractId。")
    return matches[0]


def page_endpoint_keys(plan: Mapping, page: Mapping) -> list[tuple[str, str]]:
    """将页面紧凑引用与正式复合依赖绑定，保持同名接口的契约边界。"""
    return scoped_page_endpoint_keys(endpoint_catalog(plan), page)


def scoped_page_endpoint_keys(catalog: Mapping, page: Mapping) -> list[tuple[str, str]]:
    """在实时或冻结的接口身份目录内解析页面声明，不依赖另一份计划副本。"""
    dependencies = (page.get("technicalReferences") or {}).get("endpointDependencies")
    if dependencies is None:
        dependencies = (page.get("references") or {}).get("endpoint_dependencies", [])
    keys = []
    for dependency in dependencies:
        key = resolve_endpoint_key(catalog, str(dependency.get("endpoint_id") or ""),
                                   str(dependency.get("api_contract_id") or ""))
        if key not in keys:
            keys.append(key)
    for endpoint_id in page.get("requiredEndpointIds", []):
        if not any(key[1] == endpoint_id for key in keys):
            keys.append(resolve_endpoint_key(catalog, endpoint_id))
    return keys
