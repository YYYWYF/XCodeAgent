"""从独立 Agent 能力物化显式公开入口，内部业务调用不要求公开聊天路由。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from starlette.routing import compile_path

from app.domain.runtime_endpoint_contract import RuntimeInvocation, AgentExecutionContract
from app.services.planning_frozen import plain_json
from app.services.runtime_agent_contracts import (
    agent_contract_index, agent_fact_digest, agent_invocation, validate_agent_interaction_contract,
)
from app.services.runtime_business_contract import serves_python_business
from app.workspace.endpoint_design_documents import endpoint_design_stem


def runtime_endpoint_identity(agent_id: str) -> tuple[str, str]:
    """只从合法正式 Agent ID 派生复合身份，禁止调用者自报路径或接口 ID。"""

    if not isinstance(agent_id, str) or len(agent_id) > 128:
        raise ValueError("Agent 身份必须是字符串且不能超过 128 个字符。")
    invocation = agent_invocation(agent_id)
    return f"agent.{agent_id}", invocation["endpointId"]


def _current_agent_contracts(plan: Mapping[str, Any], *, require_topology: bool = True) -> dict[str, dict]:
    """严格要求新能力声明，并复用七模块校验；不读取或升级缺声明的旧合同。"""

    raw = plain_json(plan)
    agents = raw.get("agent_contracts")
    if not isinstance(agents, list):
        raise ValueError("当前合同必须显式提供 agent_contracts 数组。")
    technical_agents, capabilities = [], {}
    for index, agent in enumerate(agents):
        required = {"agentId", "name", "purpose", "pageIds", "publicEndpoint", "agentSettings", "interactionContract"}
        if not isinstance(agent, dict) or set(agent) != required:
            raise ValueError("当前 Agent 合同必须包含完整七模块及显式 interactionContract。")
        agent_id = agent["agentId"]
        if not isinstance(agent_id, str):
            raise ValueError("Agent 身份必须是字符串。")
        capabilities[agent_id] = validate_agent_interaction_contract(
            agent["interactionContract"], agent_id=agent_id,
            field_path=f"agent_contracts[{index}].interactionContract",
        )
        technical_agents.append(deepcopy(agent))
    # 复用既有校验的是七模块事实，并非为缺失的新合同提供兼容读取。
    contracts = agent_contract_index({**raw, "agent_contracts": technical_agents}, require_runtime=require_topology)
    for agent_id, agent in contracts.items():
        agent["interactionContract"] = capabilities[agent_id]
    return contracts


def _runtime_contract(agent: dict) -> dict:
    """物化唯一固定入口，不向请求/响应实体 Schema 或字段映射伪造空对象。"""

    agent_id = agent["agentId"]
    contract_id, endpoint_id = runtime_endpoint_identity(agent_id)
    invocation = RuntimeInvocation.model_validate(agent_invocation(agent_id)).model_dump(mode="json", by_alias=True)
    return {
        "id": contract_id, "name": agent["name"] + "运行接口", "entity_ids": [],
        "base_path": invocation["path"], "authentication": {"required": False}, "schemas": {},
        "endpoints": [{
            "id": endpoint_id, "name": agent["name"] + "会话", "kind": "agent_runtime", "agent_id": agent_id,
            "method": invocation["method"], "path": invocation["path"], "summary": agent["purpose"],
            "parameters": [], "request_schema_ref": None, "response_schema_ref": None,
            "error_codes": [], "authentication": {"required": False},
            "invocation": invocation, "interactionContract": deepcopy(agent["interactionContract"]),
        }],
    }


def _validate_identities(contracts: list[dict]) -> None:
    """同时检查复合身份、实际路由与设计 stem 冲突，不按 endpointId 取第一项。"""

    contract_ids, identities, routes, stems, runtime_routes = set(), set(), set(), {}, []
    business_routes = []
    for contract in contracts:
        contract_id = contract.get("id")
        if not isinstance(contract_id, str) or not contract_id.strip() or len(contract_id) > 256 or contract_id in contract_ids:
            raise ValueError("API Contract 身份必须是非空唯一字符串且不超过 256 字符。")
        contract_ids.add(contract_id)
        endpoints = contract.get("endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            raise ValueError(f"API Contract {contract_id} 必须包含 Endpoint。")
        for endpoint in endpoints:
            if not isinstance(endpoint, dict) or not isinstance(endpoint.get("kind"), str) or endpoint["kind"] not in {"business", "agent_runtime"}:
                raise ValueError("Endpoint.kind 必须显式声明为 business 或 agent_runtime。")
            endpoint_id = endpoint.get("id")
            if not isinstance(endpoint_id, str) or not endpoint_id.strip() or len(endpoint_id) > 256:
                raise ValueError("Endpoint 身份必须是非空字符串且不超过 256 字符。")
            identity = (contract_id, endpoint_id)
            if identity in identities:
                raise ValueError(f"Endpoint 复合身份重复：{identity}。")
            identities.add(identity)
            method, path = endpoint.get("method"), endpoint.get("path")
            if not isinstance(method, str) or method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                raise ValueError(f"Endpoint {identity} 必须使用明确的大写 HTTP 方法。")
            if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
                raise ValueError(f"Endpoint {identity} 必须使用不含查询或片段的绝对路由。")
            route = (method, path)
            if route in routes:
                raise ValueError(f"Endpoint 实际路由重复：{route}。")
            routes.add(route)
            if endpoint["kind"] == "agent_runtime":
                runtime_routes.append(route)
            else:
                business_routes.append(route)
            stem = endpoint_design_stem(*identity)
            if stem in stems:
                raise ValueError(f"Endpoint 设计文件名冲突：{stems[stem]} 与 {identity}。")
            stems[stem] = identity
    # 参数路由也可能截获固定运行入口，不能只比较路由字面值。
    for method, path in business_routes:
        try:
            pattern, _, _ = compile_path(path)
        except (ValueError, AssertionError) as exc:
            raise ValueError(f"业务 Endpoint 路由无效：{path}。") from exc
        if any(method == runtime_method and pattern.fullmatch(runtime_path)
               for runtime_method, runtime_path in runtime_routes):
            raise ValueError(f"业务参数路由会截获 Agent 运行入口：{method} {path}。")


def materialize_runtime_endpoints(plan: Mapping[str, Any], *, refresh_derived: bool = False,
                                  require_topology: bool = True) -> dict:
    """编译显式当前合同并验证已有派生值，重复调用稳定且不修改传入对象。"""

    # 候选接口可先独立复验；正式编译及已声明架构仍必须遵守服务图边界。
    check_topology = require_topology or plan.get("topology") is not None
    if check_topology and not serves_python_business(plan):
        raise ValueError("运行 Endpoint 只能编译到显式 agent_runtime_direct 技术规划。")
    result = plain_json(plan)
    agents = _current_agent_contracts(result, require_topology=check_topology)
    contracts = result.get("api_contracts")
    if not isinstance(contracts, list) or any(not isinstance(item, dict) for item in contracts):
        raise ValueError("当前 api_contracts 必须显式提供数组。")
    _validate_identities(contracts)
    expected = {runtime_endpoint_identity(agent_id)[0]: _runtime_contract(agent) for agent_id, agent in agents.items()
                if agent["publicEndpoint"]}
    retained, published = [], set()
    for index, contract in enumerate(contracts):
        contract_id = contract.get("id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise ValueError("API Contract 身份必须是非空字符串。")
        endpoints = contract.get("endpoints")
        if not isinstance(endpoints, list) or any(not isinstance(item, dict) for item in endpoints):
            raise ValueError("API Contract.endpoints 必须是 Endpoint 对象数组。")
        if refresh_derived and any(item.get("kind") == "agent_runtime" for item in endpoints):
            # 仅供正式规划/修订候选重编译：先证明旧条目是完整当前派生形状，
            # 再从新 Agent 权威重建显示字段；开发/启动读取仍严格拒绝任何不一致。
            if len(endpoints) != 1:
                raise ValueError("运行 Contract 必须且只能包含一个派生 Endpoint。")
            previous = endpoints[0]
            name, purpose, identity = contract.get("name"), previous.get("summary"), previous.get("agent_id")
            if not isinstance(name, str) or not name.endswith("运行接口") or not isinstance(purpose, str):
                raise ValueError("旧运行入口不是当前合法派生形状，不能重编译。")
            capability = AgentExecutionContract.model_validate(previous.get("interactionContract")).model_dump(mode="json", by_alias=True)
            expected_previous = _runtime_contract({"agentId": identity, "name": name.removesuffix("运行接口"),
                "purpose": purpose, "interactionContract": capability})
            if contract != expected_previous:
                raise ValueError("运行入口身份、路径或能力被独立改写，不能通过候选重编译掩盖。")
            if contract_id not in expected:
                continue
            contract = expected[contract_id]
        if contract_id in expected:
            if contract != expected[contract_id] or contract_id in published:
                raise ValueError(f"运行 Endpoint {contract_id} 的派生身份、绑定或能力已被修改/占用。")
            published.add(contract_id)
        else:
            if any(item.get("kind") == "agent_runtime" for item in endpoints):
                raise ValueError("存在未绑定当前正式 Agent 的运行 Endpoint。")
            if any("agent_id" in item or "invocation" in item or "interactionContract" in item for item in endpoints):
                raise ValueError("business Endpoint 不能携带 Agent 运行合同字段。")
            retained.append(deepcopy(contract))
    result["api_contracts"] = retained + [deepcopy(expected[key]) for key in sorted(expected)]
    _validate_identities(result["api_contracts"])
    return result


def agent_execution_capability_snapshot(plan: Mapping[str, Any], agent_id: str) -> dict:
    """为内部和公开 Agent 共用执行快照，不要求存在公开运行 Endpoint。"""
    contracts = _current_agent_contracts(plan)
    if agent_id not in contracts:
        raise ValueError("执行目标不是当前正式 Agent。")
    agent = contracts[agent_id]
    contract_id, endpoint_id = runtime_endpoint_identity(agent_id)
    capability = {"apiContractId": contract_id, "endpointId": endpoint_id, "agentId": agent_id,
                  "invocation": agent_invocation(agent_id, public=agent["publicEndpoint"]),
                  "interactionContract": deepcopy(agent["interactionContract"])}
    return {**capability, "capabilitySha256": agent_fact_digest(capability)}


def runtime_endpoint_capability_snapshot(plan: Mapping[str, Any], api_contract_id: str, endpoint_id: str) -> dict:
    """使用完整复合身份定位唯一运行入口，生成后续设计和策略共用的语义快照。"""

    current = materialize_runtime_endpoints(plan)
    matches = [(contract, endpoint) for contract in current["api_contracts"] for endpoint in contract["endpoints"]
               if contract["id"] == api_contract_id and endpoint["id"] == endpoint_id]
    if len(matches) != 1 or matches[0][1]["kind"] != "agent_runtime":
        raise ValueError("指定复合身份不是唯一正式运行 Endpoint。")
    _, endpoint = matches[0]
    capability = {
        "apiContractId": api_contract_id, "endpointId": endpoint_id, "agentId": endpoint["agent_id"],
        "invocation": deepcopy(endpoint["invocation"]), "interactionContract": deepcopy(endpoint["interactionContract"]),
    }
    return {**capability, "capabilitySha256": agent_fact_digest(capability)}
