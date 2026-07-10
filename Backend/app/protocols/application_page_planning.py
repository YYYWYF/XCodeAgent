from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi.encoders import jsonable_encoder

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
    encoder = EventEncoder(accept or "text/event-stream")
    thread_id = str(payload.get("threadId") or uuid4())
    run_id = str(payload.get("runId") or f"page-planning-{uuid4().hex[:12]}")
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(
            TextMessageStartEvent(messageId=message_id, role="assistant")
        )
        try:
            planning_input = _page_planning_input(payload)
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

            response_payload = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "completed",
                "action": action,
                **result,
            }
        except Exception as exc:
            message = f"页面规划失败：{type(exc).__name__}: {exc}"
            response_payload = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "failed",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

        safe_payload = jsonable_encoder(response_payload)
        yield encoder.encode(
            CustomEvent(name=PAGE_PLANNING_EVENT_NAME, value=safe_payload)
        )
        yield encoder.encode(
            StateSnapshotEvent(snapshot={"pagePlanning": safe_payload})
        )
        yield encoder.encode(
            TextMessageContentEvent(messageId=message_id, delta=message)
        )
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"pagePlanning": safe_payload},
            )
        )

    return stream()


def _page_planning_input(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    page_planning = forwarded_props.get("pagePlanning")
    return page_planning if isinstance(page_planning, dict) else {}
