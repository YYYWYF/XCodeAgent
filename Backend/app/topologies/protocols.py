"""声明拓扑定义必须实现的三阶段策略合同。"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from app.topologies.model import (
    DevelopmentTopologyPlan,
    DesignTopologyPlan,
    PlanningTopologyPlan,
    TopologyContext,
    TopologyFacts,
    TopologyType,
)


class ApplicationTopologyDefinition(Protocol):
    """约束每个拓扑以相同方式提供匹配和三阶段编译能力。"""

    type: TopologyType

    def rejection_reasons(self, facts: TopologyFacts) -> tuple[str, ...]:
        """返回归一化事实不满足当前拓扑的稳定原因。"""

    def compile_design(self, context: TopologyContext) -> DesignTopologyPlan:
        """编译设计阶段蓝图。"""

    def compile_planning(self, context: TopologyContext) -> PlanningTopologyPlan:
        """编译计划阶段蓝图。"""

    def compile_development(self, context: TopologyContext) -> DevelopmentTopologyPlan:
        """编译开发阶段蓝图。"""

    def development_runner(self, owner: str) -> tuple[str, Callable[..., Any]] | None:
        """返回当前拓扑独有代码 owner 的 Build 执行器。"""
