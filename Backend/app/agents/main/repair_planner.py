from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.test_validation import create_repair_task_plan


def _repair_planning_prompt(
    *,
    test_report: dict[str, Any],
    revision_requests: list[dict[str, Any]],
    build_task_plan: dict[str, Any] | None,
) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "Review the test report and revision requests. Decide whether repair is "
        "needed and summarize a repair task plan for specialist code agents. "
        "Do not mark failed tests as passed. Do not silently change confirmed "
        "requirements, PageSpec, or API contracts.\n\n"
        f"TestReport:\n{json.dumps(test_report, ensure_ascii=False, indent=2)}\n\n"
        f"RevisionRequests:\n{json.dumps(revision_requests, ensure_ascii=False, indent=2)}\n\n"
        f"CurrentBuildTaskPlan:\n{json.dumps(build_task_plan or {}, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_main_agent(
    *,
    test_report: dict[str, Any],
    revision_requests: list[dict[str, Any]],
    build_task_plan: dict[str, Any] | None,
    workspace: str | None = None,
) -> str:
    from app.agents import create_agent_bundle

    result = create_agent_bundle(workspace).main.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _repair_planning_prompt(
                        test_report=test_report,
                        revision_requests=revision_requests,
                        build_task_plan=build_task_plan,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def plan_repairs_with_main_agent(
    *,
    test_report: dict[str, Any],
    revision_requests: list[dict[str, Any]],
    build_task_plan: dict[str, Any] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    settings = Settings.from_env()
    if not revision_requests:
        agent_note = "No repair required because all quality gate checks passed."
    else:
        agent_note = _invoke_live_main_agent(
            test_report=test_report,
            revision_requests=revision_requests,
            build_task_plan=build_task_plan,
            workspace=workspace,
        )

    repair_task_plan = create_repair_task_plan(
        revision_requests=revision_requests,
        agent_note=agent_note,
    )
    repair_task_plan["prepared_by"]["model"] = settings.model_name
    return repair_task_plan
