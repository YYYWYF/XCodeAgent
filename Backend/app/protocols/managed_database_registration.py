"""通过已有生命周期 AG-UI 入口执行模拟数据库注册。"""

from __future__ import annotations

import asyncio

from app.protocols.ag_ui_action_stream import AgUiActionResult
from app.services.application_lifecycle import (
    ApplicationLifecycleMissingError, application_lifecycle_payload, load_application_lifecycle,
)
from app.services.managed_database_registration import ensure_managed_database_registration


async def register_managed_database_action(workspace: str, application_id: str) -> AgUiActionResult:
    """验证工作区身份后注册，返回已持久化的绑定及当前配置版本。"""

    state = load_application_lifecycle(workspace)
    if state is None:
        raise ApplicationLifecycleMissingError("注册模拟数据库前必须先创建应用生命周期。")
    if state.application.id != application_id:
        raise ValueError("当前工作区属于另一个应用，不能注册模拟数据库。")
    application = await asyncio.to_thread(ensure_managed_database_registration, workspace)
    return AgUiActionResult(
        data={
            "action": "register_managed_database",
            "lifecycle": application_lifecycle_payload(state),
            "managedDatabase": application["managedDatabase"],
            "configRevision": application["configRevision"],
        },
        message="内置数据源资源已注册，后续使用已保存的 datasourceKey 和固定挂载。",
    )
