"""在既有 AG-UI 设计动作内局部调整共享工程，旧草稿始终保持只读。"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable
import uuid

from app.services.agent_ui_design_generator import _check_cancel
from app.services.agent_ui_design_revision import revise_agent_ui_design
from app.services.agent_ui_design_mock import AgentUiDesignMock
from app.services.agent_ui_design_preview import agent_ui_design_previews
from app.services.agent_ui_design_project import validate_design_project
from app.services.agent_ui_design_workflow import design_environment
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_interactions import agent_project_page_bindings
from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path, workspace_root, write_ui_designs_json

logger = logging.getLogger(__name__)
_PROGRESS_INTERVAL = 5.0


def _adjust_project(state: dict[str, Any], pages: list[dict[str, Any]], targets: list[str],
                    instruction: str, cancelled: threading.Event,
                    on_progress: Callable[[str, str], None] | None = None) -> list[dict[str, Any]]:
    """校验旧资产并在新草稿中编辑、编译，成功后才切换预览和共享引用。"""
    from app.services.agent_ui_design_projection import current_project

    current = [page for page in pages if page.get("design_asset")]
    project, asset = current_project(state, next(page for page in current if page["pageId"] in targets))
    if any(page["design_asset"] != asset for page in current):
        raise ValueError("当前智能体页面的工程身份不一致，请重新生成全部智能体页面。")
    validate_design_project(project)
    _check_cancel(cancelled.is_set)
    run_id = f"agent-ui-adjust-{uuid.uuid4().hex}"
    inspection, cli = design_environment(str(project.workspace), run_id)
    draft = project.workspace / ".devagentstudio/ui-design/drafts" / uuid.uuid4().hex
    design = revise_agent_ui_design(inspection, current_project=project, draft_root=draft, pnpm_cli=cli,
        requirement_spec=state["requirement_spec"], product_plan=state["product_plan"], run_id=run_id,
        should_cancel=cancelled.is_set, targets=targets, instruction=instruction, on_progress=on_progress)
    _check_cancel(cancelled.is_set)
    # 模型执行期间旧源码、正式事实或清单变化时拒绝提交，不能覆盖其他已接受的工作。
    validate_design_project(project)
    latest = load_ui_designs_json(ui_designs_json_path(state))
    if latest and latest.get("pages") != pages:
        raise ValueError("设计调整期间页面状态已变化，已保留当前设计，请基于最新版本重新提交。")
    updated_asset = {**asset, "draft": draft.name, "revision": design.revision,
        "factsDigest": design.facts_digest, "environmentIdentity": design.project.identity,
        "sourceDigest": design.project.source_digest, "buildDigest": design.project.build_digest}
    # 共享应用变化撤销全部受影响页确认；仅页面展示 props 变化保留其他页的确认。
    affected = set(design.affected_page_ids)
    product_pages = {page["pageId"]: page for page in project_agent_design_pages(
        state["requirement_spec"], state["product_plan"])}
    result: list[dict[str, Any]] = []
    for page in pages:
        if not page.get("design_asset"):
            result.append(page)
            continue
        entry = {**page, "design_asset": updated_asset}
        if page["pageId"] in affected:
            entry["bindings"] = agent_project_page_bindings(
                design.project.project_dir, product_pages[page["pageId"]], updated_asset["pageIds"])
            entry.pop("asset_confirmation", None)
            entry.update(status="ready", error="", verification={"status": "pending_user_confirmation",
                "checks": [{"id": name, "status": "passed"} for name in ("lint", "typecheck", "build")], "errors": []})
        result.append(entry)
    if on_progress:
        on_progress('正在启动设计预览', targets[0])
    agent_ui_design_previews.start(design.project, node_executable=inspection.node_executable,
        run_id=run_id, mock=AgentUiDesignMock(inspection, targets[0], design.revision),
        check_cancel=lambda: _check_cancel(cancelled.is_set))
    # 预览切换后立即保存真实结果，避免 Graph 在返回边界取消时只留下新进程而丢失新资产。
    write_ui_designs_json(state, {**latest, "confirmation_status": "pending_user_confirmation", "pages": result})
    return result


async def adjust_agent_pages(state: dict[str, Any], pages: list[dict[str, Any]], targets: list[str],
                             instruction: str, *,
                             on_progress: Callable[[str, str], None] | None = None) -> list[dict[str, Any]]:
    """与后台重生成共享锁；取消时等待编辑线程停止，失败仅标记目标页并保留旧稿。"""
    cancelled = threading.Event()
    pool = get_ui_design_generation_pool()
    async with pool.agent_project_lock(str(workspace_root(state))):
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        current = ('正在准备设计草稿', targets[0])
        reporting = True

        def report(message: str, page_id: str) -> None:
            """只在原事件循环推送当前阶段和实际耗时，终态后忽略迟到通知。"""
            nonlocal current
            if not reporting:
                return
            current = (message, page_id)
            if on_progress:
                on_progress(f'{message}（已用时 {int(time.monotonic() - started)} 秒）', page_id)

        def report_from_worker(message: str, page_id: str) -> None:
            """将工作线程的阶段通知交回工作流上下文，不从线程直接写 AG-UI。"""
            loop.call_soon_threadsafe(report, message, page_id)

        report(*current)
        worker = asyncio.create_task(asyncio.to_thread(_adjust_project, state, pages, targets,
                                                      instruction, cancelled, report_from_worker))
        try:
            # 仅更新等待文案，不给 Creator 增加总时限或重复提交任务。
            while not worker.done():
                done, _ = await asyncio.wait({worker}, timeout=_PROGRESS_INTERVAL)
                if not done:
                    report(*current)
            return worker.result()
        except asyncio.CancelledError:
            reporting = False
            cancelled.set()
            await asyncio.gather(worker, return_exceptions=True)
            raise
        except Exception as exc:
            logger.exception("agent_ui_design_adjust_failed targets=%s", targets)
            latest = load_ui_designs_json(ui_designs_json_path(state)) or {}
            originals = {page["pageId"]: page.get("design_asset") for page in pages}
            return [{**page, "status": "generation_failed", "error": str(exc)}
                    if page.get("pageId") in targets and page.get("design_asset") == originals.get(page.get("pageId"))
                    else page for page in latest.get("pages", pages)]
        finally:
            reporting = False
