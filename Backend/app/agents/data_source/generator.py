from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_result
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def _data_source_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    return (
        "You are the Data Source Generation Agent in an app-generation workflow.\n"
        "Execute only the approved data-source tasks below. Modify code only within "
        "each task's allowed_paths. Generate data models, migrations, seed or mock "
        "data, APIs, validation, permissions, and backend tests. Obey the confirmed "
        "API contract exactly. ProjectPlan.api_contracts is the only source of model fields; "
        "data_sources.schema_refs select those schemas and must not be expanded with new fields. "
        "If the contract cannot be implemented, return a "
        "change_request instead of silently changing it.\n"
        "Do not modify RequirementSpec, PageDetail, ProjectPlan, API contracts, or "
        "the task DAG directly.\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n"
        "Treat every allowed_paths entry as relative to virtual root '/'. For example, "
        "app/backend/** means /app/backend/** in filesystem tool calls.\n\n"
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

    result = create_agent_bundle(workspace).data_source.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _data_source_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
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
                "model": settings.model_name,
                "source": "data_source_deep_agent",
            },
        )
        for task in tasks
    ]
