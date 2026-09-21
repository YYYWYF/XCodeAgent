"""实现纯 Frontend + Agent Runtime 直连拓扑的三阶段蓝图。"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.topologies.model import (
    DevelopmentTopologyPlan,
    DesignTopologyPlan,
    PlanningTopologyPlan,
    TopologyContext,
    TopologyType,
)


class AgentRuntimeDirectTopology:
    """编译不包含 Java Backend、Gateway、业务数据库或 RBAC 的 Agent 应用。"""

    type = TopologyType.AGENT_RUNTIME_DIRECT

    def matches(self, context: TopologyContext) -> bool:
        """仅在纯 Agent 正式事实和 canonical capability 同时满足时匹配。"""

        plan = context.technical_plan
        config = context.application_config
        authorization = config.get("authorization")
        auth = config.get("auth")
        if (
            not isinstance(authorization, dict)
            or authorization.get("enabled") is not False
            or not isinstance(auth, dict)
            or type(auth.get("enable")) is not bool
        ):
            return False
        contracts = _dict_items(plan.get("agent_contracts"))
        if not contracts or _dict_items(plan.get("entities")) or _dict_items(plan.get("api_contracts")):
            return False
        if any(_has_backend_tool(contract) for contract in contracts):
            return False
        return not _page_endpoint_dependencies(plan)

    def compile_design(self, context: TopologyContext) -> DesignTopologyPlan:
        """声明 Runtime Public Edge、认证终止点和三段架构摘要。"""

        auth_enabled = context.application_config["auth"]["enable"] is True
        return DesignTopologyPlan(
            public_edge_service_id="agent-runtime",
            service_ids=("agent-runtime",),
            authentication_termination=(
                "agent-runtime" if auth_enabled else "anonymous-session"
            ),
            architecture={
                "frontend": "React 客户端通过 AG-UI 访问 Agent Runtime Public Edge。",
                "agent_runtime": (
                    "Python 3.12 + DeepAgents，承载公开 AG-UI、Agent 执行、"
                    "统一认证适配和会话状态。"
                ),
                "data": "只保存 Agent 运行状态，不承载通用业务实体或自建用户体系。",
            },
            source_facts_sha256=_source_facts_sha256(context),
        )

    def compile_planning(self, context: TopologyContext) -> PlanningTopologyPlan:
        """声明 Direct 模板 roots、Unit、能力证据和固定检查。"""

        plan = context.technical_plan
        auth_enabled = context.application_config["auth"]["enable"] is True
        agent_ids = _ids(plan.get("agent_contracts"), "agentId")
        page_ids = _ids(plan.get("pages"), "pageId")
        capabilities = [
            "agent_runtime_public_edge",
            "agent_runtime_principal_ownership",
            "agent_runtime_local_debug",
            (
                "agent_runtime_public_auth"
                if auth_enabled
                else "agent_runtime_anonymous_session"
            ),
        ]
        unit_ids = [
            "application:root",
            "frontend:shell",
            "frontend:api-client",
            *( ["frontend:auth-guard"] if auth_enabled else [] ),
            *(f"frontend:agent-surface:{page_id}" for page_id in page_ids),
            "agent:runtime",
            *(f"agent:{agent_id}" for agent_id in agent_ids),
            "app:integration",
        ]
        return PlanningTopologyPlan(
            managed_roots=("frontend", "agent-runtime"),
            unit_ids=tuple(unit_ids),
            required_template_capabilities=tuple(capabilities),
            required_checks=(
                "agent-runtime-contract-tests",
                "agent-runtime-pytest",
                "frontend-build",
                "ag-ui-integration",
                "principal-isolation",
            ),
        )

    def compile_development(self, context: TopologyContext) -> DevelopmentTopologyPlan:
        """声明现有 Frontend/Agent Generator 和 Direct 启动验收图。"""

        del context
        return DevelopmentTopologyPlan(
            generator_owners=("frontend", "agent"),
            launch_stages=(
                "structure",
                "agent_runtime",
                "frontend",
                "integration_probe",
                "ready",
            ),
            acceptance_checks=(
                "topology-template-build-hash",
                "managed-roots",
                "agent-runtime-pytest",
                "frontend-build",
                "ag-ui-integration",
                "auth-or-anonymous",
                "cross-principal-isolation",
                "debug-profile-excluded",
            ),
        )


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从数组中提取对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _ids(value: Any, key: str) -> tuple[str, ...]:
    """按输入顺序返回非空去重标识。"""

    return tuple(
        dict.fromkeys(
            str(item.get(key) or "").strip()
            for item in _dict_items(value)
            if str(item.get(key) or "").strip()
        )
    )


def _has_backend_tool(contract: dict[str, Any]) -> bool:
    """识别任何 Java Endpoint Tool 或旧 Endpoint 展开形状。"""

    settings = contract.get("agentSettings")
    settings = settings if isinstance(settings, dict) else {}
    tools = settings.get("tools")
    tools = tools if isinstance(tools, dict) else {}
    for binding in _dict_items(tools.get("bindings")):
        source = binding.get("source")
        source = source if isinstance(source, dict) else {}
        if source.get("type") == "backend_endpoint" or isinstance(binding.get("endpoint"), dict):
            return True
    return False


def _page_endpoint_dependencies(plan: dict[str, Any]) -> bool:
    """拒绝任何页面对业务 Endpoint 的依赖。"""

    for page in _dict_items(plan.get("pages")):
        references = page.get("references")
        references = references if isinstance(references, dict) else {}
        if _dict_items(references.get("endpoint_dependencies")):
            return True
    return False


def _source_facts_sha256(context: TopologyContext) -> str:
    """绑定拓扑匹配实际消费的正式事实和配置修订。"""

    payload = {
        "agents": context.technical_plan.get("agent_contracts"),
        "pages": context.technical_plan.get("pages"),
        "entities": context.technical_plan.get("entities"),
        "apiContracts": context.technical_plan.get("api_contracts"),
        "configRevision": context.application_config.get("configRevision"),
        "auth": context.application_config.get("auth"),
        "authorization": context.application_config.get("authorization"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(encoded.encode("utf-8")).hexdigest()


AGENT_RUNTIME_DIRECT_TOPOLOGY = AgentRuntimeDirectTopology()
