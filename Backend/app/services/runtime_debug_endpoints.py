"""从当前正式规划投影智能体调用入口，不依赖页面或提供任意 URL 调试。"""

from copy import deepcopy

from app.domain.development_artifacts import DevelopmentArtifacts
from app.services.api_design_schema import resolve_mapping_schema
from app.services.api_schema_refs import normalize_local_schema_ref
from app.services.runtime_agent_contracts import agent_invocation


def debug_endpoints(plan: dict, agent: dict, artifacts: DevelopmentArtifacts | None) -> list[dict]:
    """按显式关联列出已完成开发的业务接口；能力入口沿用智能体自身的调试门禁。"""
    invocation = agent_invocation(agent["agentId"], public=agent["publicEndpoint"])
    result = [{"apiContractId": f"agent.{agent['agentId']}", "endpointId": invocation["endpointId"],
        "label": agent["name"] + ("运行入口" if agent["publicEndpoint"] else "内部能力调试入口"),
        "method": "POST", "path": invocation["path"], "responseMode": "ag-ui", "kind": "agent_runtime", "relation": "agent_entry",
        "parameters": [], "requestSchema": deepcopy(agent["interactionContract"]["inputSchema"]),
        "responseSchema": deepcopy(agent["interactionContract"].get("resultSchema"))}]
    tools = {(item["apiContractId"], item["endpointId"]) for item in agent["agentSettings"]["tools"]["bindings"]}
    for api in plan.get("api_contracts", []):
        for endpoint in api.get("endpoints", []):
            call = endpoint.get("agentCall")
            invokes_agent = bool(call and call["agentId"] == agent["agentId"])
            is_tool = (api["id"], endpoint["id"]) in tools
            if endpoint.get("kind") != "business" or not (invokes_agent or is_tool):
                continue
            # 智能体完成不能代替业务接口完成；缺少记录、开发中或正式输入已变化均不可调试。
            progress = artifacts.endpoints.get(api["id"], {}).get(endpoint["id"]) if artifacts else None
            if not progress or progress.initial_development_status != "completed" or progress.change_status != "unchanged":
                continue
            schemas = api.get("schemas") or {}
            entry = {"apiContractId": api["id"], "endpointId": endpoint["id"],
                "label": endpoint.get("name") or endpoint.get("summary") or endpoint["id"],
                "method": endpoint["method"], "path": endpoint["path"], "kind": "business",
                "responseMode": call["responseMode"] if call else "json",
                "relation": "agent_call" if invokes_agent else "tool",
                "parameters": deepcopy(endpoint.get("parameters") or [])}
            for key in ("request", "response"):
                reference = normalize_local_schema_ref(endpoint.get(f"{key}_schema_ref"), contract_id=api["id"])
                entry[f"{key}Schema"] = resolve_mapping_schema(schemas[reference], schemas, frozenset()) if reference in schemas else None
            result.append(entry)
    # 先展示实际业务接口；独立能力入口仍可选择，不再成为每个智能体的固定调试目标。
    return result[1:] + result[:1]
