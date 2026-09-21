"""定义生成应用拓扑在设计、计划和开发阶段的只读蓝图。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TopologyType(StrEnum):
    """预留已命名的生成应用拓扑；只有注册后才能参与编译。"""

    AGENT_RUNTIME_DIRECT = "agent_runtime_direct"


@dataclass(frozen=True)
class TopologyContext:
    """向拓扑策略提供正式技术计划候选和应用配置快照。"""

    technical_plan: dict[str, Any]
    application_config: dict[str, Any]


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
        """生成正式 TechnicalPlan 保存的最小拓扑身份与设计事实。"""

        return {
            "type": self.type.value,
            **self.design.as_dict(),
        }
