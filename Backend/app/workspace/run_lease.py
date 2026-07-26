from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


@dataclass(frozen=True)
class WorkspaceRunOwner:
    """保存一个活动运行的工作区与资源登记信息。"""

    workspace_key: str
    scope_type: str
    target_id: str
    thread_id: str
    run_id: str
    resource_keys: tuple[str, ...]


class WorkspaceRunLease:
    def __init__(
        self,
        registry: WorkspaceRunLeaseRegistry,
        owner: WorkspaceRunOwner,
    ) -> None:
        """创建可幂等释放的活动运行登记句柄。"""

        self._registry = registry
        self.owner = owner
        self._released = False

    def release(self) -> None:
        """幂等移除当前活动运行登记。"""

        if self._released:
            return
        self._released = True
        self._registry.release(self.owner)


class WorkspaceRunLeaseRegistry:
    """记录进程内活动运行的生命周期，不用资源交集阻断并行请求。"""

    def __init__(self) -> None:
        """初始化进程内活动运行登记表。"""

        self._lock = Lock()
        self._active: dict[str, WorkspaceRunOwner] = {}

    def acquire(
        self,
        *,
        workspace_root: str | None,
        project_id: str | None,
        execution_scope: dict[str, str] | None = None,
        resource_claims: list[dict[str, str]] | None = None,
        thread_id: str,
        run_id: str,
    ) -> WorkspaceRunLease:
        """登记同工作区运行及资源集合，并返回用于结束时清理的句柄。"""

        workspace_key = effective_workspace_key(workspace_root, project_id)
        owner = WorkspaceRunOwner(
            workspace_key=workspace_key,
            scope_type=_scope_type(execution_scope),
            target_id=_scope_target(execution_scope),
            thread_id=thread_id,
            run_id=run_id,
            resource_keys=_resource_keys(execution_scope, resource_claims),
        )
        with self._lock:
            self._active[_owner_key(owner)] = owner
        return WorkspaceRunLease(self, owner)

    def release(self, owner: WorkspaceRunOwner) -> None:
        """仅在 owner 仍匹配时移除对应活动运行。"""

        with self._lock:
            if self._active.get(_owner_key(owner)) == owner:
                self._active.pop(_owner_key(owner), None)

    def active_owner(
        self,
        *,
        workspace_root: str | None,
        project_id: str | None,
    ) -> WorkspaceRunOwner | None:
        """返回指定工作区任意一个活动运行，供状态诊断使用。"""

        workspace_key = effective_workspace_key(workspace_root, project_id)
        with self._lock:
            return next(
                (
                    owner
                    for owner in self._active.values()
                    if owner.workspace_key == workspace_key
                ),
                None,
            )


def _scope_type(scope: dict[str, str] | None) -> str:
    """缺少显式范围时按应用级执行处理，保持旧调用的安全语义。"""

    value = str((scope or {}).get("type") or "application")
    return value if value in {"application", "page", "data_source", "endpoint"} else "application"


def _scope_target(scope: dict[str, str] | None) -> str:
    """返回租约范围的稳定目标标识。"""

    return str((scope or {}).get("targetId") or "application")


def _owner_key(owner: WorkspaceRunOwner) -> str:
    """生成允许同工作区多页面并存的进程内租约键。"""

    return "\0".join((owner.workspace_key, owner.scope_type, owner.target_id, owner.run_id))


def _resource_keys(
    execution_scope: dict[str, str] | None,
    resource_claims: list[dict[str, str]] | None,
) -> tuple[str, ...]:
    """优先使用服务端解析的资源声明，旧调用则退回单一执行范围。"""

    keys = [
        f"{str(item.get('type') or '')}:{str(item.get('targetId') or item.get('target_id') or '')}"
        for item in resource_claims or []
        if isinstance(item, dict)
        and item.get("type")
        and (item.get("targetId") or item.get("target_id"))
    ]
    if keys:
        return tuple(dict.fromkeys(keys))
    return (f"{_scope_type(execution_scope)}:{_scope_target(execution_scope)}",)


def effective_workspace_key(
    workspace_root: str | None,
    project_id: str | None,
) -> str:
    """把显式工作区或默认项目目录规范化为稳定登记键。"""

    if workspace_root:
        root = Path(workspace_root).expanduser().resolve(strict=False)
    else:
        root = (Path.cwd() / "var" / "workspaces" / (project_id or "demo-project")).resolve(
            strict=False
        )
    return os.path.normcase(str(root))


workspace_run_leases = WorkspaceRunLeaseRegistry()
