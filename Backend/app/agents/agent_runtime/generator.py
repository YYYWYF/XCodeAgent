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
    """只投射当前 Agent Tool 实际引用的 Java API Contract 与 Schema。"""

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
    """构造只实现 Python sidecar 与 Agent Contract 的执行提示词。"""

    contracts = _contracts_for_tasks(project_plan, tasks)
    api_contracts = _api_contracts_for_agents(project_plan, contracts)
    return (
        "Execute the approved Agent Runtime tasks in order. The formal Agent Contracts below "
        "are the sole source for the ProductPlan-derived identity, capabilities, interaction, "
        "seven-part agentSettings, Python 3.12 + DeepAgents runtime, security, AG-UI SSE invocation, "
        "evaluation, and artifact paths. The platform-owned Agent Runtime template is already present and must not "
        "be modified. Each business-Agent task must implement exactly its declared Agent module, "
        "API tool adapter, and test. First read "
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}agent-runtime-generate/SKILL.md. Compile agentSettings.prompt "
        "with the locked platform rules. The template already resolves project_default through "
        "init_chat_model and injects model, runtime_context, and checkpointer into the generated "
        "create_agent(*, model, runtime_context, checkpointer) entry function; never initialize a "
        "second model. Pass generated tools and the declared short-term checkpointer behavior to "
        "create_deep_agent. Tool adapters call only their declared Java API endpoints with scoped "
        "user context and environment-resolved service credentials; the browser must never call the Python "
        "sidecar directly. All writes must stay inside each task's allowed_paths under "
        "agent-runtime/; you must not modify frontend or Java backend, planning documents, API "
        "contracts, or task metadata. Do not install dependencies, start services, or run project "
        "verification commands; outer integration verification owns those checks.\n\n"
        "Return exactly one JSON object whose only top-level field is task_results, with exactly "
        "one result per task. Each result contains task_id, status (completed, already_satisfied, "
        "or failed), and a non-empty summary. Use failed with failure_category and failure_reason "
        "when the contract cannot be implemented in scope. Do not return markdown or free text.\n\n"
        f"Formal Agent Contracts:\n{json.dumps(contracts, ensure_ascii=False, indent=2)}\n\n"
        "Resolved Java API Contracts for declared Tools:\n"
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
    """通过独立 Agent CodeRunner 执行 Python sidecar 构建任务。"""

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
