"""将当前正式 Runtime 事实编译为 Python/Agent 检查，不接收模型自报验收规则。"""

from copy import deepcopy

from app.services.runtime_agent_contracts import agent_contract_index
from app.services.runtime_agent_module_policy import agent_module_facts, module_task_source
from app.services.runtime_business_contract import serves_python_business

PYTHON_CHECK_KINDS = {
    "backend.objects": "python.objects_contract",
    "backend.repository": "python.repository_contract",
    "backend.application_service": "python.application_service_contract",
    "backend.endpoint_controller": "python.endpoint_contract",
    "backend.upstream": "python.upstream_contract",
    "backend.bootstrap": "python.bootstrap_contract",
}
RUNTIME_VERIFIER_NAMES = {kind: kind.replace(".", "_") for kind in (*PYTHON_CHECK_KINDS.values(), "agent.module_contract")}


def endpoint_entity_ids(contracts: list[dict]) -> set[str]:
    """以当前接口契约声明的实体界定业务输入，避免 Agent 的其他工具污染复用判断。"""
    return {entity_id for api in contracts for entity_id in api.get("entity_ids", [])}


def dependency_paths(tasks: list[dict]) -> dict[str, list[str]]:
    """仅纳入当前 DAG 显式依赖交付文件，供只读类型/调用追踪，不沿目录递归扫描。"""

    registry = {str(task.get("id")): task for task in tasks}
    result = {}
    for task_id, task in registry.items():
        pending, visited, paths = list(task.get("dependencies") or []), set(), set()
        while pending:
            key = str(pending.pop())
            if key in visited or key not in registry:
                continue
            visited.add(key)
            dependency = registry[key]
            paths.update(path for item in dependency.get("deliverables") or [] for path in item.get("paths") or []
                         if isinstance(path, str) and path.startswith("agent-runtime/src/app/") and path.endswith(".py"))
            pending.extend(dependency.get("dependencies") or [])
        result[task_id] = sorted(paths)
    return result


def topology_facts(plan: dict) -> dict:
    """只绑定实际消费的服务图，不让无关规划字段导致所有任务失效。"""

    return {key: deepcopy((plan.get("topology") or {}).get(key)) for key in (
        "type", "serviceIds", "publicEdgeServiceId", "authenticationTermination",
    )}


def compile_runtime_check(task: dict, deliverable: dict, formal: dict) -> list[dict] | None:
    """在原交付物身份下替换语言相关验收；Java 和前端继续原编译器。"""

    from app.services.business_acceptance import (
        _api_sources, _business_check, _endpoint_expectations, _endpoint_detail_sources,
        _entity_expectation, _external_api_expectations, _external_designs,
        _operation_expectations, _selected_entities, _source,
    )

    plan = formal["project_plan"]
    if not serves_python_business(plan):
        return None
    kind = deliverable["kind"]
    topology = _source("runtime_topology", "agent-runtime", "/topology", topology_facts(plan))
    if kind == "agent.runtime":
        agent_id = deliverable["target_id"]
        _, module, _ = module_task_source(task)
        facts = agent_module_facts(plan, agent_id, module)
        contract = agent_contract_index(plan)[agent_id]
        expected = {"agent_id": agent_id, "module": module, "configuration": deepcopy(contract["agentSettings"][module]),
                    "agent_count": len(agent_contract_index(plan)), "formal_agent_ids": sorted(agent_contract_index(plan)),
                    "identity": {key: contract[key] for key in ("name", "purpose")},
                    "support_paths": formal["runtime_dependency_paths"]}
        source = _source("agent_module", agent_id, f"/agent_contracts/{agent_id}/agentSettings/{module}", facts)
        if module in {"tools", "knowledge"}:
            expected["tools_configuration"] = deepcopy(contract["agentSettings"]["tools"])
            expected["tool_endpoints"] = [
                {"api_contract_id": api["id"], "endpoint_id": endpoint["id"], "method": endpoint["method"].upper(), "path": endpoint["path"]}
                for api in plan.get("api_contracts", []) for endpoint in api.get("endpoints", [])
                if any(binding["apiContractId"] == api["id"] and binding["endpointId"] == endpoint["id"]
                       for binding in contract["agentSettings"]["tools"]["bindings"])
            ]
            if module == "knowledge":
                sources = [topology, source,
                           _source("agent_module", agent_id, f"/agent_contracts/{agent_id}/agentSettings/tools",
                                   agent_module_facts(plan, agent_id, "tools"))]
            else:
                sources = [topology, source]
        else:
            sources = [topology, source]
        return [_business_check(task, deliverable, "agent.module_contract", "按正式七模块设置验证真实 Agent 装配和可信上下文。",
                                sources, expected)]
    if kind not in PYTHON_CHECK_KINDS:
        return None
    # Python 业务职责只消费自身业务 Endpoint。上下文同时包含 Agent 接入设计时，
    # 不能将整份上下文挂到 bootstrap 等 Unit，造成平台自行生成越界验收来源。
    endpoint_ids = set(formal.get("endpoint_ids") or [])
    contract_ids = set(formal.get("api_contract_ids") or [])
    scoped_contracts = []
    for api in formal.get("contracts", []):
        if contract_ids and api["id"] not in contract_ids:
            continue
        business_endpoints = [endpoint for endpoint in api.get("endpoints", [])
                              if endpoint.get("kind") == "business"
                              and (not endpoint_ids or endpoint["id"] in endpoint_ids)]
        if business_endpoints:
            scoped_contracts.append({**api, "endpoints": business_endpoints})
    keys = {(api["id"], endpoint["id"]) for api in scoped_contracts for endpoint in api["endpoints"]}
    entity_ids = endpoint_entity_ids(scoped_contracts)
    formal = {**formal, "contracts": scoped_contracts,
              "entity_details": [entity for entity in formal.get("entity_details", [])
                                 if entity["entity_id"] in entity_ids],
              "endpoint_designs": [design for design in formal.get("endpoint_designs", [])
                  if design.get("implementationKind") == "business"
                  and (design.get("apiContractId"), design.get("endpointId")) in keys]}
    endpoints = _endpoint_expectations(formal)
    entities = []
    sources = [topology, *_api_sources(formal, endpoints), *_endpoint_detail_sources(formal)]
    raw_entities = {entity["id"]: entity for entity in plan.get("entities", [])}
    for entity in _selected_entities(formal):
        raw = raw_entities[entity["entity_id"]]
        projected = _entity_expectation(entity)
        projected["database_table"] = raw.get("table_name") or ""
        projected["fields"] = deepcopy(raw.get("fields") or [])
        entities.append(projected)
        sources.append(_source("runtime_entity", raw["id"], f"/entities/{raw['id']}", raw))
    expected = {"entities": entities, "endpoints": endpoints, "operations": _operation_expectations(formal),
                "endpoint_designs": deepcopy(formal["endpoint_designs"]), "support_paths": formal["runtime_dependency_paths"]}
    from app.services.runtime_agent_calls import agent_call_response_schema
    expected["agent_calls"] = [{"binding": endpoint["agentCall"], "responseSchema": agent_call_response_schema(api, endpoint),
                                "apiContractId": api["id"], "endpointId": endpoint["id"]}
                               for api in scoped_contracts for endpoint in api["endpoints"] if endpoint.get("agentCall")]
    if kind == "backend.upstream":
        expected["external_apis"] = [item for design in _external_designs(formal)
                                     for item in _external_api_expectations(design, endpoints)]
    description = {"backend.objects": "核对 Pydantic/领域模型字段、类型、必填和枚举及 API DTO 结构。",
                   "backend.repository": "核对正式物理表、参数绑定、读写操作和可信 Principal 归属。",
                   "backend.application_service": "核对字段映射、Repository 委托、事务和真实返回链。",
                   "backend.endpoint_controller": "核对同一 FastAPI handler 的 method/path、参数、响应和服务委托。",
                   "backend.upstream": "核对正式上游 method/path、参数和错误处理。",
                   "backend.bootstrap": "核对模板数据库/HTTP 基础装配，拒绝 DDL 或凭据硬编码。"}[kind]
    return [_business_check(task, deliverable, PYTHON_CHECK_KINDS[kind], description, sources, expected)]
