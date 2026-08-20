from __future__ import annotations

import json
from typing import Any

from app.agents.data_source.prompt_context import (
    data_source_execution_context as _data_source_execution_context,
    execution_task_packet as _execution_task_packet,
    task_data_source_types as _task_data_source_types,
    task_required_skill_paths as _task_required_skill_paths,
)
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results


def _data_source_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    """生成按任务路由 Skill 且只暴露定向正式上下文的执行提示词。"""

    task_packets = [_execution_task_packet(task) for task in tasks]
    execution_context = _data_source_execution_context(project_plan, tasks)
    return (
        "Execute the approved backend tasks below one by one in task_id order. Modify only "
        "each task's allowed_paths and implement only its declared change_scope.\n"
        "For each task, read every file in required_skill_paths before implementing that task. "
        "Read a repeated Skill path only once in this invocation, and apply it only to tasks "
        "that declare that path. Never apply database persistence rules to external_api entities.\n"
        "The targeted execution context is authoritative: API request/response fields come from "
        "the matching API Contract and EndpointDetail; persistence or upstream mappings come from "
        "the matching confirmed EntityDesign. Never infer fields or source types from omitted global "
        "ProjectPlan data. If the contract cannot be implemented, return a change_request instead "
        "of silently changing it. Do not modify formal planning artifacts or the task DAG.\n\n"
        "Do not install dependencies or run Maven, Gradle, lint, typecheck, unit-test, or dev-server "
        "commands. Do not create temporary verification scripts. The outer integration-test phase "
        "owns repository verification.\n\n"
        "Return one syntactically valid JSON object with task_results and exactly one result per "
        "approved task. Every result must include task_id, status (completed, already_satisfied, "
        "or failed), and summary. Use already_satisfied only when the exact target state already "
        "exists without writing. Failed work must include failure_category and failure_reason. "
        "Do not wrap the JSON in a Markdown fence.\n\n"
        f"Approved backend task packets:\n{json.dumps(task_packets, ensure_ascii=False, indent=2)}\n\n"
        f"Targeted execution context:\n{json.dumps(execution_context, ensure_ascii=False, indent=2)}\n"
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
    required_builtin_skills = sorted(
        {
            path
            for task in tasks
            for path in _task_required_skill_paths(task)
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
