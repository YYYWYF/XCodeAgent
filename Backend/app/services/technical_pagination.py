"""按当前 .list 合同补齐候选分页结构，不替换业务字段或正式确认。"""
from copy import deepcopy


def complete_technical_pagination(plan: dict) -> dict:
    """仅补充可确定的标准字段；复杂或共享响应保留给严格校验和正式修订。"""
    result = deepcopy(plan)
    fields = {"total", "pageSize", "current", "list"}
    for contract in result.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        schemas = contract.get("schemas")
        endpoints = contract.get("endpoints")
        if not isinstance(schemas, dict) or not isinstance(endpoints, list):
            continue
        for endpoint in endpoints:
            if not isinstance(endpoint, dict) or not str(endpoint.get("id", "")).endswith(".list"):
                continue
            parameters = endpoint.get("parameters")
            if not isinstance(parameters, list):
                continue
            # 只追加缺失参数，不覆盖过滤条件及已声明的参数类型和默认值。
            names = {p.get("name") for p in parameters if isinstance(p, dict) and p.get("in", "query") == "query"}
            for name, default, description in (("current", 1, "当前页码，从 1 开始。"), ("pageSize", 20, "每页返回的记录条数。")):
                if name not in names:
                    parameters.append({"name": name, "in": "query", "required": False,
                        "description": description, "schema": {"type": "integer", "minimum": 1, "default": default}})
            reference = endpoint.get("response_schema_ref")
            response = schemas.get(reference) if isinstance(reference, str) else None
            if not isinstance(response, dict) or response.get("type") != "object":
                continue
            properties = response.get("properties")
            required = response.get("required", [])
            if (not isinstance(properties, dict) or not isinstance(required, list)
                    or not set(properties).issubset(fields) or not set(required).issubset(fields)
                    or not isinstance(properties.get("list"), dict) or properties["list"].get("type") != "array"):
                continue
            if sum(isinstance(e, dict) and e.get("response_schema_ref") == reference for e in endpoints) != 1:
                continue
            for name, description in (("total", "符合条件的记录总数。"), ("pageSize", "每页记录条数。"), ("current", "当前页码。")):
                properties.setdefault(name, {"type": "integer", "description": description})
            response["required"] = [*required, *(name for name in ("total", "pageSize", "current", "list") if name not in required)]
    return result
