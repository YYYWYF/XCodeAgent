"""实体建表的独立 AG-UI 产品协议。"""

import asyncio
from typing import Any, AsyncIterator

from app.domain.entity_tables import EntityTableRequest
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.entity_tables import run_entity_table_action
from app.services.managed_table_client import ManagedTableError


def _entity_table_error_data(error: Exception) -> dict[str, Any]:
    """仅投射安全的产品提示，使前端不靠错误文案或 HTTP 状态猜测后续动作。"""

    return {"issue": error.issue} if isinstance(error, ManagedTableError) else {}


def entity_tables_capabilities() -> dict[str, Any]:
    """发布实体 SQL 预览、明确创建、数据丢弃重建与只读刷新合同。"""

    return {"name": "entity-tables", "endpoint": "/entity-tables/run", "transport": "ag-ui-sse",
            "actions": ["prepare", "execute", "rebuild", "refresh"], "customEventName": "entity-tables",
            "stateSnapshotKey": "entityTables", "workflowIndependent": True}


def build_entity_tables_stream(*, payload: dict[str, Any], accept: str | None = None) -> AsyncIterator[str]:
    """校验请求后通过标准 AG-UI 生命周期返回成功或业务失败。"""

    async def operation() -> AgUiActionResult:
        """只调用本次动作，不进入旧实体绑定工作流。"""

        forwarded = payload.get("forwardedProps") or {}
        request = EntityTableRequest.model_validate(forwarded.get("entityTables") or {})
        result = await asyncio.to_thread(run_entity_table_action, request)
        return AgUiActionResult(data=result, message={
            "prepare": "已生成实体建表 SQL，请确认后执行。",
            "execute": "SaaS 已受理，请刷新查看执行结果。",
            "rebuild": "SaaS 已受理重建请求，请等待原操作完成；期间请勿启动应用。",
            "refresh": "已读取原操作及当前实体结构检查结果。",
        }[request.action])

    return build_ag_ui_action_stream(
        payload=payload, event_name="entity-tables", state_key="entityTables",
        run_id_prefix="entity-tables", operation=operation,
        error_message_prefix="实体建表操作失败", accept=accept,
        error_data=_entity_table_error_data,
    )
