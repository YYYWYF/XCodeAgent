"""提供导航提醒所需的轻量 Git 业务文件计数。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.version_control import (
    _business_code_paths,
    _read_status,
    _resolve_independent_repository_root,
    _resolve_workspace_root,
)


class InspectPendingCountRequest(BaseModel):
    """校验返回欢迎页前的实时 Git 计数请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["inspect_pending_count"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)


class PendingCodeCount(BaseModel):
    """返回规范化工作区及未提交业务文件数量。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["inspect_pending_count"] = "inspect_pending_count"
    workspace_root: str = Field(alias="workspaceRoot")
    pending_code_count: int = Field(alias="pendingCodeCount", ge=0)


def inspect_pending_count(request: InspectPendingCountRequest) -> PendingCodeCount:
    """仅校验仓库和读取状态，沿用完整快照的过滤口径而不生成提交指纹。"""

    workspace = _resolve_workspace_root(request.workspace_root)
    repository = _resolve_independent_repository_root(workspace)
    _status_bytes, files = _read_status(repository)
    return PendingCodeCount(
        workspaceRoot=str(workspace),
        pendingCodeCount=len(_business_code_paths([item.path for item in files])),
    )
