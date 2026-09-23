"""远端分支动作的独立 AG-UI 协议。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.protocols.ag_ui_action_stream import (
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services.repository_branch import (
    check_remote_branch,
    create_local_branch,
    delete_remote_branch,
)


REPOSITORY_BRANCH_EVENT_NAME = "repository-branch"


class RepositoryBranchAction(BaseModel):
    """校验远端分支动作：检查存在性、新建分支、删除分支。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["check", "create", "delete"]
    branch_name: str = Field(alias="branchName", min_length=1, max_length=255)
    # check/delete 只需要仓库地址；create 从工作区配置里读仓库地址，因此需要工作区。
    repo_url: str | None = Field(default=None, alias="repoUrl")
    workspace_root: str | None = Field(default=None, alias="workspaceRoot", max_length=4096)
    # 用户在新建分支时是否已确认覆盖远端同名分支。
    allow_overwrite: bool = Field(default=False, alias="allowOverwrite")

    @model_validator(mode="after")
    def validate_required_fields(self) -> "RepositoryBranchAction":
        """按动作校验各自必需的字段。"""

        if self.action in {"check", "delete"} and not str(self.repo_url or "").strip():
            raise ValueError(f"{self.action} 必须提供 repoUrl。")
        if self.action == "create" and not str(self.workspace_root or "").strip():
            raise ValueError("create 必须提供 workspaceRoot。")
        return self


def repository_branch_capabilities() -> dict[str, Any]:
    """发布独立远端分支动作的公开协议能力。"""

    return {
        "name": "repository-branch",
        "endpoint": "/repository-branch/run",
        "transport": "ag-ui-sse",
        "actions": ["check", "create", "delete"],
        "customEventName": REPOSITORY_BRANCH_EVENT_NAME,
        "stateSnapshotKey": "repositoryBranch",
        "workflowIndependent": True,
    }


def build_repository_branch_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """执行远端分支动作，并投射完整 AG-UI 生命周期。"""

    branch_input = _repository_branch_input(payload)

    async def operation() -> AgUiActionResult:
        """在独立线程执行同步 Git 动作，避免阻塞事件循环。"""

        request = RepositoryBranchAction.model_validate(branch_input)
        if request.action == "check":
            exists = await asyncio.to_thread(
                check_remote_branch, request.repo_url or "", request.branch_name
            )
            data = {
                "action": "check",
                "branchName": request.branch_name,
                "exists": exists,
            }
            message = (
                f"远端已存在分支 {request.branch_name}。"
                if exists
                else f"远端暂无分支 {request.branch_name}。"
            )
            return AgUiActionResult(data=data, message=message)

        if request.action == "create":
            result = await asyncio.to_thread(
                create_local_branch,
                request.workspace_root or "",
                request.branch_name,
                allow_overwrite=request.allow_overwrite,
            )
            message = (
                f"已创建并推送到分支 {request.branch_name}。"
                if result.get("status") == "created"
                else f"分支 {request.branch_name} 已在本地创建。{result.get('message') or ''}"
            )
            # 动作数据里的 `status` 会顶掉信封的运行态（见 ag_ui_action_stream 的说明），
            # 所以分支结果的状态另起字段名 branchStatus。
            return AgUiActionResult(
                data={
                    "action": "create",
                    "branchName": result.get("branchName") or request.branch_name,
                    "branchStatus": result.get("status"),
                    "commitSha": result.get("commitSha") or "",
                    "message": result.get("message") or "",
                },
                message=message,
            )

        result = await asyncio.to_thread(
            delete_remote_branch, request.repo_url or "", request.branch_name
        )
        message = (
            f"已删除远端分支 {request.branch_name}。"
            if result.get("status") == "deleted"
            else f"删除远端分支 {request.branch_name} 失败：{result.get('message') or '原因未知'}"
        )
        return AgUiActionResult(
            data={
                "action": "delete",
                "branchName": result.get("branchName") or request.branch_name,
                "branchStatus": result.get("status"),
                "message": result.get("message") or "",
            },
            message=message,
        )

    action = str(branch_input.get("action") or "")
    return build_ag_ui_action_stream(
        payload=payload,
        event_name=REPOSITORY_BRANCH_EVENT_NAME,
        state_key="repositoryBranch",
        run_id_prefix="repository-branch",
        operation=operation,
        error_message_prefix="远端分支操作失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
        # 只有 create 会改动工作区（切分支），需要参与工作区互斥保护。
        workspace_root=str(branch_input.get("workspaceRoot") or "") or None
        if action == "create"
        else None,
        register_workspace_run=action == "create",
    )


def _repository_branch_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取远端分支业务输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    repository_branch = forwarded_props.get("repositoryBranch")
    return repository_branch if isinstance(repository_branch, dict) else {}
