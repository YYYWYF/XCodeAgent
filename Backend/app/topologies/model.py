"""定义生成应用拓扑在设计、计划和开发阶段的只读蓝图。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TopologyType(StrEnum):
    """预留已命名的生成应用拓扑；只有注册后才能参与编译。"""

    AGENT_RUNTIME_DIRECT = "agent_runtime_direct"


class TopologyFactSource(StrEnum):
    """标识拓扑事实来自产品设计还是正式技术计划。"""

    PRODUCT_PLAN = "product_plan"
    TECHNICAL_PLAN = "technical_plan"


@dataclass(frozen=True)
class TopologyFacts:
    """保存不同规划阶段归一化后的应用拓扑客观事实。"""

    source: TopologyFactSource
    auth_enabled: bool | None
    authorization_enabled: bool | None
    agent_ids: tuple[str, ...]
    backend_requirement_ids: tuple[str, ...]
    surface_agent_ids: tuple[str, ...] = ()
    business_action_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopologyContext:
    """向拓扑策略提供正式技术计划候选和应用配置快照。"""

    technical_plan: dict[str, Any]
    application_config: dict[str, Any]
    facts: TopologyFacts


@dataclass(frozen=True)
class DesignTopologyPlan:
    """保存设计阶段确定的服务边界、公开入口和认证终止点。"""

    public_edge_service_id: str
    service_ids: tuple[str, ...]
    authentication_termination: str
    architecture: dict[str, str]
    source_facts_sha256: str

    def as_dict(self) -> dict[str, Any]:
        """把设计蓝图转换为 TechnicalPlan 可持久化的稳定投影。"""

        return {
            "publicEdgeServiceId": self.public_edge_service_id,
            "serviceIds": list(self.service_ids),
            "authenticationTermination": self.authentication_termination,
            "sourceFactsSha256": self.source_facts_sha256,
        }


@dataclass(frozen=True)
class PlanningTopologyPlan:
    """保存模板、Build Unit 和检查所需的计划阶段差异。"""

    managed_roots: tuple[str, ...]
    unit_ids: tuple[str, ...]
    required_template_capabilities: tuple[str, ...]
    required_checks: tuple[str, ...]


@dataclass(frozen=True)
class DevelopmentTopologyPlan:
    """保存代码生成 owner、服务启动顺序和集成验收范围。"""

    generator_owners: tuple[str, ...]
    launch_stages: tuple[str, ...]
    acceptance_checks: tuple[str, ...]


@dataclass(frozen=True)
class TopologyBlueprint:
    """聚合一个拓扑在三个阶段的完整只读策略结果。"""

    type: TopologyType
    design: DesignTopologyPlan
    planning: PlanningTopologyPlan
    development: DevelopmentTopologyPlan

    def technical_plan_projection(self) -> dict[str, Any]:
        """保存拓扑身份、设计事实及 Build 可直接消费的 Unit 编译结果。"""

        return {
            "type": self.type.value,
            **self.design.as_dict(),
            "unitIds": list(self.planning.unit_ids),
        }


@dataclass(frozen=True)
class TopologyEvaluation:
    """记录一个已注册拓扑对当前事实的匹配结果。"""

    type: TopologyType
    rejection_reasons: tuple[str, ...]

    @property
    def matched(self) -> bool:
        """返回当前拓扑是否满足全部不变量。"""

        return not self.rejection_reasons


@dataclass(frozen=True)
class TopologyResolution:
    """返回统一 Resolver 的事实、逐拓扑判定和唯一选择结果。"""

    facts: TopologyFacts
    evaluations: tuple[TopologyEvaluation, ...]
    selected_type: TopologyType | None
    blueprint: TopologyBlueprint | None = None

    @property
    def matched(self) -> bool:
        """返回当前事实是否唯一匹配一个已注册拓扑。"""

        return self.selected_type is not None

    def rejection_reasons_for(self, topology_type: TopologyType) -> tuple[str, ...]:
        """返回指定拓扑的稳定拒绝原因；未注册枚举立即失败。"""

        for evaluation in self.evaluations:
            if evaluation.type is topology_type:
                return evaluation.rejection_reasons
        raise ValueError(f"拓扑 {topology_type.value} 未参与本次解析。")
