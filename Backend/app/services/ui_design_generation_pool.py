"""UI 设计稿解耦式并发生成池（方案 B）。

把「换一换 / 选模板」的单页设计稿生成从 LangGraph 节点的同步路径解耦为
进程级 asyncio worker 池。ui_confirmation 节点只负责把生成意图（工作区、目标页、
动作、模板）登记到池并立即返回；池在后台用 N 个 worker 并发调用 LLM 生成、
落盘 .tsx、更新 specs/ui-designs.json。多页因此可在任意点击间隔下并发生成，
不再受「同一 thread 不能并发 Graph run」的 checkpoint 约束，也不再受单 run 内
Semaphore 的 3 页上限与前端 3 页 acting 上限影响。

进度由前端通过「无操作 resume」轮询读取 ui-designs.json 获得（全程走 AG-UI
StateSnapshot）：入队后页面状态为 queued，worker 领取后为 generating，结束后为
confirmed / generation_failed。这些状态均落盘，进程重启后由 ui_confirmation 节点
把仍处于 queued/generating 且池未在处理的页重新入队，实现自愈恢复。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from threading import Event
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.agents.model_business_context import bind_model_action_run
from app.services.run_workers import bind_run_workers
from app.services.ui_design_generation_task import (
    UiDesignGenerationCancelled, UiDesignGenerationTask, generate_page_entry,
)
from app.services.ui_design_generator import persist_page_code
from app.services.ui_design_manifest import UI_MANIFEST_SCHEMA_VERSION, build_ui_page_manifest
from app.services.workspace_process_registry import workspace_process_registry
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path, write_ui_designs_json

logger = logging.getLogger("uvicorn.error")
UI_DESIGN_STATUS_QUEUED = "queued"
UI_DESIGN_STATUS_GENERATING = "generating"


@dataclass
class _Generation:
    """每次提交拥有独立身份、单向取消信号及真实退出回执。"""

    task: UiDesignGenerationTask
    id: str = field(default_factory=lambda: f"ui-design-{uuid4().hex}")
    cancelled: Event = field(default_factory=Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = False
    failure: Exception | None = None


class UiDesignGenerationPool:
    """生成线程完整退出前保留页面占用，终态与代码由当前 owner 一次提交。"""

    def __init__(self, concurrency: int) -> None:
        """初始化独立任务队列和工作区提交锁。"""
        self._concurrency = max(1, concurrency)
        self._queue: asyncio.Queue[_Generation] = asyncio.Queue()
        self._jobs: dict[tuple[str, str], _Generation] = {}
        self._deleting_workspaces: set[str] = set()
        self._stopping_workspaces: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._workers: list[asyncio.Task[Any]] = []

    def _lock_for(self, workspace: str) -> asyncio.Lock:
        """串行化同工作区清单与设计稿提交。"""
        return self._locks.setdefault(workspace, asyncio.Lock())

    async def _ensure_started(self) -> None:
        """常驻 worker 使用空 Context，禁止继承首次提交的 Run 身份。"""
        if not self._workers:
            for _ in range(self._concurrency):
                self._workers.append(asyncio.create_task(self._worker_loop(), context=contextvars.Context()))

    def is_active(self, workspace: str, page_id: str) -> bool:
        """停止请求等待退出期间仍计为活跃，禁止同页重入。"""
        return (workspace, page_id) in self._jobs

    def pending_page_ids(self, workspace: str) -> set[str]:
        """返回包括正在停止在内的全部真实在途页面。"""
        return {page_id for root, page_id in self._jobs if root == workspace}

    @staticmethod
    def _read_manifest(workspace: str, project_id: str) -> dict[str, Any]:
        """读取最新清单，首次提交时准备待确认骨架。"""
        manifest = load_ui_designs_json(ui_designs_json_path({"workspace": workspace, "project_id": project_id}))
        return manifest if manifest.get("pages") else {
            "schema_version": UI_MANIFEST_SCHEMA_VERSION, "confirmation_status": "pending_user_confirmation", "pages": []}

    @staticmethod
    def _replace_page(manifest: dict, page_id: str, entry: dict) -> None:
        """仅替换目标页，保留其他页面最新结果。"""
        pages = manifest.setdefault("pages", [])
        for index, page in enumerate(pages):
            if page.get("pageId") == page_id:
                pages[index] = entry
                return
        pages.append(entry)

    async def submit(self, tasks: list[UiDesignGenerationTask]) -> list[str]:
        """登记独立生成代次，原任务尚未退出或工作区正在停止时不接受新任务。"""
        await self._ensure_started()
        accepted = []
        for task in tasks:
            async with self._lock_for(task.workspace):
                if task.workspace in self._deleting_workspaces | self._stopping_workspaces:
                    raise RuntimeError("工作区正在停止设计稿生成，请等待后台退出。")
                key = (task.workspace, task.page_id)
                if key in self._jobs:
                    continue
                job = _Generation(task)
                self._jobs[key] = job
                try:
                    self._commit(job, self._entry(job, UI_DESIGN_STATUS_QUEUED))
                except BaseException:
                    self._jobs.pop(key, None)
                    raise
                self._queue.put_nowait(job)
                accepted.append(task.page_id)
        return accepted

    def _entry(self, job: _Generation, status: str, error: str = "") -> dict:
        """为当前生成代次构造运行状态，不把取消当作新设计稿确认。"""
        task = job.task
        return {**build_ui_page_manifest(task.spec_page, page_key=task.page_key,
            status=status, template_id=task.template_id, error=error), "generation_id": job.id}

    def _commit(self, job: _Generation, entry: dict) -> None:
        """在工作区锁内核对精确 owner，代码和清单提交期间不允许取消请求插入。"""
        task = job.task
        if self._jobs.get((task.workspace, task.page_id)) is not job:
            raise UiDesignGenerationCancelled("原页面生成任务已失效。")
        if entry.get("status") == "confirmed":
            if job.cancelled.is_set():
                raise UiDesignGenerationCancelled("页面设计生成已取消。")
            entry["code_path"] = persist_page_code(task.project_dir, task.page_key, entry["code"])
        entry["generation_id"] = job.id
        manifest = self._read_manifest(task.workspace, task.project_id)
        self._replace_page(manifest, task.page_id, entry)
        write_ui_designs_json({"workspace": task.workspace, "project_id": task.project_id}, manifest)

    async def _worker_loop(self) -> None:
        """领取精确任务对象，旧队列项不能触碰同页新任务。"""
        while True:
            job = await self._queue.get()
            task = job.task
            key = (task.workspace, task.page_id)
            try:
                if self._jobs.get(key) is not job:
                    continue
                job.active = True
                logger.info("ui_design_generation.started generationId=%s pageId=%s workspace=%s", job.id, task.page_id, task.workspace)
                async with self._lock_for(task.workspace):
                    if not job.cancelled.is_set():
                        self._commit(job, self._entry(job, UI_DESIGN_STATUS_GENERATING))
                if not job.cancelled.is_set():
                    await self._process(job)
            except UiDesignGenerationCancelled:
                pass
            except Exception as exc:
                logger.exception("ui_design_pool_worker_failed generation_id=%s", job.id)
                async with self._lock_for(task.workspace):
                    if not job.cancelled.is_set():
                        self._commit(job, self._entry(job, "generation_failed", str(exc)))
            finally:
                # 线程结束后才发布 cancelled；先释放 owner 再通知等待者，重试不与旧清理竞争。
                try:
                    if self._jobs.get(key) is job:
                        async with self._lock_for(task.workspace):
                            if job.cancelled.is_set() and task.workspace not in self._deleting_workspaces:
                                self._commit(job, self._entry(job, "cancelled", "用户已停止本次生成。"))
                except Exception as exc:
                    job.failure = exc
                    logger.exception("ui_design_stop_persistence_failed generation_id=%s", job.id)
                finally:
                    if self._jobs.get(key) is job:
                        self._jobs.pop(key, None)
                    logger.info("ui_design_generation.exited generationId=%s pageId=%s cancelled=%s", job.id, task.page_id, job.cancelled.is_set())
                    job.done.set()
                    self._queue.task_done()

    async def _process(self, job: _Generation) -> None:
        """逐任务绑定模型取消身份，并等待同步线程完成后才能收口。"""
        with bind_run_workers(job.id, job.task.workspace), bind_model_action_run(job.id):
            future = asyncio.create_task(asyncio.to_thread(
                generate_page_entry, job.task, should_cancel=job.cancelled.is_set))
            try:
                entry = await asyncio.shield(future)
            except asyncio.CancelledError:
                self._request_cancel(job)
                # asyncio Task 取消不能证明线程已退出；线程的 finally 完成后才清除 owner。
                await asyncio.gather(future, return_exceptions=True)
                raise
        async with self._lock_for(job.task.workspace):
            if job.cancelled.is_set():
                raise UiDesignGenerationCancelled("页面设计生成已取消。")
            self._commit(job, entry)

    def _request_cancel(self, job: _Generation) -> None:
        """永久撤销此代任务，并在模型 HTTP 字节流边界中断响应。"""
        job.cancelled.set()
        workspace_process_registry.cancel_run(job.id)
        logger.info("ui_design_generation.stop_requested generationId=%s pageId=%s", job.id, job.task.page_id)

    async def _cancel_jobs(self, jobs: list[_Generation], timeout_seconds: float) -> list[str]:
        """先同时撤销所有目标，再等待真实线程退出；超时保留 stopping 和占用。"""
        for job in jobs:
            self._request_cancel(job)
            async with self._lock_for(job.task.workspace):
                if self._jobs.get((job.task.workspace, job.task.page_id)) is not job:
                    continue
                if job.task.workspace not in self._deleting_workspaces:
                    self._commit(job, self._entry(job, "stopping" if job.active else "cancelled"))
                if not job.active:
                    self._jobs.pop((job.task.workspace, job.task.page_id), None)
                    job.done.set()
        try:
            await asyncio.wait_for(asyncio.gather(*(job.done.wait() for job in jobs)), max(timeout_seconds, 0.01))
        except TimeoutError:
            pass
        if any(job.failure for job in jobs):
            raise RuntimeError("后台已退出，但停止状态保存失败，请同步后重试。")
        return [job.task.page_id for job in jobs if not job.done.is_set()]

    async def cancel_page(self, workspace: str, page_id: str, *, generation_id: str | None = None,
            timeout_seconds: float = 30.0) -> bool:
        """只停止用户所见代次，成功回执意味着线程与最终写入均已完成。"""
        job = self._jobs.get((workspace, page_id))
        if job is None:
            # 进程重启后已无旧线程；用户显式停止同一 stopping 代次才可收口，不能重开模型。
            async with self._lock_for(workspace):
                manifest = self._read_manifest(workspace, "")
                page = next((page for page in manifest.get("pages", []) if page.get("pageId") == page_id), None)
                if page and generation_id and page.get("generation_id") == generation_id and page.get("status") == "stopping":
                    self._replace_page(manifest, page_id, {**page, "status": "cancelled", "error": "设计稿生成已停止。"})
                    write_ui_designs_json({"workspace": workspace}, manifest)
                    return True
            return False
        if generation_id is not None and job.id != generation_id:
            raise ValueError("页面生成任务已变化，请刷新后操作。")
        remaining = await self._cancel_jobs([job], timeout_seconds)
        if remaining:
            raise TimeoutError("后台仍在退出，请等待后重试停止；当前尚未停止成功。")
        return True

    @asynccontextmanager
    async def planning_stop(self, workspace: str):
        """规划 End 期间阻止新提交，排空页面 writer 后才计算结束指纹并清理生命周期。"""
        if workspace in self._stopping_workspaces:
            raise RuntimeError("正在结束当前规划，请等待后台退出。")
        self._stopping_workspaces.add(workspace)
        try:
            jobs = [job for (root, _), job in self._jobs.items() if root == workspace]
            if await self._cancel_jobs(jobs, 30.0):
                raise TimeoutError("UI 设计稿后台仍在退出，尚未结束规划，请再次点击结束任务。")
            yield
        finally:
            self._stopping_workspaces.discard(workspace)

    async def cancel_workspace(self, workspace: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        """应用删除保持工作区栅栏，并等待全部页面真实退出。"""
        self._deleting_workspaces.add(workspace)
        jobs = [job for (root, _), job in self._jobs.items() if root == workspace]
        queued = sum(not job.active for job in jobs)
        remaining = await self._cancel_jobs(jobs, timeout_seconds)
        return {"cancelledQueuedCount": queued, "remainingPageIds": remaining}

    def end_workspace_deletion(self, workspace: str) -> None:
        """删除完成或失败后解除栅栏，不恢复任何已取消任务。"""
        self._deleting_workspaces.discard(workspace)


_POOL: UiDesignGenerationPool | None = None


def get_ui_design_generation_pool() -> UiDesignGenerationPool:
    """返回进程级生成池，并发度沿用当前配置。"""
    global _POOL
    if _POOL is None:
        _POOL = UiDesignGenerationPool(Settings.from_env().ui_design_concurrency)
    return _POOL
