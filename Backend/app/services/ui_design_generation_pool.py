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
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.services.ui_design_generator import (
    delete_page_code,
    generate_adjusted_page_react_code,
    generate_page_react_code,
    load_template_source,
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


def generate_page_entry(task: UiDesignGenerationTask) -> dict[str, Any]:
    """同步执行单页生成（worker 在 to_thread 里跑），返回带 code/status 的清单条目。

    regenerate：删旧稿 + 调 LLM 全新生成；select_template：把模板仅作视觉参考
    重写为符合 ProductPlan 的单页设计稿。成功置 status="confirmed"（用户主动
    触发即视为该页已确认），失败置 generation_failed 并带 error。
    """

    page = task.spec_page
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
            )
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
        except Exception as exc:  # noqa: BLE001 - 汇总为 generation_failed 反馈给前端
            logger.exception("ui_design_template_failed page_id=%s", task.page_id)
            return build_ui_page_manifest(
                page,
                page_key=task.page_key,
                status="generation_failed",
                template_id=task.template_id,
                error=str(exc),
            )

    # regenerate：删旧稿，绕过 load_page_code 复用，强制重新调 LLM。
    delete_page_code(task.project_dir, task.page_key)
    try:
        code = generate_page_react_code(page, task.page_key, task.project_dir)
        code_path = persist_page_code(task.project_dir, task.page_key, code)
        return build_ui_page_manifest(
            page,
            page_key=task.page_key,
            code_path=code_path,
            code=code,
            status="confirmed",
        )
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
        if self._started:
            return
        self._started = True
        loop = asyncio.get_running_loop()
        for _index in range(self._concurrency):
            self._workers.append(loop.create_task(self._worker_loop()))

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
            by_workspace.setdefault(task.workspace, []).append(task)
        for workspace, ws_tasks in by_workspace.items():
            async with self._lock_for(workspace):
                manifest = self._read_manifest(workspace, ws_tasks[0].project_id)
                queued: list[UiDesignGenerationTask] = []
                for task in ws_tasks:
                    key = (workspace, task.page_id)
                    if key in self._pending_ids:
                        continue  # 已排队/生成中：去重，避免重复生成
                    self._pending_ids.add(key)
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

    async def _worker_loop(self) -> None:
        while True:
            task = await self._queue.get()
            try:
                await self._process(task)
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
                self._pending_ids.discard((task.workspace, task.page_id))
                self._queue.task_done()

    async def _process(self, task: UiDesignGenerationTask) -> None:
        # 领取即标记 generating，让前端轮询看到「生成中」。
        await self._write_result(
            task,
            build_ui_page_manifest(
                task.spec_page,
                page_key=task.page_key,
                status=UI_DESIGN_STATUS_GENERATING,
                template_id=task.template_id,
            ),
        )
        entry = await asyncio.to_thread(generate_page_entry, task)
        await self._write_result(task, entry)

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
