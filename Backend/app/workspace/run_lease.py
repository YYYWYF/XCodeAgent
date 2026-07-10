from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


WORKSPACE_BUSY_ERROR_CODE = "workspace_busy"


@dataclass(frozen=True)
class WorkspaceRunOwner:
    workspace_key: str
    thread_id: str
    run_id: str


class WorkspaceBusyError(RuntimeError):
    code = WORKSPACE_BUSY_ERROR_CODE

    def __init__(self, owner: WorkspaceRunOwner) -> None:
        self.owner = owner
        super().__init__(
            "该工作区已有 Workflow 正在执行，请等待当前任务结束后重试。"
        )


class WorkspaceRunLease:
    def __init__(
        self,
        registry: WorkspaceRunLeaseRegistry,
        owner: WorkspaceRunOwner,
    ) -> None:
        self._registry = registry
        self.owner = owner
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._registry.release(self.owner)


class WorkspaceRunLeaseRegistry:
    """Process-local non-blocking leases for file-mutating workflow runs."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[str, WorkspaceRunOwner] = {}

    def acquire(
        self,
        *,
        workspace_root: str | None,
        project_id: str | None,
        thread_id: str,
        run_id: str,
    ) -> WorkspaceRunLease:
        workspace_key = effective_workspace_key(workspace_root, project_id)
        owner = WorkspaceRunOwner(
            workspace_key=workspace_key,
            thread_id=thread_id,
            run_id=run_id,
        )
        with self._lock:
            existing = self._active.get(workspace_key)
            if existing is not None:
                raise WorkspaceBusyError(existing)
            self._active[workspace_key] = owner
        return WorkspaceRunLease(self, owner)

    def release(self, owner: WorkspaceRunOwner) -> None:
        with self._lock:
            if self._active.get(owner.workspace_key) == owner:
                self._active.pop(owner.workspace_key, None)

    def active_owner(
        self,
        *,
        workspace_root: str | None,
        project_id: str | None,
    ) -> WorkspaceRunOwner | None:
        workspace_key = effective_workspace_key(workspace_root, project_id)
        with self._lock:
            return self._active.get(workspace_key)


def effective_workspace_key(
    workspace_root: str | None,
    project_id: str | None,
) -> str:
    if workspace_root:
        root = Path(workspace_root).expanduser().resolve(strict=False)
    else:
        root = (Path.cwd() / "var" / "workspaces" / (project_id or "demo-project")).resolve(
            strict=False
        )
    return os.path.normcase(str(root))


workspace_run_leases = WorkspaceRunLeaseRegistry()
