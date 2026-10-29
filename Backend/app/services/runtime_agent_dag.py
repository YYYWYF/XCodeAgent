"""七模块的 Unit、职责与固定任务身份；调度和确认仍由公共 DAG 服务负责。"""

from collections.abc import Mapping
from typing import Any

from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES
from app.services.runtime_agent_contracts import agent_contract_index, agent_endpoint_keys, MODULE_LABELS
from app.services.unit_generation_requirement_targets import responsibility


def extend_agent_units(plan: dict, units: dict, existing: dict) -> None:
    """只在显式 Runtime 应用中增加 Agent Unit，保留公共业务 Unit。"""

    for agent_id in agent_contract_index(plan):
        unit_id = f"agent:{agent_id}"
        previous = existing.get(unit_id) or {}
        units[unit_id] = {"id": unit_id, "kind": "agent", "agent_id": agent_id,
                          "status": previous.get("status", "not_prepared"),
                          "task_ids": list(previous.get("task_ids") or []),
                          "source_refs": dict(previous.get("source_refs") or {}),
                          "depends_on_unit_ids": []}


def extend_agent_edges(plan: dict, units: dict, edges: list[dict], errors: list[str]) -> None:
    """Tool 依赖现有 Endpoint Unit，绑定页面依赖 Agent；不增加网关或 SQL Unit。"""

    for agent_id, contract in agent_contract_index(plan).items():
        unit_id = f"agent:{agent_id}"
        edges.append({"from": "application:root", "to": unit_id, "type": "contains"})
        for contract_id, endpoint_id in agent_endpoint_keys(contract):
            dependency = f"backend:endpoint:{contract_id}:{endpoint_id}"
            if dependency not in units:
                errors.append(f"Agent {agent_id} 缺少 Tool 实现 Unit {dependency}。")
            else:
                edges.append({"from": dependency, "to": unit_id, "type": "depends_on"})
        for page_id in contract["pageIds"]:
            if f"page:{page_id}" not in units:
                errors.append(f"Agent {agent_id} 的页面 {page_id} 没有 Unit。")
            else:
                edges.append({"from": unit_id, "to": f"page:{page_id}", "type": "depends_on"})
        edges.append({"from": unit_id, "to": "app:integration", "type": "depends_on"})


def agent_requirements(unit_id: str, plan: dict) -> list:
    """固定枚举七模块，每模块一条独立 capability，不根据 enabled 提前伪造完成。"""

    agent_id = unit_id.removeprefix("agent:")
    if agent_id not in agent_contract_index(plan):
        raise ValueError(f"Agent Unit {unit_id} 不属于当前正式技术规划。")
    return [responsibility(
        f"agent.{agent_id}.{module}", description=f"实现 {agent_id} 的{MODULE_LABELS[module]}模块。",
        kind="agent.runtime", target_id=agent_id, agent_id=agent_id, agent_module=module,
    ) for module in AGENT_RUNTIME_MODULES]


def agent_task_manifest(context: Any) -> tuple[dict, ...]:
    """复用 Worker 的增量职责输入，固定模块任务 ID 及候选内串行依赖。"""

    agent_id = context.unit_id.removeprefix("agent:")
    requirements = {}
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        module = refs.get("agent_module")
        if (module not in AGENT_RUNTIME_MODULES or module in requirements
                or refs.get("kind") != "agent.runtime" or refs.get("agent_id") != agent_id
                or refs.get("target_id") != agent_id or requirement.requirement_id != f"agent.{agent_id}.{module}"):
            raise ValueError("Agent 增量职责与七模块固定身份不一致。")
        requirements[module] = requirement.requirement_id
    retained = {item.get("id") for item in context.dependency_context.get("retained_task_summaries", ())
                if isinstance(item, Mapping) and item.get("unit_id") == context.unit_id}
    available = retained | {f"{context.unit_id}::{module}" for module in requirements}
    result = []
    for index, module in enumerate(AGENT_RUNTIME_MODULES):
        if module not in requirements:
            continue
        predecessor = next((f"{context.unit_id}::{previous}"
                            for previous in reversed(AGENT_RUNTIME_MODULES[:index])
                            if f"{context.unit_id}::{previous}" in available), None)
        result.append({"task_id": f"{context.unit_id}::{module}", "module": module,
                       "requirement_id": requirements[module], "dependencies": [predecessor] if predecessor else []})
    return tuple(result)
