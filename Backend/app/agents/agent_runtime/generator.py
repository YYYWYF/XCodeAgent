"""Agent Runtime 七模块代码生成执行边界。"""

from __future__ import annotations

import json
from typing import Any

from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.topologies import serves_agent_runtime_public_edge


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信列表中筛选 Agent Contract 对象。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _contracts_for_tasks(
    project_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只向 Agent CodeRunner 投射当前任务 Unit 对应的正式契约。"""

    agent_ids = {
        str(task.get("unit_id") or "").removeprefix("agent:")
        for task in tasks
        if str(task.get("unit_id") or "").startswith("agent:")
        and str(task.get("unit_id") or "") != "agent:runtime"
    }
    contracts = _dict_items(project_plan.get("agent_contracts"))
    if not agent_ids:
        return contracts
    return [
        contract
        for contract in contracts
        if str(contract.get("agentId") or "") in agent_ids
    ]


def _api_contracts_for_agents(
    project_plan: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只投射当前 Agent Tool 引用的业务 API Contract 与 Schema。"""

    referenced_contract_ids: set[str] = set()
    for contract in contracts:
        settings = (
            contract.get("agentSettings")
            if isinstance(contract.get("agentSettings"), dict)
            else {}
        )
        tools = settings.get("tools") if isinstance(settings.get("tools"), dict) else {}
        for binding in _dict_items(tools.get("bindings")):
            endpoint = (
                binding.get("endpoint")
                if isinstance(binding.get("endpoint"), dict)
                else {}
            )
            contract_id = str(endpoint.get("apiContractId") or "").strip()
            if contract_id:
                referenced_contract_ids.add(contract_id)
            source = binding.get("source") if isinstance(binding.get("source"), dict) else {}
            if source.get("type") == "application_service":
                service_id = str(source.get("serviceId") or "").strip()
                if service_id:
                    referenced_contract_ids.add(service_id)
    return [
        contract
        for contract in _dict_items(project_plan.get("api_contracts"))
        if str(contract.get("id") or "").strip() in referenced_contract_ids
    ]


def _agent_runtime_generation_prompt(
    *,
    project_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    """构造只实现当前模板模块和正式 Agent Contract 的执行提示词。"""

    contracts = _contracts_for_tasks(project_plan, tasks)
    api_contracts = _api_contracts_for_agents(project_plan, contracts)
    direct = serves_agent_runtime_public_edge(project_plan)
    return (
        "Execute the approved Agent Runtime tasks in order. Each task is platform-compiled from "
        "the formal Agent Contract and contains the only allowed template read, modify, add, and "
        "test paths for its module. Inspect and reuse the existing Python 3.12 + DeepAgents Runtime "
        "template; make the smallest authorized change and never create a mechanical per-Agent "
        "wrapper. First read "
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}agent-runtime-generate/SKILL.md and only the references it "
        "routes for the current module. The template already injects the project model, trusted "
        "RuntimeContext, and checkpointer; never initialize a second model. Preserve declared Tool "
        "and formal business identities. In Direct topology, a local Tool imports the generated "
        "Application Service; never call a loopback REST URL or require a Java Gateway. In composed "
        "topology, keep missing gateway transport fail-closed. Never modify "
        "frontend, Java backend, dependency files, environment files, planning artifacts, or the "
        "Build DAG. Do not install dependencies, start services, or run project-level verification; "
        "the outer Workflow owns those checks.\n\n"
        "Return exactly one JSON object whose only top-level field is task_results, with exactly one "
        "result per task. Each result contains task_id, status, summary, and implementation_action. "
        "Use status completed for modify/add, already_satisfied for skip/reuse, or failed with "
        "failure_category and failure_reason. Do not return markdown or free text.\n\n"
        f"Formal Agent Contracts:\n{json.dumps(contracts, ensure_ascii=False, indent=2)}\n\n"
        f"Selected public edge: {'agent-runtime' if direct else 'gateway'}\n"
        "Referenced business API Contracts for declared Tools:\n"
        f"{json.dumps(api_contracts, ensure_ascii=False, indent=2)}\n\n"
        f"Approved tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n"
    )


def _invoke_live_agent_runtime(
    *,
    project_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """调用本次工作区隔离的 Agent Runtime Deep Agent。"""

    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).agent_runtime,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _agent_runtime_generation_prompt(
                        project_plan=project_plan,
                        tasks=tasks,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )


def generate_agent_runtime_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """通过模板感知的独立 Agent CodeRunner 执行七模块任务。"""

    del build_task_plan
    if not tasks:
        return []
    settings = Settings.from_env()
    agent_note = _invoke_live_agent_runtime(
        project_plan=project_plan,
        tasks=tasks,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )
    return create_agent_task_results(
        tasks,
        agent_note,
        executed_by={
            "agent": "agent-runtime-generation-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "agent_runtime_deep_agent",
            "requiredSkillsLoaded": list(selected_skill_names or []),
        },
        require_structured=True,
        strict_schema=True,
    )


def _python_business_generation_prompt(
    project_plan: dict[str, Any], tasks: list[dict[str, Any]]
) -> str:
    """向 Direct 业务代码执行器仅投射当前 Unit 的正式 Entity/API/Endpoint 事实。"""

    unit_ids = [str(task.get("unit_id") or "") for task in tasks]
    entity_ids = {
        unit_id.split(":", 2)[2]
        for unit_id in unit_ids
        if unit_id.startswith(("python:entity:", "python:migration:", "python:repository:"))
    }
    contract_ids = {
        unit_id.split(":", 3)[2]
        for unit_id in unit_ids
        if unit_id.startswith(("python:service:", "python:endpoint:"))
    }
    contracts = [
        item for item in _dict_items(project_plan.get("api_contracts"))
        if str(item.get("id") or "") in contract_ids
    ]
    entity_ids.update(
        str(entity_id)
        for contract in contracts
        for entity_id in contract.get("entity_ids") or []
        if str(entity_id).strip()
    )
    entities = [
        item for item in _dict_items(project_plan.get("entities"))
        if str(item.get("id") or "") in entity_ids
    ]
    return (
        "Execute only approved owner=python-business tasks. The selected topology is "
        "agent_runtime_direct: Python Application Runtime owns business Entities, Repositories, "
        "Application Services and FastAPI Endpoints. Entity SQL migrations have already been confirmed "
        "by the user and written to the project; never regenerate or overwrite SQL. Reuse the template's TrustedBusinessContext, "
        "business_router, database adapter and auth/anonymous principal; do not create user tables, "
        "a Java Backend, a Gateway or a second Build workflow. REST and Agent Tools must call "
        "the same Application Service. Each task's target_files, allowed_paths and change_scope "
        "are hard boundaries; do not edit router.py or template infrastructure unless a task explicitly "
        "owns it. Derive typed request/response DTOs only from the confirmed Direct API Contract schemas; "
        "Direct topology has no Endpoint field-mapping or EntitySourceBinding artifact. If an "
        "important behavior is underspecified, return failed with contract_mismatch rather than "
        "inventing business rules. Do not install dependencies, run builds, or start services.\n\n"
        "Return exactly one JSON object with only task_results, one result per task. Each result "
        "has task_id, status, summary, implementation_action; status is completed, "
        "already_satisfied, or failed with failure_category and failure_reason.\n\n"
        f"Formal Entities:\n{json.dumps(entities, ensure_ascii=False, indent=2)}\n\n"
        f"Formal API Contracts:\n{json.dumps(contracts, ensure_ascii=False, indent=2)}\n\n"
        f"Approved Tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n"
    )


def generate_python_business_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """复用 Runtime 工程权限及统一 Build 结果协议执行 Direct Python 业务任务。"""

    del build_task_plan
    if not tasks:
        return []
    from app.agents import create_agent_bundle

    settings = Settings.from_env()
    agent_note = invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).agent_runtime,
        {"messages": [{"role": "user", "content": _python_business_generation_prompt(project_plan, tasks)}]},
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )
    return create_agent_task_results(
        tasks,
        agent_note,
        executed_by={
            "agent": "python-business-generation-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "agent_runtime_direct_business",
            "requiredSkillsLoaded": list(selected_skill_names or []),
        },
        require_structured=True,
        strict_schema=True,
    )


__all__ = ["generate_agent_runtime_with_deep_agent", "generate_python_business_with_deep_agent"]
