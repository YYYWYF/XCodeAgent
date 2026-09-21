"""维护已审核拓扑枚举到唯一实现的显式注册表。"""

from __future__ import annotations

from app.topologies.model import TopologyType
from app.topologies.protocols import ApplicationTopologyDefinition
from app.topologies.definitions import AGENT_RUNTIME_DIRECT_TOPOLOGY


# 拓扑必须完成独立设计审核后才能进入此表。
_TOPOLOGY_REGISTRY: dict[TopologyType, ApplicationTopologyDefinition] = {
    TopologyType.AGENT_RUNTIME_DIRECT: AGENT_RUNTIME_DIRECT_TOPOLOGY,
}


def registered_topology(
    topology_type: TopologyType,
) -> ApplicationTopologyDefinition:
    """返回已注册实现；枚举缺少实现时立即失败而不是运行中降级。"""

    try:
        return _TOPOLOGY_REGISTRY[topology_type]
    except KeyError as exc:
        raise ValueError(f"拓扑 {topology_type.value} 尚未注册实现。") from exc


def registered_topologies() -> tuple[ApplicationTopologyDefinition, ...]:
    """按枚举声明顺序返回全部已注册拓扑。"""

    return tuple(
        _TOPOLOGY_REGISTRY[item]
        for item in TopologyType
        if item in _TOPOLOGY_REGISTRY
    )
