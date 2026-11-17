from __future__ import annotations

from typing import Any

from app.agents.data_source.execution_config import (
    batch_required_builtin_skill_names,
    task_required_instruction_paths,
)
from app.agents.data_source.prompts import data_source_generation_prompt
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results


def _invoke_live_data_source_agent(
    *,
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """使用本次工作流的技能白名单调用数据源 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle
    agent = create_agent_bundle(
            workspace,
            selected_skill_names,
            data_source_builtin_skill_names=batch_required_builtin_skill_names(tasks),
        ).data_source
    return invoke_agent_with_tool_activity(
        agent,
        {
            "messages": [
                {
                    "role": "user",
                    "content": data_source_generation_prompt(
                        project_plan=project_plan,
                        workspace_snapshot=workspace_snapshot,
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
    workspace_snapshot: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """通过带技能白名单的 Data Source Deep Agent 执行已批准任务。"""

    # 保留 Build owner 的统一调用签名，但不把完整任务计划注入 Java Agent Prompt。
    del build_task_plan
    if not tasks:
        return []

    settings = Settings.from_env()
    agent_note = _invoke_live_data_source_agent(
        project_plan=project_plan,
        workspace_snapshot=workspace_snapshot,
        tasks=tasks,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )
    required_builtin_skills = sorted(
        {
            path
            for task in tasks
            for path in task_required_instruction_paths(task)
        }
    )
    return create_agent_task_results(
        tasks,
        agent_note,
        executed_by={
            "agent": "data-source-generation-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "data_source_deep_agent",
            "requiredSkillsLoaded": [
                *required_builtin_skills,
                *(selected_skill_names or []),
            ],
        },
        require_structured=True,
    )
