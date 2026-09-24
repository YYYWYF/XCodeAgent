"""把产品计划和技术计划归一化为唯一的拓扑判定事实。"""

from __future__ import annotations

from typing import Any

from app.topologies.model import TopologyFacts, TopologyFactSource


def extract_topology_facts(
    artifact: dict[str, Any],
    application_config: dict[str, Any],
    *,
    source: TopologyFactSource,
) -> TopologyFacts:
    """按事实来源提取统一结构，禁止调用方自行复制拓扑判据。"""

    auth = application_config.get("auth")
    authorization = application_config.get("authorization")
    auth_enabled = (
        auth.get("enable")
        if isinstance(auth, dict) and type(auth.get("enable")) is bool
        else None
    )
    authorization_enabled = (
        authorization.get("enabled")
        if isinstance(authorization, dict)
        and type(authorization.get("enabled")) is bool
        else None
    )
    if source is TopologyFactSource.PRODUCT_PLAN:
        return _product_plan_facts(
            artifact,
            auth_enabled=auth_enabled,
            authorization_enabled=authorization_enabled,
        )
    if source is TopologyFactSource.TECHNICAL_PLAN:
        return _technical_plan_facts(
            artifact,
            auth_enabled=auth_enabled,
            authorization_enabled=authorization_enabled,
        )
    raise ValueError(f"不支持的拓扑事实来源：{source}。")


def _product_plan_facts(
    product_plan: dict[str, Any],
    *,
    auth_enabled: bool | None,
    authorization_enabled: bool | None,
) -> TopologyFacts:
    """提取 TechnicalPlan 生成前已确认的 Agent Surface 与业务动作事实。"""

    surface_agent_ids: list[str] = []
    for agent in _dict_items(product_plan.get("agents")):
        agent_id = str(agent.get("agentId") or "").strip()
        has_enabled_surface = any(
            isinstance(binding.get("surface"), dict)
            and binding["surface"].get("enabled") is True
            for binding in _dict_items(agent.get("pageActionBindings"))
        )
        if agent_id and has_enabled_surface and agent_id not in surface_agent_ids:
            surface_agent_ids.append(agent_id)

    business_action_ids: list[str] = []
    for page in _dict_items(product_plan.get("pages")):
        for action in _dict_items(page.get("actions")):
            action_id = str(action.get("actionId") or "").strip()
            if (
                action_id
                and _product_action_requires_business_backend(action)
                and action_id not in business_action_ids
            ):
                business_action_ids.append(action_id)

    return TopologyFacts(
        source=TopologyFactSource.PRODUCT_PLAN,
        auth_enabled=auth_enabled,
        authorization_enabled=authorization_enabled,
        agent_ids=tuple(surface_agent_ids),
        backend_requirement_ids=tuple(
            f"business_action:{action_id}" for action_id in business_action_ids
        ),
        surface_agent_ids=tuple(surface_agent_ids),
        business_action_ids=tuple(business_action_ids),
    )


def _technical_plan_facts(
    technical_plan: dict[str, Any],
    *,
    auth_enabled: bool | None,
    authorization_enabled: bool | None,
) -> TopologyFacts:
    """提取正式 TechnicalPlan 的服务端依赖和 Agent Contract 事实。"""

    contracts = _dict_items(technical_plan.get("agent_contracts"))
    agent_ids = _stable_ids(contracts, "agentId")
    backend_requirements: list[str] = []
    for index, entity in enumerate(_dict_items(technical_plan.get("entities"))):
        backend_requirements.append(
            f"entity:{_item_identity(entity, ('id', 'entityId'), index)}"
        )
    for index, contract in enumerate(_dict_items(technical_plan.get("api_contracts"))):
        backend_requirements.append(
            f"api_contract:{_item_identity(contract, ('contractId', 'id'), index)}"
        )
    for page_index, page in enumerate(_dict_items(technical_plan.get("pages"))):
        page_id = _item_identity(page, ("pageId",), page_index)
        references = page.get("references")
        references = references if isinstance(references, dict) else {}
        for dependency_index, dependency in enumerate(
            _dict_items(references.get("endpoint_dependencies"))
        ):
            dependency_id = _item_identity(
                dependency,
                ("endpointId", "contractId", "id"),
                dependency_index,
            )
            backend_requirements.append(f"page_endpoint:{page_id}:{dependency_id}")
    for contract_index, contract in enumerate(contracts):
        if _has_backend_tool(contract):
            agent_id = _item_identity(contract, ("agentId",), contract_index)
            backend_requirements.append(f"backend_tool:{agent_id}")

    return TopologyFacts(
        source=TopologyFactSource.TECHNICAL_PLAN,
        auth_enabled=auth_enabled,
        authorization_enabled=authorization_enabled,
        agent_ids=agent_ids,
        backend_requirement_ids=tuple(dict.fromkeys(backend_requirements)),
    )


def _product_action_requires_business_backend(action: dict[str, Any]) -> bool:
    """判断产品动作是否包含必须由业务后端实现的语义。"""

    behavior = action.get("behavior")
    if not isinstance(behavior, dict):
        return True
    behavior_type = str(behavior.get("type") or "business").strip().lower()
    if behavior_type == "sequence":
        steps = behavior.get("steps")
        if not isinstance(steps, list):
            return True
        return any(
            not isinstance(step, dict)
            or str(step.get("type") or "business").strip().lower() == "business"
            for step in steps
        )
    return behavior_type not in {"interface", "navigation", "external"}


def _has_backend_tool(contract: dict[str, Any]) -> bool:
    """识别任何 Java Endpoint Tool 或旧 Endpoint 展开形状。"""

    settings = contract.get("agentSettings")
    settings = settings if isinstance(settings, dict) else {}
    tools = settings.get("tools")
    tools = tools if isinstance(tools, dict) else {}
    for binding in _dict_items(tools.get("bindings")):
        source = binding.get("source")
        source = source if isinstance(source, dict) else {}
        if source.get("type") == "backend_endpoint" or isinstance(
            binding.get("endpoint"), dict
        ):
            return True
    return False


def _item_identity(
    item: dict[str, Any],
    keys: tuple[str, ...],
    fallback_index: int,
) -> str:
    """返回事实项的稳定标识，缺少业务标识时保留输入位置。"""

    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return f"index-{fallback_index}"


def _stable_ids(items: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    """按输入顺序提取非空且去重的对象标识。"""

    return tuple(
        dict.fromkeys(
            str(item.get(key) or "").strip()
            for item in items
            if str(item.get(key) or "").strip()
        )
    )


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从数组中提取对象项。"""

    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )
