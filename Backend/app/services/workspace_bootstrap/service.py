"""Server-owned 的首次 Workspace Bootstrap 编排服务。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.application_lifecycle import complete_workspace_bootstrap
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer
from app.services.workspace_bootstrap.models import ArchiveLimits, WorkspaceBootstrapError
from app.services.workspace_bootstrap.requested_config import compile_template_requested_config
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient
from app.services.workspace_bootstrap.template_package import validate_template_package


class WorkspaceBootstrapService:
    """持有后台任务，保证 AG-UI 订阅断开不会取消实际 Bootstrap。"""

    def __init__(self, settings: Settings) -> None:
        """保存运行配置和按 Workspace 去重的后台任务表。"""

        self._settings = settings
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def trigger(self, workspace: str | Path) -> asyncio.Task[dict[str, Any]]:
        """创建或复用一个 Server-owned Bootstrap Task。"""

        root = Path(workspace).expanduser().resolve()
        key = str(root)
        async with self._lock:
            current = self._tasks.get(key)
            if current is not None and not current.done():
                return current
            task = asyncio.create_task(self._run(root), name=f"workspace-bootstrap:{root.name}")
            self._tasks[key] = task
            task.add_done_callback(
                lambda completed: self._clear_completed_task(key, completed)
            )
            return task

    def _clear_completed_task(
        self,
        key: str,
        completed: asyncio.Task[dict[str, Any]],
    ) -> None:
        """只移除自身，避免完成回调误删随后创建的新一轮任务。"""

        if self._tasks.get(key) is completed:
            self._tasks.pop(key, None)

    async def _run(self, workspace: Path) -> dict[str, Any]:
        """编译请求、下载校验、事务物化，并确保失败写入 lifecycle。"""

        download_path: Path | None = None
        template_mutation_coordinator.begin_preparation(workspace)
        try:
            requested_config = await asyncio.to_thread(compile_template_requested_config, workspace)
            template_mutation_coordinator.raise_if_preparation_cancelled(workspace)
            client = TemplateEngineClient(
                base_url=self._settings.template_engine_base_url,
                token=self._settings.template_engine_token,
                connect_timeout=self._settings.template_engine_connect_timeout_seconds,
                read_timeout=self._settings.template_engine_read_timeout_seconds,
                max_package_bytes=self._settings.template_package_max_bytes,
            )
            download = await client.generate(requested_config)
            download_path = download.temporary_path
            template_mutation_coordinator.raise_if_preparation_cancelled(workspace)
            package = await asyncio.to_thread(
                validate_template_package,
                download_path,
                ArchiveLimits(
                    max_package_bytes=self._settings.template_package_max_bytes,
                    max_files=self._settings.template_package_max_files,
                    max_extracted_bytes=self._settings.template_package_max_extracted_bytes,
                ),
            )
            template_mutation_coordinator.raise_if_preparation_cancelled(workspace)
            template_mutation_coordinator.enter_commit_section(workspace)
            await asyncio.to_thread(
                WorkspaceMaterializer().materialize,
                workspace=workspace,
                archive_path=package.archive_path,
                template_state=package.template_state,
            )
            lifecycle = await asyncio.to_thread(complete_workspace_bootstrap, workspace, succeeded=True)
            return {"workspaceRoot": str(workspace), "lifecycle": lifecycle.model_dump(mode="json", by_alias=True)}
        except Exception as exc:
            lifecycle = await asyncio.to_thread(
                complete_workspace_bootstrap,
                workspace,
                succeeded=False,
                error_message=str(exc),
            )
            if isinstance(exc, WorkspaceBootstrapError):
                raise
            raise WorkspaceBootstrapError(str(exc)) from exc
        finally:
            if download_path is not None:
                download_path.unlink(missing_ok=True)
            template_mutation_coordinator.finish(workspace)


def workspace_bootstrap_service(settings: Settings) -> WorkspaceBootstrapService:
    """为 FastAPI 进程创建唯一的 Bootstrap 服务实例。"""

    return WorkspaceBootstrapService(settings)
