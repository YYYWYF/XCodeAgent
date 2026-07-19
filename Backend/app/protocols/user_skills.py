from __future__ import annotations

from typing import Any, AsyncIterator, Literal

from pydantic import Field

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
from app.services.user_skill_imports import (
    ImportUserSkillRequest,
    SkillImportError,
    import_user_skill_archive,
)
from app.services.user_skills import (
    ApiModel,
    list_user_skills,
    update_user_skill_enabled,
    user_skill_settings_label,
    user_skills_root_label,
)
from app.services.builtin_skills import (
    builtin_skills_root_label,
    list_builtin_skills,
)


SKILL_CATALOG_EVENT_NAME = "skill-catalog"


class SetUserSkillEnabledRequest(ApiModel):
    """校验用户技能启停动作的 AG-UI 输入。"""

    action: Literal["set-enabled"]
    relative_path: str = Field(min_length=1)
    enabled: bool


def user_skills_capabilities() -> dict[str, Any]:
    """描述独立技能目录 AG-UI 动作和公开状态契约。"""

    return {
        "name": "user-skills",
        "endpoint": "/skills/run",
        "transport": "ag-ui-sse",
        "actions": [
            "list",
            "get",
            "save",
            "create",
            "delete",
            "import",
            "set-enabled",
        ],
        "customEventName": SKILL_CATALOG_EVENT_NAME,
        "stateSnapshotKey": "skillCatalog",
        "root": user_skills_root_label(),
        "builtinRoot": builtin_skills_root_label(),
        "enablement": {
            "default": "enabled",
            "stateFile": user_skill_settings_label(),
            "disabledSkillsField": "disabledSkills",
        },
        "workflowIndependent": True,
    }


def build_user_skills_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """把技能目录动作包装成完整的 AG-UI 生命周期事件流。"""

    skill_input = _skill_catalog_input(payload)
    action = skill_input.get("action")

    async def operation() -> AgUiActionResult:
        if action == "list":
            catalog = list_user_skills()
            builtin_skills = list_builtin_skills()
            result_payload = {
                **catalog.model_dump(by_alias=True, exclude_none=True),
                "builtinRoot": builtin_skills_root_label(),
                "builtinSkills": [
                    skill.model_dump(by_alias=True, exclude_none=True)
                    for skill in builtin_skills
                ],
            }
            message = (
                f"已读取 {len(catalog.skills)} 个用户技能和 "
                f"{len(builtin_skills)} 个内置技能。"
            )
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
        elif action == "import":
            request = ImportUserSkillRequest.model_validate(skill_input)
            imported = import_user_skill_archive(
                request.file_name,
                request.archive_base64,
            )
            result_payload = imported.model_dump(by_alias=True, exclude_none=True)
            message = f"已导入技能 {imported.imported.name}。"
        elif action == "set-enabled":
            request = SetUserSkillEnabledRequest.model_validate(skill_input)
            skill = update_user_skill_enabled(
                request.relative_path,
                request.enabled,
            )
            result_payload = {
                "root": user_skills_root_label(),
                "skill": skill.model_dump(by_alias=True, exclude_none=True),
            }
            status_label = "开启" if request.enabled else "关闭"
            message = f"已{status_label}技能 {skill.name}。"
        else:
            raise ValueError(
                "skillCatalog.action 必须是 list、get、save、create、delete、import "
                "或 set-enabled。"
            )
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
        error_data=lambda exc: {
            "action": action,
            **(
                {"code": exc.code}
                if isinstance(exc, SkillImportError)
                else {}
            ),
        },
        accept=accept,
    )


def _skill_catalog_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取技能目录动作输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    skill_catalog = forwarded_props.get("skillCatalog")
    return skill_catalog if isinstance(skill_catalog, dict) else {}
