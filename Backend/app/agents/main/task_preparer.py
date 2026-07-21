from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.build_task_planner import create_build_task_plan
from app.utils.model_output import extract_json_object


def _app_name_from_plan(project_plan: dict[str, Any]) -> str:
    """Extract the application name from ProjectPlan.app.name (defensive)."""
    app = project_plan.get("app") or {}
    if isinstance(app, dict):
        name = app.get("name") or app.get("appName")
        if name:
            return str(name)
    return ""


def _task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
) -> str:
    snapshot_text = (
        json.dumps(workspace_snapshot, ensure_ascii=False, indent=2)
        if workspace_snapshot
        else "{}"
    )
    app_name = _app_name_from_plan(project_plan)
    frontend_root = f"apps/{app_name}/frontend" if app_name else "apps/<app.name>/frontend"
    return (
        "You are the build-task planning model for an app-generation workflow.\n"
        "This is a planning-only boundary. Do not call tools, do not call subagents, "
        "do not inspect files outside the provided WorkspaceSnapshot, and do not generate "
        "or modify code.\n"
        "Use the deterministic WorkspaceSnapshot as the primary source for the current "
        "source tree, package/framework conventions, route entry, API/data layer, shared "
        "modules, tests, and relevant existing files.\n"
        "Prepare an executable build task DAG from the confirmed ProjectPlan and "
        "WorkspaceSnapshot. Do not invent generic paths when an existing project "
        "convention is present in the snapshot.\n"
        f"Frontend path convention: all generated frontend code MUST live under the "
        f"virtual path `/{frontend_root}/` (resolved from ProjectPlan.app.name). Every "
        f"`src/...` path in the frontend-template-modification-boundary skill is relative "
        f"to `/{frontend_root}/`, so `src/pages/<PageKey>/index.tsx` must be planned as "
        f"`/{frontend_root}/src/pages/<PageKey>/index.tsx` in change_scope paths. Do NOT "
        f"use `Frontend/src/`, bare `src/`, or `/app/frontend/` — those are wrong for a "
        f"user-application workspace.\n"
        "Use confirmed page_detail_plans for frontend tasks and related data_sources for backend/data tasks. "
        "For page tasks, preserve and use page_goal, layout_design, operation_interactions, "
        "state_feedback, api_dependencies, response_bindings, page_navigation, permissions, "
        "and acceptance_criteria. "
        "The user-facing DAG should be organized by application-level setup, page-level generation, "
        "page-level verification, and final integration while the internal task dependencies still "
        "include API/data/shared-component prerequisites. "
        "ProjectPlan.api_contracts is the only source of fields. Preserve schema_refs, endpoint ids, "
        "request/response schema refs, and page response_bindings in task source references.\n"
        "Split work into independently verifiable tasks. Every task must include:\n"
        "- id: a stable unique task id\n"
        "- owner: frontend or data_source\n"
        "- title and description\n"
        "- dependencies: ids of prerequisite tasks\n"
        "- change_scope: [{operation: add|modify|delete, path, description}] using exact workspace-relative paths\n"
        "- impact_scope: {summary, affected_modules, public_contracts, risks}\n"
        "- can_run_in_parallel and parallel_reason\n"
        "- acceptance_criteria: concrete, testable completion conditions\n"
        "- verification_commands when known\n"
        "- status: pending for every newly planned task\n"
        "Return one JSON object only, without markdown fences or commentary. "
        "The JSON object must include workspace_analysis and tasks. It may include a dag "
        "summary, but dependencies on tasks are the source of truth for DAG edges. "
        "workspace_analysis must summarize the directories, entry files, stack, and "
        "conventions used from the WorkspaceSnapshot.\n\n"
        f"WorkspaceSnapshot:\n{snapshot_text}\n\n"
        f"ProjectPlan:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    del workspace
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).invoke(
        _task_preparation_prompt(
            project_plan,
            workspace_snapshot,
        )
    )
    content = getattr(result, "content", "")
    return content if isinstance(content, str) else str(content)


def prepare_build_tasks_with_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use a direct model boundary to prepare an executable Build DAG."""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(
        project_plan,
        workspace=workspace,
        workspace_snapshot=workspace_snapshot,
        settings=settings,
    )
    preparation_source = "direct_chat_model"

    build_task_plan = create_build_task_plan(
        project_plan,
        agent_note=agent_note,
        agent_plan=extract_json_object(agent_note),
        workspace_snapshot=workspace_snapshot,
    )
    build_task_plan["prepared_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": preparation_source,
    }
    build_task_plan["preparation_source"] = preparation_source
    return build_task_plan
