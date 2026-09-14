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
    """构造只实现当前模板模块和正式 Agent Contract 的执行提示词。"""

    contracts = _contracts_for_tasks(project_plan, tasks)
    api_contracts = _api_contracts_for_agents(project_plan, contracts)
    return (
        "Execute the approved Agent Runtime tasks in order. Each task is platform-compiled from "
        "the formal Agent Contract and contains the only allowed template read, modify, add, and "
        "test paths for its module. Inspect and reuse the existing Python 3.12 + DeepAgents Runtime "
        "template; make the smallest authorized change and never create a mechanical per-Agent "
        "wrapper. First read "
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}agent-runtime-generate/SKILL.md and only the references it "
        "routes for the current module. The template already injects the project model, trusted "
        "RuntimeContext, and checkpointer; never initialize a second model. Preserve declared Tool "
        "and Java Endpoint identities and keep missing gateway transport fail-closed. Never modify "
        "frontend, Java backend, dependency files, environment files, planning artifacts, or the "
        "Build DAG. Do not install dependencies, start services, or run project-level verification; "
        "the outer Workflow owns those checks.\n\n"
        "Return exactly one JSON object whose only top-level field is task_results, with exactly one "
        "result per task. Each result contains task_id, status, summary, and implementation_action. "
        "Use status completed for modify/add, already_satisfied for skip/reuse, or failed with "
        "failure_category and failure_reason. Do not return markdown or free text.\n\n"
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


__all__ = ["generate_agent_runtime_with_deep_agent"]
