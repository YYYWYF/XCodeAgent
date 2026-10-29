"""为应用级 DAG 组装当前已确认 Endpoint 映射上下文。"""

from pathlib import Path
from typing import Any
from app.services.runtime_agent_contracts import agent_contract_index
from app.services.agent_runtime_template_policy import load_agent_runtime_template_policy

from app.services.api_design import (
    api_design_business_descriptions,
    api_design_mapping_flows,
    api_design_source_types,
    load_confirmed_endpoint_designs,
)


def application_endpoint_context(workspace: str | Path, project_plan: dict[str, Any]) -> dict[str, Any]:
    """逐 Contract 加载当前映射，避免应用范围遗漏接口或混淆跨 Contract 同名 ID。"""

    designs: list[dict[str, Any]] = []
    endpoint_ids: list[str] = []
    entity_ids: list[str] = []
    endpoint_refs: list[dict[str, str]] = []
    for contract in project_plan.get("api_contracts") or []:
        contract_id = contract["id"]
        ids = [endpoint["id"] for endpoint in contract.get("endpoints") or []]
        # 使用与页面/接口开发相同的正式读取和完整映射校验，不消费门禁快照。
        designs.extend(load_confirmed_endpoint_designs(
            workspace, project_plan, ids, api_contract_id=contract_id,
        ))
        endpoint_ids.extend(ids)
        endpoint_refs.extend({"id": endpoint_id, "api_contract_id": contract_id} for endpoint_id in ids)
        if ids:
            entity_ids.extend(contract.get("entity_ids") or [])
    flows = api_design_mapping_flows(designs)
    descriptions = api_design_business_descriptions(designs)
    return {
        **({"runtime_agent_template_policy": load_agent_runtime_template_policy(Path(workspace) / "agent-runtime")}
           if agent_contract_index(project_plan) else {}),
        "endpoint_ids": list(dict.fromkeys(endpoint_ids)),
        "required_endpoint_ids": list(dict.fromkeys(endpoint_ids)),
        "entity_ids": list(dict.fromkeys(entity_ids)),
        "endpoint_designs": designs,
        "source_types": api_design_source_types(designs),
        "mapping_flows": flows,
        "business_descriptions": descriptions,
        "source_refs": {
            "technical_plan_endpoints": endpoint_refs,
            "endpoint_designs": designs,
            "mapping_flows": flows,
            "business_descriptions": descriptions,
        },
    }
