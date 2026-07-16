from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_result
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def _frontend_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    return (
        "You are the Frontend Generation Agent in an app-generation workflow.\n"
        "Execute only the approved frontend tasks below. Modify code only within "
        "each task's allowed_paths. Implement layout, components, interactions, "
        "permissions, API integration, loading/empty/error states, and page tests. "
        "ProjectPlan.api_contracts is the only source of API fields. Render and submit only "
        "fields declared by the task's endpoint_ids, schema refs, and response_bindings; "
        "do not infer or add frontend-only API fields.\n"
        "Do not modify RequirementSpec, PageDetail, ProjectPlan, API contracts, or "
        "the task DAG. If an API contract or page plan cannot be implemented, "
        "return a change_request instead of silently changing it.\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n"
        "Treat every allowed_paths entry as relative to virtual root '/'. For example, "
        "app/frontend/** means /app/frontend/** in filesystem tool calls.\n\n"
        f"Approved frontend tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
        f"BuildTaskPlan summary:\n{json.dumps(build_task_plan.get('summary', {}), ensure_ascii=False, indent=2)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_frontend_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
) -> str:
    """使用本次工作流的技能白名单调用前端 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    result = create_agent_bundle(workspace, selected_skill_names).frontend.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _frontend_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def generate_frontend_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """通过带技能白名单的 Frontend Deep Agent 执行已批准任务。"""

    if not tasks:
        return []

    settings = Settings.from_env()
    agent_note = _invoke_live_frontend_agent(
        project_plan=project_plan,
        build_task_plan=build_task_plan,
        tasks=tasks,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
    )
    return [
        create_agent_task_result(
            task,
            agent_note,
            executed_by={
                "agent": "frontend-generation-agent",
                "mode": "live",
                "model": settings.model_name,
                "source": "frontend_deep_agent",
                "requiredSkillsLoaded": list(selected_skill_names or []),
            },
        )
        for task in tasks
    ]
