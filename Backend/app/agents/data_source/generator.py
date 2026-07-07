from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.graph.nodes.common import last_agent_text
from app.services.build_result_coordinator import create_agent_task_result


def _data_source_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
) -> str:
    return (
        "You are the Data Source Generation Agent in an app-generation workflow.\n"
        "Execute only the approved data-source tasks below. Modify code only within "
        "each task's allowed_paths. Generate data models, migrations, seed or mock "
        "data, APIs, validation, permissions, and backend tests. Obey the confirmed "
        "API contract exactly. If the contract cannot be implemented, return a "
        "change_request instead of silently changing it.\n"
        "Do not modify RequirementSpec, PageSpec, ProjectPlan, API contracts, or "
        "the task DAG directly.\n\n"
        f"Workspace:\n{workspace or 'default workspace'}\n\n"
        f"Approved data-source tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
        f"BuildTaskPlan summary:\n{json.dumps(build_task_plan.get('summary', {}), ensure_ascii=False, indent=2)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_data_source_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
) -> str:
    # Lazy import keeps Deep Agent construction at this live execution boundary.
    from app.agents import create_agent_bundle

    result = create_agent_bundle().data_source.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _data_source_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
                        workspace=workspace,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def generate_data_sources_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Execute approved data-source tasks through the Data Source Deep Agent."""

    if not tasks:
        return []

    settings = Settings.from_env()
    agent_note = _invoke_live_data_source_agent(
        project_plan=project_plan,
        build_task_plan=build_task_plan,
        tasks=tasks,
        workspace=workspace,
    )
    return [
        create_agent_task_result(
            task,
            agent_note,
            executed_by={
                "agent": "data-source-generation-agent",
                "mode": "live",
                "model": settings.anthropic_model,
                "source": "data_source_deep_agent",
            },
        )
        for task in tasks
    ]
