from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.build_task_planner import create_build_task_plan
from app.utils.model_output import extract_json_object


def _task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
) -> str:
    snapshot_text = (
        json.dumps(workspace_snapshot, ensure_ascii=False, indent=2)
        if workspace_snapshot
        else "{}"
    )
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "Use the deterministic WorkspaceSnapshot as the primary source for the current "
        "source tree, package/framework conventions, route entry, API/data layer, shared "
        "modules, tests, and relevant existing files. If critical context is missing, "
        "you may do targeted read-only file inspection, but do not perform broad scans. "
        "This step is read-only: do not create, edit, move, or delete files.\n"
        "Prepare an executable build task DAG from the confirmed ProjectPlan and "
        "WorkspaceSnapshot. Do not invent generic paths when an existing project "
        "convention is present in the snapshot.\n"
        "Use confirmed page_detail_plans for frontend tasks and related data_sources for backend/data tasks. "
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
        "Return one JSON object only, with workspace_analysis and tasks. "
        "workspace_analysis must summarize inspected directories, entry files, stack, and conventions.\n\n"
        f"WorkspaceSnapshot:\n{snapshot_text}\n\n"
        f"ProjectPlan:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
) -> str:
    # Lazy imports avoid constructing Deep Agents before this live boundary is used.
    from app.agents import create_agent_bundle
    result = create_agent_bundle(workspace).main.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _task_preparation_prompt(
                        project_plan,
                        workspace_snapshot,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def prepare_build_tasks_with_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use the live Main Agent boundary to prepare executable build tasks."""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(
        project_plan,
        workspace=workspace,
        workspace_snapshot=workspace_snapshot,
    )
    preparation_source = "main_agent_live"

    build_task_plan = create_build_task_plan(
        project_plan,
        agent_note=agent_note,
        agent_plan=extract_json_object(agent_note),
        workspace_snapshot=workspace_snapshot,
    )
    build_task_plan["prepared_by"] = {
        "agent": "main-agent",
        "mode": "live",
        "model": settings.model_name,
        "source": preparation_source,
    }
    build_task_plan["preparation_source"] = preparation_source
    return build_task_plan
