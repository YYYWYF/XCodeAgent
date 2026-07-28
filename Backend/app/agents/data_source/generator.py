from __future__ import annotations

import json
from typing import Any

from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results
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
        "Return one final JSON object with `task_results`, containing exactly one result per "
        "approved task. Each result must include `task_id`, `status` (`completed`, "
        "`already_satisfied`, or `failed`), and `summary`. `already_satisfied` requires "
        "`satisfaction_evidence.target_files` for every exact target and one passed evidence "
        "object for every exact acceptance criterion. A similar file at another path is not "
        "valid evidence. Failed work must include `failure_category` and `failure_reason`.\n\n"
        f"Approved data-source tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
        f"BuildTaskPlan summary:\n{json.dumps(build_task_plan.get('summary', {}), ensure_ascii=False, indent=2)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}\n\n"
        "## CRITICAL: Do NOT create temporary script files\n"
        "Do NOT create shell scripts (.sh), Python scripts (.py), JavaScript files (.js/.mjs), "
        "or any other temporary script files to run build commands. Instead, use the "
        "`terminal.exec` tool directly to execute commands like `pnpm run build`, `pnpm run dev`, "
        "`npm test`, etc. Creating temporary scripts pollutes the workspace with unnecessary files.\n"
    )


def _invoke_live_data_source_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """使用本次工作流的技能白名单调用数据源 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).data_source,
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
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )


def generate_data_sources_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """通过带技能白名单的 Data Source Deep Agent 执行已批准任务。"""

    if not tasks:
        return []

    settings = Settings.from_env()
    agent_note = _invoke_live_data_source_agent(
        project_plan=project_plan,
        build_task_plan=build_task_plan,
        tasks=tasks,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )
    return create_agent_task_results(
        tasks,
        agent_note,
        executed_by={
            "agent": "data-source-generation-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "data_source_deep_agent",
            "requiredSkillsLoaded": list(selected_skill_names or []),
        },
    )
