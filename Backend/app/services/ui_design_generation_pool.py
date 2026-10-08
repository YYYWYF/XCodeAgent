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
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from app.config import Settings
from app.services.page_templates import load_template_source
from app.services.ui_design_generator import (
    UiDesignStreamCancelled,
    delete_page_code,
    generate_adjusted_page_react_code,
    generate_page_react_code,
    persist_page_code,
)
from app.services.ui_design_manifest import (
    UI_MANIFEST_SCHEMA_VERSION,
    build_ui_page_manifest,
)
from app.workspace.spec_documents import (
    load_ui_designs_json,
    ui_designs_json_path,
    write_ui_designs_json,
)

logger = logging.getLogger(__name__)

# 生成中的瞬态状态：入队后为 queued，worker 领取后为 generating，结束后为
# confirmed / generation_failed。均落盘到 ui-designs.json 供前端轮询与重启恢复。
UI_DESIGN_STATUS_QUEUED = "queued"
UI_DESIGN_STATUS_GENERATING = "generating"

# 模板适配的视觉参考指令（与旧 ui_confirmation._apply_template_to_page 一致）。
_TEMPLATE_ADAPT_INSTRUCTION = (
    "Treat the supplied template only as a visual layout and component-style reference. "
    "Replace all template business semantics with exactly the ProductPlan information "
    "items and actions. Do not preserve any template field, metric, filter, action, "
    "route, role, or label that ProductPlan does not declare."
)


@dataclass(frozen=True)
class UiDesignGenerationTask:
    """一次单页设计稿生成任务（ui_confirmation 节点入队时构造）。"""

    workspace: str  # 工作区绝对路径（workspace_root(state)）
    project_id: str  # 工作区 project_id，仅用于重建 state 落盘
    project_dir: str  # 设计稿目录（.xcodeagent/ui-design）
    page_id: str
    spec_page: dict[str, Any]  # ProductPlan 单页事实（pageId/name/actions/items）
    page_key: str
    action: str  # "regenerate" | "select_template"
    template_id: str = ""
    recovery: bool = False  # 仅恢复落盘 queued/generating；不得覆盖更新后的终态。
    attempt_id: str = field(default_factory=lambda: uuid4().hex)


class UiDesignGenerationCancelled(RuntimeError):
    """表示应用删除已经撤销当前页面设计生成，不应再写入工作区。"""


def generate_page_entry(
    task: UiDesignGenerationTask,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """同步执行单页生成（worker 在 to_thread 里跑），返回带 code/status 的清单条目。

    regenerate：删旧稿 + 调 LLM 全新生成；select_template：把模板仅作视觉参考
    重写为符合 ProductPlan 的单页设计稿。成功置 status="confirmed"（用户主动
    触发即视为该页已确认），失败置 generation_failed 并带 error。
    """

    page = task.spec_page
    if should_cancel and should_cancel():
        raise UiDesignGenerationCancelled("应用正在删除，页面设计生成已取消。")
    if task.action == "select_template":
        if not task.project_dir or not task.template_id:
            return build_ui_page_manifest(
                page,
                page_key=task.page_key,
                status="generation_failed",
                error="模板生成缺少 project_dir 或 template_id",
            )
        try:
            template_code = load_template_source(task.template_id)
            code = generate_adjusted_page_react_code(
                page,
                task.page_key,
                task.project_dir,
                template_code,
                _TEMPLATE_ADAPT_INSTRUCTION,
                should_cancel=should_cancel,
            )
            if should_cancel and should_cancel():
                raise UiDesignGenerationCancelled("应用正在删除，页面设计生成已取消。")
            code_path = persist_page_code(task.project_dir, task.page_key, code)
            return build_ui_page_manifest(
                page,
                page_key=task.page_key,
                code_path=code_path,
                code=code,
                status="confirmed",
                template_id=task.template_id,
                template_source_path=f"src/renderer/src/templates/{task.template_id}",
            )
        except UiDesignGenerationCancelled:
            raise
        except UiDesignStreamCancelled:
            raise UiDesignGenerationCancelled("用户取消了本次生成，LLM 流已中断。")
        except Exception as exc:  # noqa: BLE001 - 汇总为 generation_failed 反馈给前端
            logger.exception("ui_design_template_failed page_id=%s", task.page_id)
            return build_ui_page_manifest(
                page,
                page_key=task.page_key,
                status="generation_failed",
                template_id=task.template_id,
                error=str(exc),
            )

    try:
        if should_cancel and should_cancel():
            raise UiDesignGenerationCancelled("应用正在删除，页面设计生成已取消。")
        # 截图视觉规范存在时沿用原截图进行多模态重生成；文字模式保持原生成器。
        from app.agents.screenshot_ui_design import regenerate_screenshot_ui_page

        screenshot_entry = regenerate_screenshot_ui_page(
            workspace=task.workspace,
            page=page,
            page_key=task.page_key,
            project_dir=task.project_dir,
        )
        if should_cancel and should_cancel():
            raise UiDesignGenerationCancelled("应用正在删除，页面设计生成已取消。")
        if screenshot_entry is not None:
            return screenshot_entry
        # 文字模式才删除旧稿以强制重生成；截图模式会原子覆盖成功产物，
        # 失败时应保留上一版可预览的设计稿。
        delete_page_code(task.project_dir, task.page_key)
        code = generate_page_react_code(
            page,
            task.page_key,
            task.project_dir,
            should_cancel=should_cancel,
        )
        if should_cancel and should_cancel():
            raise UiDesignGenerationCancelled("应用正在删除，页面设计生成已取消。")
        code_path = persist_page_code(task.project_dir, task.page_key, code)
        return build_ui_page_manifest(
            page,
            page_key=task.page_key,
            code_path=code_path,
            code=code,
            status="confirmed",
        )
    except UiDesignGenerationCancelled:
        raise
    except UiDesignStreamCancelled:
        raise UiDesignGenerationCancelled("用户取消了本次生成，LLM 流已中断。")
    except Exception as exc:  # noqa: BLE001
        logger.exception("ui_design_regenerate_failed page_id=%s", task.page_id)
        return build_ui_page_manifest(
            page,
            page_key=task.page_key,
            status="generation_failed",
            error=str(exc),
        )


class UiDesignGenerationPool:
    """进程级 UI 设计稿并发 worker 池（单例，经 get_ui_design_generation_pool 获取）。"""

    def __init__(self, concurrency: int) -> None:
        self._concurrency = max(1, concurrency)
        self._queue: asyncio.Queue[UiDesignGenerationTask] = asyncio.Queue()
        # 已排队/生成中的 (workspace, page_id) 集合，用于去重与 is_active。
        self._pending_ids: set[tuple[str, str]] = set()
        self._active_ids: set[tuple[str, str]] = set()
        self._deleting_workspaces: set[str] = set()
        # 用户主动取消的 attempt_id：worker 领取时跳过，已进入模型调用的
        # 任务由 should_cancel 回调在返回后拦截写入（LLM 调用本身不可中断，取消的
        # 语义是"结果不落盘、状态置 cancelled"，而不是杀掉 HTTP 请求）。
        # 必须按生成尝试标识而非 page_id 记录：取消旧任务后立即重试时，新任务不能
        # 清除旧任务的取消信号，否则旧模型结果会重新覆盖 cancelled 状态。
        self._cancelled_task_ids: set[str] = set()
        # 在途任务副本：cancel_page 构造 cancelled manifest 条目需要 spec_page/page_key。
        self._tasks_by_id: dict[tuple[str, str], UiDesignGenerationTask] = {}
        # 每个工作区一把写锁：多个 worker 并发更新同一工作区清单时串行化写文件。
        self._locks: dict[str, asyncio.Lock] = {}
        self._started = False
        self._workers: list[asyncio.Task[Any]] = []

    def _lock_for(self, workspace: str) -> asyncio.Lock:
        lock = self._locks.get(workspace)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[workspace] = lock
        return lock

    async def _ensure_started(self) -> None:
        """确保当前事件循环拥有足量存活 worker，并恢复无人消费的排队任务。"""

        loop = asyncio.get_running_loop()
        alive_workers = [
            worker
            for worker in self._workers
            if not worker.done() and worker.get_loop() is loop
        ]
        if self._workers and not alive_workers:
            # 热重载、流任务取消或事件循环切换后，旧 Task 可能已经结束，但历史
            # `_started=True` 不能再作为 worker 存活证据。若当前循环没有 worker，
            # 依据任务登记表重建队列，避免已经写成 queued 的页面永久无人领取。
            recoverable_tasks = [
                task
                for key, task in self._tasks_by_id.items()
                if key in self._pending_ids
            ]
            self._queue = asyncio.Queue()
            self._locks = {}
            self._active_ids.clear()
            for task in recoverable_tasks:
                self._queue.put_nowait(task)
        self._workers = alive_workers
        missing_workers = self._concurrency - len(self._workers)
        for _index in range(max(0, missing_workers)):
            self._workers.append(loop.create_task(self._worker_loop()))
        self._started = bool(self._workers)

    async def ensure_started(self) -> None:
        """供轮询恢复路径显式检查 worker 存活，修复已落盘但无人消费的 queued。"""

        await self._ensure_started()

    def is_active(self, workspace: str, page_id: str) -> bool:
        """页面是否已排队或正在生成。"""

        return (workspace, page_id) in self._pending_ids

    def pending_page_ids(self, workspace: str) -> set[str]:
        """返回工作区当前排队/生成中的 page_id 集合（供重启恢复判断）。"""

        return {page_id for (ws, page_id) in self._pending_ids if ws == workspace}

    @staticmethod
    def _read_manifest(workspace: str, project_id: str) -> dict[str, Any]:
        """读取工作区最新 UI Manifest；缺失/损坏时返回空清单骨架。"""

        state = {"workspace": workspace, "project_id": project_id}
        manifest = load_ui_designs_json(ui_designs_json_path(state))
        if manifest.get("pages"):
            return manifest
        return {
            "schema_version": UI_MANIFEST_SCHEMA_VERSION,
            "confirmation_status": "pending_user_confirmation",
            "pages": [],
        }

    @staticmethod
    def _replace_page(
        manifest: dict[str, Any], page_id: str, entry: dict[str, Any]
    ) -> None:
        """按 pageId 原位替换页面条目；未找到则追加（保证页面集合不丢页）。"""

        pages = manifest.get("pages")
        if not isinstance(pages, list):
            pages = []
            manifest["pages"] = pages
        for index, page in enumerate(pages):
            if isinstance(page, dict) and str(page.get("pageId") or "") == page_id:
                pages[index] = entry
                return
        pages.append(entry)

    async def submit(self, tasks: list[UiDesignGenerationTask]) -> list[str]:
        """登记一批生成任务并立即返回被接受的 page_id（去重：已排队/生成中跳过）。"""

        await self._ensure_started()
        accepted: list[str] = []
        if not tasks:
            return accepted
        # 按工作区分组，逐工作区加锁写 queued 状态，避免与在跑的 worker 写文件冲突。
        by_workspace: dict[str, list[UiDesignGenerationTask]] = {}
        for task in tasks:
            if task.workspace in self._deleting_workspaces:
                continue
            by_workspace.setdefault(task.workspace, []).append(task)
        for workspace, ws_tasks in by_workspace.items():
            async with self._lock_for(workspace):
                manifest = self._read_manifest(workspace, ws_tasks[0].project_id)
                queued: list[UiDesignGenerationTask] = []
                for task in ws_tasks:
                    key = (workspace, task.page_id)
                    if task.recovery:
                        current_page = next(
                            (
                                page
                                for page in manifest.get("pages", [])
                                if isinstance(page, dict)
                                and str(page.get("pageId") or "") == task.page_id
                            ),
                            None,
                        )
                        current_status = str((current_page or {}).get("status") or "")
                        if current_status not in {
                            UI_DESIGN_STATUS_QUEUED,
                            UI_DESIGN_STATUS_GENERATING,
                        }:
                            # 停止动作可能在恢复任务构造后先写入 cancelled；恢复提交
                            # 必须在同一写锁内重读并尊重最新终态，不能再次改回 queued。
                            continue
                    if key in self._pending_ids:
                        continue  # 已排队/生成中：去重，避免重复生成
                    self._pending_ids.add(key)
                    self._tasks_by_id[key] = task
                    self._replace_page(
                        manifest,
                        task.page_id,
                        build_ui_page_manifest(
                            task.spec_page,
                            page_key=task.page_key,
                            status=UI_DESIGN_STATUS_QUEUED,
                            template_id=task.template_id,
                        ),
                    )
                    queued.append(task)
                    accepted.append(task.page_id)
                if queued:
                    write_ui_designs_json(
                        {"workspace": workspace, "project_id": ws_tasks[0].project_id},
                        manifest,
                    )
                    for task in queued:
                        self._queue.put_nowait(task)
        return accepted

    async def merge_external_page_entries(
        self,
        workspace: str,
        project_id: str,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """在生成池同一写锁下合并调整页，保留其它页面的最新排队与生成状态。"""

        async with self._lock_for(workspace):
            manifest = self._read_manifest(workspace, project_id)
            for entry in entries:
                page_id = str(entry.get("pageId") or "").strip()
                if page_id:
                    self._replace_page(manifest, page_id, entry)
            if entries:
                write_ui_designs_json(
                    {"workspace": workspace, "project_id": project_id}, manifest
                )
            return manifest

    async def _worker_loop(self) -> None:
        while True:
            task = await self._queue.get()
            key = (task.workspace, task.page_id)
            try:
                if task.workspace in self._deleting_workspaces:
                    continue
                # 惰性出队：任务在排队期间被 cancel_page 取消（pending 已释放、
                # 终态已写回），或同页已经提交了新 attempt 时直接跳过旧任务，
                # 不覆盖 cancelled/新任务状态。
                if (
                    key not in self._pending_ids
                    or self._tasks_by_id.get(key) is not task
                ):
                    continue
                self._active_ids.add(key)
                await self._process(task)
            except UiDesignGenerationCancelled:
                # 工作区删除或页级取消：cancel_page 已写回 cancelled 终态（页级），
                # 工作区删除由删除流程自己处理状态，这里都不覆写。
                pass
            except Exception as exc:  # noqa: BLE001 - worker 兜底，避免整池崩溃
                logger.exception("ui_design_pool_worker_crashed page_id=%s", task.page_id)
                await self._write_result(
                    task,
                    build_ui_page_manifest(
                        task.spec_page,
                        page_key=task.page_key,
                        status="generation_failed",
                        error=str(exc),
                    ),
                )
            finally:
                self._active_ids.discard(key)
                # 取消后允许同页立即重试；旧 worker 收尾时不能误删新 attempt 的
                # pending/task 登记，否则新任务会被当成无人处理并反复自动恢复。
                if self._tasks_by_id.get(key) is task:
                    self._pending_ids.discard(key)
                    self._tasks_by_id.pop(key, None)
                self._cancelled_task_ids.discard(task.attempt_id)
                self._queue.task_done()

    async def _process(self, task: UiDesignGenerationTask) -> None:
        key = (task.workspace, task.page_id)

        def should_cancel() -> bool:
            return (
                task.workspace in self._deleting_workspaces
                or task.attempt_id in self._cancelled_task_ids
            )

        # 检查取消和写入 generating 必须在同一把工作区锁内完成：若停止动作先写入
        # cancelled，本 worker 不得随后把终态覆盖回 generating；若本 worker 先写，
        # 停止动作会等待同一把锁并最后写入 cancelled。
        async with self._lock_for(task.workspace):
            if (
                should_cancel()
                or key not in self._pending_ids
                or self._tasks_by_id.get(key) is not task
            ):
                raise UiDesignGenerationCancelled("页面设计生成已取消。")
            manifest = self._read_manifest(task.workspace, task.project_id)
            self._replace_page(
                manifest,
                task.page_id,
                build_ui_page_manifest(
                    task.spec_page,
                    page_key=task.page_key,
                    status=UI_DESIGN_STATUS_GENERATING,
                    template_id=task.template_id,
                ),
            )
            write_ui_designs_json(
                {"workspace": task.workspace, "project_id": task.project_id},
                manifest,
            )
        entry = await asyncio.to_thread(
            generate_page_entry,
            task,
            should_cancel=should_cancel,
        )
        if should_cancel():
            raise UiDesignGenerationCancelled("页面设计生成已取消。")
        await self._write_result(task, entry)

    async def cancel_workspace(self, workspace: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        """封锁工作区后续设计任务，并等待已经进入模型调用的任务停止写入。"""

        self._deleting_workspaces.add(workspace)
        queued_ids = {
            page_id
            for pending_workspace, page_id in self._pending_ids
            if pending_workspace == workspace
            and (pending_workspace, page_id) not in self._active_ids
        }
        for page_id in queued_ids:
            self._pending_ids.discard((workspace, page_id))

        async def wait_until_idle() -> None:
            """等待该工作区已进入同步生成函数的任务完成取消检查。"""

            while any(active_workspace == workspace for active_workspace, _page_id in self._active_ids):
                await asyncio.sleep(0.05)

        try:
            await asyncio.wait_for(wait_until_idle(), timeout=max(timeout_seconds, 0.1))
        except TimeoutError:
            remaining = sorted(
                page_id
                for active_workspace, page_id in self._active_ids
                if active_workspace == workspace
            )
            return {
                "cancelledQueuedCount": len(queued_ids),
                "remainingPageIds": remaining,
            }
        self._locks.pop(workspace, None)
        return {
            "cancelledQueuedCount": len(queued_ids),
            "remainingPageIds": [],
        }

    def end_workspace_deletion(self, workspace: str) -> None:
        """仅解除目标工作区的删除栅栏，不恢复已经取消的页面任务。"""

        self._deleting_workspaces.discard(workspace)

    async def cancel_page(self, workspace: str, page_id: str) -> bool:
        """用户主动取消单页生成。返回是否确有在途任务被取消。

        - 还在队列里（queued）：登记取消并立即写回 cancelled 终态、释放 pending，
          worker 领取时发现不在 pending 直接跳过（惰性出队，不重建队列）。
        - 已进入模型调用（generating）：登记 attempt_id 取消信号，LLM 调用返回
          后由 should_cancel 抛 UiDesignGenerationCancelled 拦截落盘。
        - 后端重启后内存任务已丢失、但磁盘仍为 queued/generating：直接把磁盘条目
          写成 cancelled，阻止 ui_confirmation 的自愈恢复再次自动入队。
        LLM HTTP 请求本身不打断（打断会在网关侧留下半截流式计费），取消语义是
        "结果丢弃、状态立即置终态、前端立即可重试"。queued/generating 两种情况
        都在这里立即写回 cancelled 终态：前端点停止要立刻看到可重试态，不必等
        worker 走到取消分支。worker 随后写回的同值终态与本次写入幂等（写锁串行）。
        """

        key = (workspace, page_id)
        task = self._tasks_by_id.get(key)
        if key in self._pending_ids and task is not None:
            self._cancelled_task_ids.add(task.attempt_id)
            await self._write_result(
                task,
                build_ui_page_manifest(
                    task.spec_page,
                    page_key=task.page_key,
                    status="cancelled",
                    error="用户取消了本次生成。",
                ),
            )
            self._pending_ids.discard(key)
            return True

        # 进程重启会清空内存池，但 queued/generating 已持久化。停止操作必须仍能
        # 修改磁盘权威状态，否则前端刚显示 cancelled 就会被下一次轮询改回 queued，
        # 随后 _latest_ui_designs 又会把它自动恢复为生成任务。
        async with self._lock_for(workspace):
            manifest = self._read_manifest(workspace, "")
            current_page = next(
                (
                    page
                    for page in manifest.get("pages", [])
                    if isinstance(page, dict)
                    and str(page.get("pageId") or "") == page_id
                ),
                None,
            )
            current_status = str((current_page or {}).get("status") or "")
            if current_status not in {
                UI_DESIGN_STATUS_QUEUED,
                UI_DESIGN_STATUS_GENERATING,
            }:
                return False
            cancelled_entry = dict(current_page)
            cancelled_entry["status"] = "cancelled"
            cancelled_entry["error"] = "用户取消了本次生成。"
            self._replace_page(manifest, page_id, cancelled_entry)
            write_ui_designs_json(
                {"workspace": workspace, "project_id": ""},
                manifest,
            )
        self._pending_ids.discard(key)
        return True

    async def _write_result(
        self, task: UiDesignGenerationTask, entry: dict[str, Any]
    ) -> None:
        """在写锁内做读-改-写，把单页结果合入 ui-designs.json。"""

        async with self._lock_for(task.workspace):
            manifest = self._read_manifest(task.workspace, task.project_id)
            self._replace_page(manifest, task.page_id, entry)
            write_ui_designs_json(
                {"workspace": task.workspace, "project_id": task.project_id},
                manifest,
            )


_POOL: UiDesignGenerationPool | None = None


def get_ui_design_generation_pool() -> UiDesignGenerationPool:
    """返回进程级 UI 设计稿生成池单例，并发度取 Settings.ui_design_concurrency。"""

    global _POOL
    if _POOL is None:
        settings = Settings.from_env()
        _POOL = UiDesignGenerationPool(settings.ui_design_concurrency)
    return _POOL
