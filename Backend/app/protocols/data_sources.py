"""独立数据源目录的 AG-UI 协议适配。"""

from __future__ import annotations

from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.data_sources import mutate_catalog, public_catalog, validate_saved_source, validate_source


DATA_SOURCES_EVENT_NAME = "data-sources"
DataSourceActionName = Literal["list", "create", "update", "delete", "validate", "detail"]


class DataSourceRequest(BaseModel):
    """校验单个数据源 AG-UI 路由的边界输入。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    source_id: str | None = Field(default=None, alias="sourceId", max_length=128)
    operation_id: str | None = Field(default=None, alias="operationId", max_length=128)
    source: dict[str, Any] | None = None


def data_sources_capabilities() -> dict[str, Any]:
    """发布独立数据源 AG-UI 动作能力与状态契约。"""

    return {
        "name": "data-sources",
        "basePath": "/data-sources",
        "transport": "ag-ui-sse",
        "actions": ["list", "create", "update", "delete", "validate", "detail"],
        "endpoints": {
            "list": "/data-sources/list",
            "create": "/data-sources/create",
            "update": "/data-sources/update",
            "delete": "/data-sources/delete",
            "validate": "/data-sources/validate",
            "detail": "/data-sources/detail",
        },
        "customEventName": DATA_SOURCES_EVENT_NAME,
        "stateSnapshotKey": "dataSources",
        "stateDirectory": ".xcodeagent/datasource",
        "workflowIndependent": True,
        "databaseLimit": 1,
        "externalApiAuthentication": "disabled",
    }


def build_data_sources_ag_ui_stream(
    *,
    action: DataSourceActionName,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """把固定动作的数据源路由包装成完整 AG-UI 生命周期。"""

    action_input = _data_sources_input(payload)

    async def operation() -> AgUiActionResult:
        """执行一次严格校验后的固定数据源动作。"""

        request = DataSourceRequest.model_validate(action_input)
        _validate_action_input(request, action)
        if action == "list":
            catalog = public_catalog(request.workspace_root)
            return AgUiActionResult(
                data={"action": action, "catalog": catalog.model_dump(by_alias=True)},
                message=f"已读取 {len(catalog.sources)} 个数据源。",
            )
        if action == "detail":
            catalog = public_catalog(
                request.workspace_root,
                source_id=request.source_id,
                operation_id=request.operation_id,
            )
            return AgUiActionResult(
                data={"action": action, "catalog": catalog.model_dump(by_alias=True)},
                message="已读取接口详情。",
            )
        if action == "validate":
            if request.source is not None:
                result = validate_source(request.source, request.workspace_root)
            elif request.source_id:
                result = validate_saved_source(request.workspace_root, request.source_id)
            else:
                raise ValueError("校验数据源必须提供 source 或 sourceId。")
            return AgUiActionResult(
                data={"action": action, "validation": result},
                message="数据源校验通过。",
            )
        catalog = mutate_catalog(
            request.workspace_root,
            action=action,
            source=request.source,
            source_id=request.source_id,
        )
        labels = {"create": "创建", "update": "更新", "delete": "删除"}
        return AgUiActionResult(
            data={"action": action, "catalog": catalog.model_dump(by_alias=True)},
            message=f"已{labels[action]}数据源。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=DATA_SOURCES_EVENT_NAME,
        state_key="dataSources",
        run_id_prefix="data-sources",
        operation=operation,
        error_message_prefix="数据源操作失败",
        error_data=lambda exc: {"action": action},
        accept=accept,
    )


def _validate_action_input(
    request: DataSourceRequest,
    action: DataSourceActionName,
) -> None:
    """校验固定动作所需字段，避免不同路由之间混用请求参数。"""

    if action == "list":
        if request.source is not None or request.operation_id is not None:
            raise ValueError("读取数据源列表只需要 workspaceRoot，接口详情请使用 detail 端点。")
        if request.source_id is not None:
            raise ValueError("读取数据源列表只需要 workspaceRoot。")
        return
    if action == "detail":
        if request.source is not None or not request.source_id:
            raise ValueError("读取数据源详情必须提供 sourceId。")
        return
    if action in {"create", "update"} and request.source is None:
        raise ValueError(f"{action} 数据源必须提供 source。")
    if action == "delete" and not request.source_id:
        raise ValueError("删除数据源必须提供 sourceId。")
    if action == "validate" and ((request.source is None) == (request.source_id is None)):
        raise ValueError("校验数据源必须且只能提供 source 或 sourceId。")


def _data_sources_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 forwardedProps 提取数据源动作输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    value = forwarded_props.get("dataSources")
    return value if isinstance(value, dict) else {}
