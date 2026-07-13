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

from app.services.user_skill_documents import (
    GetUserSkillRequest,
    SaveUserSkillRequest,
    read_user_skill_document,
    save_user_skill_document,
)
from app.services.user_skills import (
    list_user_skills,
    user_skills_root_label,
)


SKILL_CATALOG_EVENT_NAME = "skill-catalog"


def user_skills_capabilities() -> dict[str, Any]:
    return {
        "name": "user-skills",
        "endpoint": "/skills/run",
        "transport": "ag-ui-sse",
        "actions": ["list", "get", "save"],
        "customEventName": SKILL_CATALOG_EVENT_NAME,
        "stateSnapshotKey": "skillCatalog",
        "root": user_skills_root_label(),
        "workflowIndependent": True,
    }


def build_user_skills_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    encoder = EventEncoder(accept or "text/event-stream")
    thread_id = str(payload.get("threadId") or uuid4())
    run_id = str(payload.get("runId") or f"skills-{uuid4().hex[:12]}")
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(
            TextMessageStartEvent(messageId=message_id, role="assistant")
        )
        try:
            skill_input = _skill_catalog_input(payload)
            action = skill_input.get("action")
            if action == "list":
                catalog = list_user_skills()
                result_payload = catalog.model_dump(by_alias=True, exclude_none=True)
                message = f"已读取 {len(catalog.skills)} 个用户技能。"
            elif action == "get":
                request = GetUserSkillRequest.model_validate(skill_input)
                document = read_user_skill_document(request.relative_path)
                result_payload = {
                    "root": user_skills_root_label(),
                    "document": document.model_dump(by_alias=True),
                }
                message = f"已读取技能 {document.name}。"
            elif action == "save":
                request = SaveUserSkillRequest.model_validate(skill_input)
                document = save_user_skill_document(
                    request.relative_path,
                    request.content,
                    request.expected_revision,
                )
                result_payload = {
                    "root": user_skills_root_label(),
                    "document": document.model_dump(by_alias=True),
                }
                message = f"已保存技能 {document.name}。"
            else:
                raise ValueError("skillCatalog.action 必须是 list、get 或 save。")

            response_payload: dict[str, Any] = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "completed",
                "action": action,
                **result_payload,
            }
        except Exception as exc:
            message = f"技能操作失败：{type(exc).__name__}: {exc}"
            response_payload = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "failed",
                "action": skill_input.get("action") if "skill_input" in locals() else None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

        safe_payload = jsonable_encoder(response_payload)
        yield encoder.encode(
            CustomEvent(name=SKILL_CATALOG_EVENT_NAME, value=safe_payload)
        )
        yield encoder.encode(
            StateSnapshotEvent(snapshot={"skillCatalog": safe_payload})
        )
        yield encoder.encode(
            TextMessageContentEvent(messageId=message_id, delta=message)
        )
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"skillCatalog": safe_payload},
            )
        )

    return stream()


def _skill_catalog_input(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    skill_catalog = forwarded_props.get("skillCatalog")
    return skill_catalog if isinstance(skill_catalog, dict) else {}
