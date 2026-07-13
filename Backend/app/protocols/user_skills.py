from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import (
    AgUiActionResult,
    build_ag_ui_action_stream,
)

from app.services.user_skill_documents import (
    CreateUserSkillRequest,
    DeleteUserSkillRequest,
    GetUserSkillRequest,
    SaveUserSkillRequest,
    create_user_skill_document,
    delete_user_skill,
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
        "actions": ["list", "get", "save", "create", "delete"],
        "customEventName": SKILL_CATALOG_EVENT_NAME,
        "stateSnapshotKey": "skillCatalog",
        "root": user_skills_root_label(),
        "workflowIndependent": True,
    }


def build_user_skills_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    skill_input = _skill_catalog_input(payload)
    action = skill_input.get("action")

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
            elif action == "create":
                request = CreateUserSkillRequest.model_validate(skill_input)
                document = create_user_skill_document(request.content)
                result_payload = {
                    "root": user_skills_root_label(),
                    "document": document.model_dump(by_alias=True),
                }
                message = f"已创建技能 {document.name}。"
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
            elif action == "delete":
                request = DeleteUserSkillRequest.model_validate(skill_input)
                deleted = delete_user_skill(request.relative_path)
                result_payload = {
                    "root": user_skills_root_label(),
                    "deleted": deleted.model_dump(by_alias=True),
                }
                message = f"已删除技能 {deleted.name}。"
            else:
                raise ValueError(
                    "skillCatalog.action 必须是 list、get、save、create 或 delete。"
                )

            response_payload: dict[str, Any] = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "completed",
                "action": action,
                **result_payload,
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
        return AgUiActionResult(
            data={"action": action, **result_payload},
            message=message,
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=SKILL_CATALOG_EVENT_NAME,
        state_key="skillCatalog",
        run_id_prefix="skills",
        operation=operation,
        error_message_prefix="技能操作失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
    )


def _skill_catalog_input(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    skill_catalog = forwarded_props.get("skillCatalog")
    return skill_catalog if isinstance(skill_catalog, dict) else {}
