"""远端分支动作的独立 AG-UI 协议。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.protocols.ag_ui_action_stream import (
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services.git_branch import GitBranchError, validate_branch_name
from app.services.repository_branch import (
    list_remote_branches,
    create_local_branch,
    delete_remote_branch,
    push_workspace_branch,
)


REPOSITORY_BRANCH_EVENT_NAME = "repository-branch"


class RepositoryBranchAction(BaseModel):
    """校验远端分支动作：检查存在性、新建、推送与显式删除分支。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["list", "check", "create", "push", "delete"]
    # push 的分支名从工作区配置读，所以不是所有动作都需要它；按动作校验。
    branch_name: str | None = Field(default=None, alias="branchName", max_length=255)
    # check/delete 只需要仓库地址；create/push 从工作区配置里读仓库地址，因此需要工作区。
    repo_url: str | None = Field(default=None, alias="repoUrl")
    workspace_root: str | None = Field(default=None, alias="workspaceRoot", max_length=4096)
    # create 动作的可选起点（历史分支名 / 提交 sha）。「基于历史版本继续开发」用：
    # 新分支从这条历史 ref 建出，而不是从当前 HEAD。仅 create 接受该字段。
    start_point: str | None = Field(default=None, alias="startPoint", max_length=255)

    @model_validator(mode="after")
    def validate_required_fields(self) -> "RepositoryBranchAction":
        """按动作校验各自必需的字段。"""

        if self.action in {"check", "create", "delete"} and not str(
            self.branch_name or ""
        ).strip():
            raise ValueError(f"{self.action} 必须提供 branchName。")
        if self.action in {"list", "check", "delete"} and not str(self.repo_url or "").strip():
            raise ValueError(f"{self.action} 必须提供 repoUrl。")
        if self.action in {"create", "push"} and not str(self.workspace_root or "").strip():
            raise ValueError(f"{self.action} 必须提供 workspaceRoot。")
        # startPoint 是「基于历史版本继续开发」的语义，只在 create 上有意义。
        if self.start_point is not None and self.action != "create":
            raise ValueError("startPoint 仅在 create 动作上有效。")
        return self


def repository_branch_capabilities() -> dict[str, Any]:
    """发布独立远端分支动作的公开协议能力。"""

    return {
        "name": "repository-branch",
        "endpoint": "/repository-branch/run",
        "transport": "ag-ui-sse",
        "actions": ["list", "check", "create", "push", "delete"],
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
        # 校验器已保证 check/create/delete 的 branchName 非空；这里收窄成 str 供下游使用。
        branch = request.branch_name or ""
        if request.action in {"list", "check"}:
            if request.action == "check":
                branch = validate_branch_name(branch)
            branches = await asyncio.to_thread(list_remote_branches, request.repo_url or "")
            if request.action == "list":
                return AgUiActionResult(
                    data={"action": "list", "branches": branches},
                    message=f"已读取远端 {len(branches)} 个分支。",
                )
            exists = branch in branches
            data = {
                "action": "check",
                "branchName": branch,
                "exists": exists,
                "branches": branches,
            }
            message = (
                f"远端已存在版本 {branch}。"
                if exists
                else f"远端暂无版本 {branch}。"
            )
            return AgUiActionResult(data=data, message=message)

        if request.action == "create":
            result = await asyncio.to_thread(
                create_local_branch,
                request.workspace_root or "",
                branch,
                start_point=request.start_point,
            )
            if result.get("status") == "failed":
                # 未创建本地分支时是业务失败，不能伪装成已创建并继续清空规划产物。
                raise GitBranchError(result.get("message") or "创建分支失败。")
            message = (
                f"已创建并推送到版本 {branch}。"
                if result.get("status") == "created"
                else f"版本 {branch} 已在本地创建。{result.get('message') or ''}"
            )
            # 动作数据里的 `status` 会顶掉信封的运行态（见 ag_ui_action_stream 的说明），
            # 所以分支结果的状态另起字段名 branchStatus。
            return AgUiActionResult(
                data={
                    "action": "create",
                    "branchName": result.get("branchName") or branch,
                    "branchStatus": result.get("status"),
                    "commitSha": result.get("commitSha") or "",
                    "message": result.get("message") or "",
                },
                message=message,
            )

        if request.action == "push":
            # 重试把当前分支推到远端。分支名/仓库地址都从工作区配置读，
            # 所以不需要请求里带 branchName；返回值同样把 branchName 带出来。
            result = await asyncio.to_thread(
                push_workspace_branch, request.workspace_root or ""
            )
            pushed = result.get("status") == "pushed"
            return AgUiActionResult(
                data={
                    "action": "push",
                    "branchName": result.get("branchName") or "",
                    "branchStatus": result.get("status"),
                    "commitSha": result.get("commitSha") or "",
                    "message": result.get("message") or "",
                },
                message=(
                    f"已推送到版本 {result.get('branchName') or ''}。"
                    if pushed
                    else f"版本 {result.get('branchName') or ''} 仍未推送到远端：{result.get('message') or '原因未知'}"
                ),
            )

        result = await asyncio.to_thread(
            delete_remote_branch, request.repo_url or "", branch
        )
        message = (
            f"已删除远端版本 {branch}。"
            if result.get("status") == "deleted"
            else f"删除远端版本 {branch} 失败：{result.get('message') or '原因未知'}"
        )
        return AgUiActionResult(
            data={
                "action": "delete",
                "branchName": result.get("branchName") or branch,
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
        error_message_prefix="远端版本操作失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
        # 只有 create 会改动本地 Git 与工作区分支，需要参与工作区互斥和删除保护。
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
