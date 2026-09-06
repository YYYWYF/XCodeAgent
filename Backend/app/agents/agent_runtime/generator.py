"""Agent Runtime 七模块代码生成执行边界。"""

from __future__ import annotations

from typing import Any

from app.agents.tool_activity_stream import ToolActivityCallback


def generate_agent_runtime_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """在 CodeRunner 批次实施前显式阻断七模块执行，禁止再次误报完成。"""

    del (
        project_plan,
        build_task_plan,
        workspace,
        selected_skill_names,
        on_tool_activity,
    )
    return [
        {
            "task_id": str(task.get("id") or ""),
            "owner": "agent",
            "status": "failed",
            "failure_category": "plan_mismatch",
            "failure_reason": (
                "Agent 七模块 DAG 已编译，但模板感知 CodeRunner 尚未在当前实施批次启用。"
            ),
            "agent_note": (
                "已阻止未实现代码的流程把该模块误报为代码生成完成。"
            ),
            "changed_files": [],
            "commands": [],
            "change_request": {
                "reason": "需要完成模板感知 Agent CodeRunner 实施批次。"
            },
        }
        for task in tasks
    ]


__all__ = ["generate_agent_runtime_with_deep_agent"]
