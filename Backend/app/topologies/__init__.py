"""生成应用拓扑的统一类型、注册与编译入口。"""

from app.topologies.compiler import (
    compile_registered_topology,
    resolve_registered_topology,
    topology_type_from_plan,
)
from app.topologies.model import (
    DevelopmentTopologyPlan,
    DesignTopologyPlan,
    PlanningTopologyPlan,
    TopologyBlueprint,
    TopologyContext,
    TopologyType,
)
from app.topologies.queries import (
    AGENT_RUNTIME_SERVICE_ID,
    BACKEND_SERVICE_ID,
    confirmed_authentication_termination,
    confirmed_public_edge_service_id,
    confirmed_service_ids,
    includes_backend_service,
    read_confirmed_technical_plan,
    serves_agent_runtime_public_edge,
)

__all__ = [
    "AGENT_RUNTIME_SERVICE_ID",
    "BACKEND_SERVICE_ID",
    "DevelopmentTopologyPlan",
    "DesignTopologyPlan",
    "PlanningTopologyPlan",
    "TopologyBlueprint",
    "TopologyContext",
    "TopologyType",
    "compile_registered_topology",
    "confirmed_authentication_termination",
    "confirmed_public_edge_service_id",
    "confirmed_service_ids",
    "includes_backend_service",
    "read_confirmed_technical_plan",
    "resolve_registered_topology",
    "serves_agent_runtime_public_edge",
    "topology_type_from_plan",
]
