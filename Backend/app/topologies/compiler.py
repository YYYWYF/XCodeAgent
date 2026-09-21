"""集中选择和编译拓扑，禁止下游模块重复推断工程结构。"""

from __future__ import annotations

from typing import Any

from app.topologies.model import TopologyBlueprint, TopologyContext, TopologyType
from app.topologies.registry import registered_topologies, registered_topology


def resolve_registered_topology(
    technical_plan: dict[str, Any],
    application_config: dict[str, Any],
) -> TopologyBlueprint | None:
    """按正式候选事实自动匹配唯一拓扑；无匹配时保留尚未迁移的当前流程。"""

    context = TopologyContext(
        technical_plan=technical_plan,
        application_config=application_config,
    )
    matches = [definition for definition in registered_topologies() if definition.matches(context)]
    if len(matches) > 1:
        names = "、".join(definition.type.value for definition in matches)
        raise ValueError(f"正式事实同时匹配多个应用拓扑：{names}。")
    if not matches:
        return None
    definition = matches[0]
    return TopologyBlueprint(
        type=definition.type,
        design=definition.compile_design(context),
        planning=definition.compile_planning(context),
        development=definition.compile_development(context),
    )


def compile_registered_topology(
    topology_type: TopologyType,
    technical_plan: dict[str, Any],
    application_config: dict[str, Any],
) -> TopologyBlueprint:
    """按已确认枚举编译拓扑，并重新验证当前事实仍满足其不变量。"""

    context = TopologyContext(
        technical_plan=technical_plan,
        application_config=application_config,
    )
    definition = registered_topology(topology_type)
    if not definition.matches(context):
        raise ValueError(f"当前正式事实不再满足拓扑 {topology_type.value}。")
    return TopologyBlueprint(
        type=definition.type,
        design=definition.compile_design(context),
        planning=definition.compile_planning(context),
        development=definition.compile_development(context),
    )


def topology_type_from_plan(plan: dict[str, Any]) -> TopologyType | None:
    """读取已确认 TechnicalPlan 的拓扑枚举；未迁移计划返回 None。"""

    topology = plan.get("topology") if isinstance(plan.get("topology"), dict) else None
    raw_type = topology.get("type") if topology is not None else None
    if raw_type is None:
        return None
    try:
        return TopologyType(str(raw_type))
    except ValueError as exc:
        raise ValueError(f"TechnicalPlan.topology.type 不受支持：{raw_type}。") from exc

