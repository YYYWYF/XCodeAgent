"""T29 沿现有目标依赖解析绑定当前接口、实体及已确认映射。"""

from pathlib import Path
from typing import Any

from app.services.runtime_endpoint_dependencies import resolve_runtime_target


def formal_dependencies(workspace: Path, plan: dict[str, Any], page_id: str) -> dict[str, Any]:
    """仅投影该页面及 Agent/Tool 闭包，不以无关接口或整份技术规划统一失效。"""
    from app.services.api_design import load_confirmed_endpoint_designs
    resolved = resolve_runtime_target(plan, 'page', page_id)
    endpoint_keys = set(resolved['endpoint_keys'])
    apis: list[dict[str, Any]] = []
    designs: list[dict[str, Any]] = []
    entity_ids: set[str] = set()
    for api in plan.get('api_contracts', []):
        endpoints = [endpoint for endpoint in api.get('endpoints', []) if (api['id'], endpoint['id']) in endpoint_keys]
        if not endpoints:
            continue
        apis.append({**api, 'endpoints': endpoints})
        entity_ids.update(api.get('entity_ids') or [])
        for endpoint in endpoints:
            if endpoint.get('kind') == 'business':
                designs.extend(load_confirmed_endpoint_designs(workspace, plan, [endpoint['id']], api_contract_id=api['id']))
    return {'topology': plan.get('topology'), 'apis': apis, 'endpointDesigns': designs,
            'agents': [agent for agent in plan.get('agent_contracts', []) if agent['agentId'] in resolved['agent_ids']],
            'page': next(page for page in plan['pages'] if page.get('pageId') == page_id),
            'pageImplementation': [item for item in plan.get('page_implementation_contracts', []) if item.get('pageId') == page_id],
            'entities': [entity for entity in plan.get('entities', []) if entity['id'] in entity_ids]}
