"""编译业务接口到 Agent 的显式调用及依赖，不新增 Agent 业务实现。"""

from collections.abc import Mapping
from typing import Any

from app.domain.agent_call import AgentCallBinding
from app.services.planning_frozen import plain_json


def endpoint_agent_call(endpoint: Mapping[str, Any]) -> dict | None:
    """只消费正式 agentCall，普通业务接口没有该字段时不推测模型调用。"""
    value = endpoint.get("agentCall")
    return AgentCallBinding.model_validate(plain_json(value)).model_dump(by_alias=True) if value is not None else None


def agent_call_response_schema(contract: Mapping, endpoint: Mapping) -> dict:
    """解析接口本身的完整响应 Schema，结果映射后仍须遵守普通业务接口合同。"""
    from app.services.api_schema_refs import normalize_local_schema_ref
    from app.services.api_design_schema import resolve_mapping_schema
    from app.domain.agent_execution_schema import validate_execution_schema
    schemas = plain_json(contract.get("schemas") or {})
    reference = endpoint.get("response_schema_ref")
    if not isinstance(reference, str) or not reference:
        raise ValueError("调用 Agent 的业务接口必须声明 response_schema_ref。")
    key = normalize_local_schema_ref(reference, contract_id=contract["id"])
    if key not in schemas:
        raise ValueError("Agent 调用接口缺少正式响应 Schema。")
    resolved = resolve_mapping_schema(schemas[key], schemas, frozenset())
    validate_execution_schema(resolved)
    return resolved


def validate_agent_call_design(plan: dict, endpoint: dict, design: dict, *, persisted: bool = False) -> dict:
    """设计确认和 Build 复验都核对 Agent 当前能力，不能只沿用旧 confirmed 标记。"""
    from app.services.runtime_endpoint_contracts import agent_execution_capability_snapshot
    call = endpoint_agent_call(endpoint)
    if call is None or design.get("agentCall") != call:
        raise ValueError("Agent 调用映射必须与当前 TechnicalPlan 一致；修改请走正式修订。")
    snapshot = agent_execution_capability_snapshot(plan, call["agentId"])
    if design.get("agentCapabilitySha256") != snapshot["capabilitySha256"]:
        raise ValueError("Agent 输入输出能力已变化，请重新审阅并确认接口调用映射。")
    if design.get("fieldMappings") != [] or any(design.get(key) for key in (
            "sourceBinding", "databaseOperation", "databaseWrites", "externalApiBindings", "databaseQuery", "staticData")):
        raise ValueError("Agent 调用接口使用 inputBindings/resultBindings，不能再声明另一套来源映射。")
    if not persisted and set(design) - {"apiContractId", "endpointId", "agentCall", "agentCapabilitySha256", "fieldMappings"}:
        raise ValueError("Agent 调用设计包含未声明字段。")
    return {"agentCall": call, "agentCapabilitySha256": snapshot["capabilitySha256"],
            "fieldMappings": [], "sourceSnapshots": [], "databaseWrites": [], "externalApiBindings": []}


def validate_agent_calls(plan: Mapping[str, Any], agents: dict[str, dict]) -> None:
    """校验目标能力并在正式规划边界拒绝 Agent 与 Tool 的循环调用。"""
    dependencies: dict[str, set[str]] = {f"agent:{key}": set() for key in agents}
    endpoints = {(api["id"], item["id"]): item for api in plan.get("api_contracts", ()) for item in api.get("endpoints", ())}
    for (api_id, endpoint_id), endpoint in endpoints.items():
        node = f"endpoint:{api_id}:{endpoint_id}"
        dependencies.setdefault(node, set())
        call = endpoint_agent_call(endpoint)
        if call is None:
            continue
        agent = agents.get(call["agentId"])
        if endpoint.get("kind") != "business" or agent is None:
            raise ValueError("agentCall 必须由业务 Endpoint 引用当前正式 Agent。")
        if agent["interactionContract"].get("resultSchema") is None:
            raise ValueError("业务接口调用的 Agent 必须声明结构化 resultSchema。")
        api = next(api for api in plan["api_contracts"] if api["id"] == api_id)
        agent_call_response_schema(api, endpoint)
        dependencies[node].add(f"agent:{call['agentId']}")
    for agent_id, agent in agents.items():
        dependencies[f"agent:{agent_id}"].update(
            f"endpoint:{item['apiContractId']}:{item['endpointId']}" for item in agent["agentSettings"]["tools"]["bindings"])
    visited, active = set(), set()

    def visit(node: str) -> None:
        """沿明确调用关系检测环，不把合法共享服务当成冲突。"""
        if node in active:
            raise ValueError(f"Agent/业务接口存在循环依赖：{node}，请拆分 Tool 服务与调用 Agent 的服务。")
        if node in visited:
            return
        active.add(node)
        for dependency in dependencies.get(node, ()):
            visit(dependency)
        active.remove(node)
        visited.add(node)

    for node in dependencies:
        visit(node)
