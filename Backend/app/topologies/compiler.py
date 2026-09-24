"""集中选择和编译拓扑，禁止下游模块重复推断工程结构。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.topologies.facts import extract_topology_facts
from app.topologies.model import (
    TopologyBlueprint,
    TopologyContext,
    TopologyEvaluation,
    TopologyFactSource,
    TopologyResolution,
    TopologyType,
)
from app.topologies.protocols import ApplicationTopologyDefinition
from app.topologies.registry import registered_topologies, registered_topology


def resolve_topology(
    artifact: dict[str, Any],
    application_config: dict[str, Any],
    *,
    source: TopologyFactSource,
) -> TopologyResolution:
    """以唯一事实提取和 Registry 评估入口解析应用拓扑。"""

    facts = extract_topology_facts(
        artifact,
        application_config,
        source=source,
    )
    definitions = registered_topologies()
    evaluations = tuple(
        TopologyEvaluation(
            type=definition.type,
            rejection_reasons=definition.rejection_reasons(facts),
        )
        for definition in definitions
    )
    matches = [
        definition
        for definition, evaluation in zip(definitions, evaluations, strict=True)
        if evaluation.matched
    ]
    if len(matches) > 1:
        names = "、".join(definition.type.value for definition in matches)
        raise ValueError(f"正式事实同时匹配多个应用拓扑：{names}。")
    if not matches:
        return TopologyResolution(
            facts=facts,
            evaluations=evaluations,
            selected_type=None,
        )

    definition = matches[0]
    blueprint = None
    if source is TopologyFactSource.TECHNICAL_PLAN:
        context = TopologyContext(
            technical_plan=artifact,
            application_config=application_config,
            facts=facts,
        )
        blueprint = _compile_blueprint(definition, context)
    return TopologyResolution(
        facts=facts,
        evaluations=evaluations,
        selected_type=definition.type,
        blueprint=blueprint,
    )


def resolve_product_topology(
    product_plan: dict[str, Any],
    application_config: dict[str, Any],
) -> TopologyResolution:
    """解析 TechnicalPlan 生成前的产品设计拓扑候选。"""

    return resolve_topology(
        product_plan,
        application_config,
        source=TopologyFactSource.PRODUCT_PLAN,
    )


def resolve_registered_topology(
    technical_plan: dict[str, Any],
    application_config: dict[str, Any],
) -> TopologyBlueprint | None:
    """按正式候选事实解析蓝图；无匹配时保留尚未迁移的当前流程。"""

    resolution = resolve_topology(
        technical_plan,
        application_config,
        source=TopologyFactSource.TECHNICAL_PLAN,
    )
    return resolution.blueprint


def compile_registered_topology(
    topology_type: TopologyType,
    technical_plan: dict[str, Any],
    application_config: dict[str, Any],
) -> TopologyBlueprint:
    """按已确认枚举编译拓扑，并重新验证当前事实仍满足其不变量。"""

    facts = extract_topology_facts(
        technical_plan,
        application_config,
        source=TopologyFactSource.TECHNICAL_PLAN,
    )
    definition = registered_topology(topology_type)
    rejection_reasons = definition.rejection_reasons(facts)
    if rejection_reasons:
        raise ValueError(
            f"当前正式事实不再满足拓扑 {topology_type.value}："
            + "；".join(rejection_reasons)
        )
    context = TopologyContext(
        technical_plan=technical_plan,
        application_config=application_config,
        facts=facts,
    )
    return _compile_blueprint(definition, context)


def compile_selected_technical_plan(
    topology_type: TopologyType,
    core_plan: dict[str, Any],
    product_plan: dict[str, Any],
    application_config: dict[str, Any],
) -> dict[str, Any]:
    """在用户选定的拓扑下，把 Core 候选编译成可确认的完整 TechnicalPlan。"""

    candidate = deepcopy(core_plan)
    if topology_type is TopologyType.AGENT_RUNTIME_DIRECT:
        from app.services.project_plan import compile_direct_agent_contracts

        candidate["agent_contracts"] = compile_direct_agent_contracts(
            candidate,
            product_plan,
            auth_enabled=application_config["auth"]["enable"] is True,
        )
    blueprint = compile_registered_topology(
        topology_type, candidate, application_config
    )
    return {
        **candidate,
        "topology": blueprint.technical_plan_projection(),
        "architecture": deepcopy(blueprint.design.architecture),
    }


def _compile_blueprint(
    definition: ApplicationTopologyDefinition,
    context: TopologyContext,
) -> TopologyBlueprint:
    """把唯一匹配的拓扑定义编译为完整三阶段蓝图。"""

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
