"""向生成应用主流程暴露已确认拓扑的设计事实，禁止下游重复推断工程结构。

框架合同要求 `TechnicalPlan` 只持久化稳定拓扑身份和设计事实，下游不得再按目录
是否存在或按字符串比较拓扑名称来推断结构。因此下游统一经本模块读取事实：

- 需要知道应用包含哪些服务边界时，读 `confirmed_service_ids` 或
  `includes_backend_service`；
- 需要知道公开入口由谁承担时，读 `confirmed_public_edge_service_id` 或
  `serves_agent_runtime_public_edge`；
- 需要知道认证在何处终止时，读 `confirmed_authentication_termination`。

尚未迁移到拓扑框架的历史计划没有 `TechnicalPlan.topology` 投影，此时各查询按
当前主流程（Frontend + Backend）给出保守答案，保证旧流程行为不变。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Agent Runtime 作为独立服务的稳定标识；Direct 拓扑由它承担应用公开入口。
AGENT_RUNTIME_SERVICE_ID = "agent-runtime"
# Java Backend 服务边界标识；存在该边界即表示应用包含后端工程。
BACKEND_SERVICE_ID = "backend"

_TECHNICAL_PLAN_RELATIVE_PATH = Path(".xcodeagent/plans/technical-plan.json")
_TECHNICAL_PLAN_ARTIFACT_TYPE = "technical-plan"


def read_confirmed_technical_plan(workspace_root: str | Path) -> dict[str, Any] | None:
    """读取已确认的 TechnicalPlan；未确认、缺失或损坏时返回 None。

    返回 None 表示调用方不得回退到任何目录结构猜测，只能按未迁移流程处理。
    """

    path = Path(workspace_root).expanduser().resolve() / _TECHNICAL_PLAN_RELATIVE_PATH
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(plan, dict)
        or plan.get("artifact_type") != _TECHNICAL_PLAN_ARTIFACT_TYPE
        or plan.get("confirmation_status") != "confirmed"
    ):
        return None
    return plan


def confirmed_service_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    """读取拓扑投影声明的服务边界；未迁移计划返回空元组。"""

    topology = plan.get("topology")
    raw = topology.get("serviceIds") if isinstance(topology, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item or "").strip())


def confirmed_public_edge_service_id(plan: dict[str, Any]) -> str | None:
    """读取拓扑投影声明的公开入口服务；未迁移计划返回 None。"""

    topology = plan.get("topology")
    raw = topology.get("publicEdgeServiceId") if isinstance(topology, dict) else None
    text = str(raw or "").strip()
    return text or None


def confirmed_authentication_termination(plan: dict[str, Any]) -> str | None:
    """读取拓扑投影声明的认证终止点；未迁移计划返回 None。"""

    topology = plan.get("topology")
    raw = topology.get("authenticationTermination") if isinstance(topology, dict) else None
    text = str(raw or "").strip()
    return text or None


def includes_backend_service(plan: dict[str, Any]) -> bool:
    """判断应用是否包含 Java Backend 服务边界。

    尚未声明服务边界的计划按当前主流程的 Frontend + Backend 处理，因此视为
    包含 Backend，避免收敛后把旧流程误判成不含后端。
    """

    service_ids = confirmed_service_ids(plan)
    if not service_ids:
        return True
    return BACKEND_SERVICE_ID in service_ids


def serves_agent_runtime_public_edge(plan: dict[str, Any]) -> bool:
    """判断应用公开入口是否由 Agent Runtime 自身承担。"""

    return confirmed_public_edge_service_id(plan) == AGENT_RUNTIME_SERVICE_ID


def confirmed_launch_stages(
    plan: dict[str, Any], application_config: dict[str, Any]
) -> tuple[str, ...]:
    """从已确认拓扑重新编译启动阶段，不按目录结构猜测服务顺序。"""

    from app.topologies.compiler import compile_registered_topology, topology_type_from_plan

    topology_type = topology_type_from_plan(plan)
    if topology_type is None:
        return ()
    blueprint = compile_registered_topology(topology_type, plan, application_config)
    return blueprint.development.launch_stages
