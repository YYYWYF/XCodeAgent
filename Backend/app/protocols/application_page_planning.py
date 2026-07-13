from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import (
    AgUiActionResult,
    build_ag_ui_action_stream,
)

from app.services.application_page_planning import (
    ConfirmPagePlanRequest,
    GeneratePagePlanRequest,
    PagePlanningQuestionsRequest,
    confirm_application_page_plan,
    generate_application_page_plan,
    generate_page_planning_questions,
)


PAGE_PLANNING_EVENT_NAME = "application-page-planning"


def application_page_planning_capabilities() -> dict[str, Any]:
    return {
        "name": "application-page-planning",
        "endpoint": "/application-page-planning/run",
        "transport": "ag-ui-sse",
        "actions": ["questions", "plan", "confirm"],
        "customEventName": PAGE_PLANNING_EVENT_NAME,
        "stateSnapshotKey": "pagePlanning",
        "workflowIndependent": True,
    }


def build_application_page_planning_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    planning_input = _page_planning_input(payload)

    async def operation() -> AgUiActionResult:
        action = planning_input.get("action")
        if action == "questions":
            request = PagePlanningQuestionsRequest.model_validate(planning_input)
            response = await generate_page_planning_questions(request)
            result: dict[str, Any] = {
                "questions": response.model_dump(by_alias=True)["questions"]
            }
            message = "页面规划细节问题已生成。"
        elif action == "plan":
            request = GeneratePagePlanRequest.model_validate(planning_input)
            response = await generate_application_page_plan(request)
            result = {"plan": response.model_dump(by_alias=True)["plan"]}
            message = "页面结构草案已生成，请审核。"
        elif action == "confirm":
            request = ConfirmPagePlanRequest.model_validate(planning_input)
            response = confirm_application_page_plan(request)
            result = {
                "confirmation": response.model_dump(
                    by_alias=True, exclude_none=True
                )
            }
            message = "页面结构已确认并写入 application.json。"
        else:
            raise ValueError("pagePlanning.action 必须是 questions、plan 或 confirm。")
        return AgUiActionResult(data={"action": action, **result}, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=PAGE_PLANNING_EVENT_NAME,
        state_key="pagePlanning",
        run_id_prefix="page-planning",
        operation=operation,
        error_message_prefix="页面规划失败",
        accept=accept,
    )


def _page_planning_input(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    page_planning = forwarded_props.get("pagePlanning")
    return page_planning if isinstance(page_planning, dict) else {}
