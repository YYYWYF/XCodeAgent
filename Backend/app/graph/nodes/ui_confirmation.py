"""UI确认节点：为需求 spec 中每个页面生成 React 设计稿代码并等待用户逐页确认。

节点位于 requirements 与 project_planning 之间。首次进入时先从 GitHub 模板
仓库 clone 设计稿工程到工作区 .xcodeagent/ui-design/ 并启动 dev server，再为
每个页面调用 antd-ui-design 技能生成真实视觉的 React + antd 设计稿 .tsx，写入
设计稿工程的 src/pages/<PageKey>/index.tsx 并注册到 BIZ_MENUS 菜单；随后返回
ui_design_confirmation 待确认交互，用户逐页确认全部通过后才放行进入项目规划。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

from app.graph.nodes.confirmation import (
    extract_confirmation_answer,
    user_confirmed_text,
)
from app.graph.state import ProjectState
from app.services.ui_design_generator import (
    derive_page_key,
    generate_page_react_code,
    load_page_code,
    menu_path_for_page,
    persist_page_code,
    rewrite_menus,
    route_path_for_page,
    validate_tsx,
)
from app.services.ui_design_project_setup import setup_ui_design_project
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.spec_documents import workspace_root


logger = logging.getLogger(__name__)

# 并发生成上限：避免模型服务或 cloudflare 隧道并发限流。
_UI_DESIGN_CONCURRENCY = 3


def _page_list(state: ProjectState) -> list[dict[str, Any]]:
    """从需求 spec 读取扁平页面清单，缺失时返回空列表。"""

    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        return []
    pages = requirement_spec.get("pages")
    return [page for page in pages if isinstance(page, dict)] if isinstance(pages, list) else []


def _page_id(page: dict[str, Any]) -> str:
    return str(page.get("pageId") or page.get("id") or "").strip()


def _ui_design_confirmation_payload(ui_designs: dict[str, Any]) -> dict[str, Any]:
    """构造 UI确认待确认交互载荷，附带页面设计稿摘要供前端渲染。"""

    pages = ui_designs.get("pages") if isinstance(ui_designs, dict) else None
    pending_count = sum(
        1
        for page in (pages or [])
        if isinstance(page, dict) and page.get("status") != "confirmed"
    )
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="UI确认",
                question=(
                    "请逐页审核已生成的设计稿并确认。"
                    "全部页面确认后，回复“确认全部设计稿”继续项目规划；"
                    "如需调整某页，可先在下方操作后再确认。"
                ),
                type="text",
                placeholder="例如：确认全部设计稿 / 第2页需要改成表格布局。",
            )
        ]
    )
    payload["mode"] = "ui_design_confirmation"
    payload["message"] = "请逐页确认设计稿后再继续项目规划。"
    payload["pending_count"] = pending_count
    payload["pages"] = list(pages or [])
    return payload


def _ui_design_confirmed_payload(ui_designs: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "ui_design_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "所有页面设计稿已确认，可以继续项目规划。",
        "pages": ui_designs.get("pages", []) if isinstance(ui_designs, dict) else [],
    }


def _user_confirmed_all_designs(request: str) -> bool:
    """判断用户本轮是否明确确认全部设计稿。"""

    return user_confirmed_text(
        request,
        positive_signals=(
            "确认全部设计稿",
            "确认所有设计稿",
            "设计稿确认",
            "全部确认",
            "确认设计稿",
        ),
        negative_signals=(
            "修改",
            "调整",
            "换一换",
            "重新生成",
            "不是",
            "需要改",
        ),
    )


def _has_explicit_user_submission(state: ProjectState) -> bool:
    """与 requirements 节点一致：创建规划须收到本轮结构化交互提交。"""

    return (
        state.get("workflow_scope") != "application_planning"
        or state.get("user_interaction_submission") is True
    )


def _emit_progress(message: str, **detail: object) -> None:
    """向 LangGraph custom stream 推送 UI确认进度，供前端流式展示。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer(
        {
            "type": "ui_confirmation.progress",
            "node_name": "ui_confirmation",
            "message": message,
            "detail": detail,
        }
    )


async def _generate_one_page(
    page: dict[str, Any],
    project_dir: str,
    used_keys: set[str],
    semaphore: asyncio.Semaphore,
    ready_pages: list[dict[str, Any]],
    total: int,
) -> dict[str, Any]:
    """生成单个页面的 React 设计稿并落盘，完成后追加到共享列表并推送流式进度。"""

    page_id = _page_id(page)
    page_key = derive_page_key(page, used_keys)
    menu_path = menu_path_for_page(page, page_key)
    route_path = route_path_for_page(menu_path)
    entry = {
        "pageId": page_id,
        "name": str(page.get("name") or page_id),
        "path": str(page.get("path") or "/"),
        "description": str(page.get("description") or ""),
        "page_key": page_key,
        "menu_path": menu_path,
        "route_path": route_path,
        "code_path": "",
        "status": "pending",
    }
    if not page_id or not project_dir:
        return entry
    # 已存在落盘设计稿直接复用，避免恢复时重复调用 LLM。
    existing = load_page_code(project_dir, page_key)
    if existing:
        entry["code_path"] = str(
            Path(project_dir) / "src" / "pages" / page_key / "index.tsx"
        )
        ready_pages.append(entry)
        _emit_progress(
            f"已加载现有设计稿：{entry['name']}（{len(ready_pages)}/{total}）",
            pageId=page_id,
            ready=len(ready_pages),
            total=total,
            pages=list(ready_pages),
        )
        return entry
    async with semaphore:
        _emit_progress(
            f"正在生成设计稿：{entry['name']}",
            pageId=page_id,
            ready=len(ready_pages),
            total=total,
            pages=list(ready_pages),
        )
        try:
            code = await asyncio.to_thread(generate_page_react_code, page, page_key)
            ok, err = await asyncio.to_thread(validate_tsx, project_dir, code)
            if not ok:
                logger.warning(
                    "ui_design_validate_failed page_id=%s err=%s", page_id, err[:200]
                )
                entry["status"] = "generation_failed"
                entry["error"] = err[:500]
                ready_pages.append(entry)
                _emit_progress(
                    f"设计稿语法校验失败：{entry['name']}（{len(ready_pages)}/{total}）",
                    pageId=page_id,
                    ready=len(ready_pages),
                    total=total,
                    pages=list(ready_pages),
                )
                return entry
            entry["code_path"] = persist_page_code(project_dir, page_key, code)
            ready_pages.append(entry)
            _emit_progress(
                f"设计稿已生成：{entry['name']}（{len(ready_pages)}/{total}）",
                pageId=page_id,
                ready=len(ready_pages),
                total=total,
                pages=list(ready_pages),
            )
        except Exception:
            logger.exception("ui_design_generation_failed page_id=%s", page_id)
            entry["status"] = "generation_failed"
            ready_pages.append(entry)
            _emit_progress(
                f"设计稿生成失败：{entry['name']}（{len(ready_pages)}/{total}）",
                pageId=page_id,
                ready=len(ready_pages),
                total=total,
                pages=list(ready_pages),
            )
    return entry


async def _build_initial_ui_designs(state: ProjectState) -> dict[str, Any]:
    """准备设计稿工程并并发为每个页面生成 React 设计稿，返回初始 ui_designs 状态。

    先 clone/更新 GitHub 模板并启动 dev server（流式推送准备进度），再并发
    生成各页设计稿。每生成完一页即通过 LangGraph stream 推送带当前已生成页面
    的进度，前端可流式展示。全部生成完成后重写 menus.ts 注册所有页面菜单。
    """

    workspace = str(workspace_root(state))
    pages = _page_list(state)
    valid_pages = [page for page in pages if _page_id(page)]
    total = len(valid_pages)

    # 准备设计稿工程：clone 模板 + 安装依赖 + 启动 dev server。
    _emit_progress("正在准备设计稿工程（拉取模板并启动预览服务）", ready=0, total=total, pages=[])
    setup = await asyncio.to_thread(setup_ui_design_project, workspace)
    project_dir = str(setup.get("project_dir") or "")
    preview_origin = str(setup.get("preview_origin") or "")
    if setup.get("status") != "ready":
        _emit_progress(
            f"设计稿预览服务未就绪：{setup.get('message')}（代码仍会生成）",
            ready=0, total=total, pages=[],
        )
    else:
        _emit_progress(
            f"设计稿工程已就绪，预览地址：{preview_origin}",
            ready=0, total=total, pages=[],
        )

    _emit_progress(f"开始为 {total} 个页面生成设计稿", ready=0, total=total, pages=[])
    semaphore = asyncio.Semaphore(_UI_DESIGN_CONCURRENCY)
    used_keys: set[str] = {"DefaultPage"}
    # 共享已就绪页面列表：每个 task 完成后追加并推送，供前端流式渲染。
    ready_pages: list[dict[str, Any]] = []
    tasks = [
        _generate_one_page(page, project_dir, used_keys, semaphore, ready_pages, total)
        for page in valid_pages
    ]
    page_entries = await asyncio.gather(*tasks)
    # 全部页面生成完成后，重写设计稿工程菜单注册所有页面。
    try:
        rewrite_menus(project_dir, page_entries)
    except Exception:
        logger.exception("ui_design_menus_rewrite_failed project=%s", project_dir)
    _emit_progress(
        "全部页面设计稿已生成，等待逐页确认",
        ready=total,
        total=total,
        pages=list(page_entries),
    )
    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": page_entries,
        "preview_origin": preview_origin,
    }


async def ui_confirmation(state: ProjectState) -> dict:
    """生成各页 React 设计稿并等待用户逐页确认。"""

    existing = state.get("ui_designs")
    request = state.get("request", "")

    # 恢复路径：已有待确认设计稿且本轮无新提交，直接重放确认卡。
    if (
        isinstance(existing, dict)
        and existing.get("confirmation_status") == "pending_user_confirmation"
        and not _has_explicit_user_submission(state)
    ):
        return {
            "phase": "ui_confirmation",
            "status": "requires_user_input",
            "ui_designs": existing,
            "clarification": _ui_design_confirmation_payload(existing),
            "timeline": ["ui_confirmation"],
        }

    # 用户确认路径：检测到全部确认信号且无页面仍待确认。
    if (
        isinstance(existing, dict)
        and existing.get("confirmation_status") == "pending_user_confirmation"
        and _user_confirmed_all_designs(request)
    ):
        confirmed_pages = [
            {**page, "status": "confirmed"}
            for page in existing.get("pages", [])
            if isinstance(page, dict)
        ]
        ui_designs = {
            "confirmation_status": "confirmed",
            "pages": confirmed_pages,
        }
        return {
            "phase": "ui_confirmation",
            "status": "completed",
            "ui_designs": ui_designs,
            "clarification": _ui_design_confirmed_payload(ui_designs),
            "timeline": ["ui_confirmation"],
        }

    # 首次进入或重新生成：并发为每个页面生成 React 设计稿。
    ui_designs = await _build_initial_ui_designs(state)
    return {
        "phase": "ui_confirmation",
        "status": "requires_user_input",
        "ui_designs": ui_designs,
        "clarification": _ui_design_confirmation_payload(ui_designs),
        "timeline": ["ui_confirmation"],
    }
