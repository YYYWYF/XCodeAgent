"""实现 Frontend + Python Application Runtime 直连拓扑的三阶段蓝图。"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Callable

from app.topologies.model import (
    DevelopmentTopologyPlan,
    DesignTopologyPlan,
    PlanningTopologyPlan,
    TopologyContext,
    TopologyFacts,
    TopologyType,
)


class AgentRuntimeDirectTopology:
    """编译由 Python Runtime 承载业务 API、Agent 和数据的应用。"""

    type = TopologyType.AGENT_RUNTIME_DIRECT

    def development_runner(self, owner: str) -> tuple[str, Callable[..., Any]] | None:
        """把 Direct 独有的 Python business owner 绑定到 Runtime 业务生成器。"""

        if owner != "python-business":
            return None
        from app.agents.agent_runtime.generator import generate_python_business_with_deep_agent

        return "python-business.deep_agent", generate_python_business_with_deep_agent

    def rejection_reasons(self, facts: TopologyFacts) -> tuple[str, ...]:
        """以唯一归一化事实集验证 Direct 拓扑全部不变量。"""

        reasons: list[str] = []
        if facts.auth_enabled is None:
            reasons.append("auth.enable 必须是布尔值")
        if facts.authorization_enabled is not False:
            reasons.append("authorization.enabled 必须为 false")
        if not facts.agent_ids:
            reasons.append("必须至少存在一个可用 Agent")
        java_only_requirements = tuple(
            item for item in facts.backend_requirement_ids
            if item.startswith("backend_tool:")
        )
        if java_only_requirements:
            reasons.append(
                "存在只能由 Java Backend 承载的 Tool："
                + "、".join(java_only_requirements)
            )
        return tuple(reasons)

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
                "frontend": "React 客户端通过 REST 和 AG-UI 访问 Python Application Runtime。",
                "agent_runtime": (
                    "Python 3.12 + FastAPI + DeepAgents，承载业务 API、Agent 执行、"
                    "统一认证适配和应用服务。"
                ),
                "data": "业务 Entity/Repository 与 Agent checkpoint 独立持久化，不自建用户体系。",
            },
            source_facts_sha256=_source_facts_sha256(context),
        )

    def compile_planning(self, context: TopologyContext) -> PlanningTopologyPlan:
        """声明 Direct 模板 roots、Unit、能力证据和固定检查。"""

        plan = context.technical_plan
        auth_enabled = context.application_config["auth"]["enable"] is True
        agent_ids = _ids(plan.get("agent_contracts"), "agentId")
        from app.services.frontend_page_tree import project_plan_page_records

        page_ids = _ids(project_plan_page_records(plan), "pageId")
        entity_ids = _ids(plan.get("entities"), "id")
        api_contracts = _dict_items(plan.get("api_contracts"))
        capabilities = [
            "python_application_runtime",
            "python_business_api",
            "python_domain_model",
            "python_repository",
            "python_business_migrations",
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
            "python:bootstrap",
            "frontend:shell",
            "frontend:api-client",
            *( ["frontend:auth-guard"] if auth_enabled else [] ),
            *(f"python:entity:{entity_id}" for entity_id in entity_ids),
            *(f"python:migration:{entity_id}" for entity_id in entity_ids),
            *(f"python:repository:{entity_id}" for entity_id in entity_ids),
            *(
                f"python:service:{contract.get('id')}"
                for contract in api_contracts
                if str(contract.get("id") or "").strip()
            ),
            *(
                f"python:endpoint:{contract.get('id')}:{endpoint.get('id')}"
                for contract in api_contracts
                if str(contract.get("id") or "").strip()
                for endpoint in _dict_items(contract.get("endpoints"))
                if str(endpoint.get("id") or "").strip()
            ),
            *(f"page:{page_id}" for page_id in page_ids),
            "agent:runtime",
            *(f"agent:{agent_id}" for agent_id in agent_ids),
            "app:integration",
        ]
        return PlanningTopologyPlan(
            managed_roots=("frontend", "agent-runtime"),
            unit_ids=tuple(unit_ids),
            required_template_capabilities=tuple(capabilities),
            required_checks=(
                "python-business-contract-tests",
                "agent-runtime-contract-tests",
                "agent-runtime-pytest",
                "frontend-build",
                "ag-ui-integration",
                "principal-isolation",
            ),
        )

    def compile_development(self, context: TopologyContext) -> DevelopmentTopologyPlan:
        """声明 Python 业务/Agent/Frontend Generator 和 Direct 启动验收图。"""

        del context
        return DevelopmentTopologyPlan(
            generator_owners=("frontend", "python-business", "agent"),
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
