"""声明拓扑定义必须实现的三阶段策略合同。"""

from __future__ import annotations

from typing import Protocol

from app.topologies.model import (
    DevelopmentTopologyPlan,
    DesignTopologyPlan,
    PlanningTopologyPlan,
    TopologyContext,
    TopologyType,
)


class ApplicationTopologyDefinition(Protocol):
    """约束每个拓扑以相同方式提供匹配和三阶段编译能力。"""

    type: TopologyType

    def matches(self, context: TopologyContext) -> bool:
        """判断正式候选是否唯一满足当前拓扑。"""

    def compile_design(self, context: TopologyContext) -> DesignTopologyPlan:
        """编译设计阶段蓝图。"""

    def compile_planning(self, context: TopologyContext) -> PlanningTopologyPlan:
        """编译计划阶段蓝图。"""

    def compile_development(self, context: TopologyContext) -> DevelopmentTopologyPlan:
        """编译开发阶段蓝图。"""
