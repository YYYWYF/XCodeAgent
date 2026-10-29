"""声明拓扑定义必须实现的三阶段策略合同。"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from app.topologies.model import (
    DevelopmentTopologyPlan,
    DesignTopologyPlan,
    PlanningTopologyPlan,
    TopologyContext,
    TopologyType,
)


class ApplicationTopologyDefinition(Protocol):
    """约束拓扑显式选择、能力检查和三阶段编译，不提供自动匹配。"""

    type: TopologyType

    def is_available(self, context: TopologyContext) -> tuple[str, ...]:
        """返回尚未交付的模板、生成器或启动能力，非空时禁止选择。"""

    def validate_selection(self, context: TopologyContext) -> None:
        """校验用户选择与当前应用事实，冲突时抛错而不改选。"""

    def compile_design(self, context: TopologyContext) -> DesignTopologyPlan:
        """编译设计阶段蓝图。"""

    def compile_planning(self, context: TopologyContext) -> PlanningTopologyPlan:
        """编译计划阶段蓝图。"""

    def compile_development(self, context: TopologyContext) -> DevelopmentTopologyPlan:
        """编译开发阶段蓝图。"""

    def development_runner(self, owner: str) -> tuple[str, Callable[..., Any]] | None:
        """返回当前拓扑独有代码 owner 的 Build 执行器。"""

