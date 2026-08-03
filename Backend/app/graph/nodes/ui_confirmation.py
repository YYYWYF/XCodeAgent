"""UI确认节点：为需求 spec 中每个页面生成线框图设计稿并等待用户逐页确认。

节点位于 requirements 与 project_planning 之间。首次进入时为每个页面调用
wireframe-redline-generate 技能生成低保真线框图 HTML 并落盘；随后返回
ui_design_confirmation 待确认交互，用户逐页确认全部通过后才放行进入项目规划。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.config import get_stream_writer

from app.graph.nodes.confirmation import (
    extract_confirmation_answer,
    user_confirmed_text,
)
from app.graph.state import ProjectState
from app.services.ui_design_generator import (
    generate_page_wireframe,
    load_wireframe,
    persist_wireframe,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload


logger = logging.getLogger(__name__)

# 并发生成上限：避免模型服务或 cloudflare 隧道并发限流。
_WIREFRAME_CONCURRENCY = 3


def _page_list(state: ProjectState) -> list[dict[str, Any]]:
    """从需求 spec 读取扁平页面清单，缺失时返回空列表。"""

    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        return []
    pages = requirement_spec.get("pages")
    return [page for page in pages if isinstance(page, dict)] if isinstance(pages, list) else []


def _page_id(page: dict[str, Any]) -> str:
    return str(page.get("pageId") or page.get("id") or "").strip()


def _ui_design_confirmation_payload(ui_designs: dict[str, Any], workspace: str = "") -> dict[str, Any]:
    """构造 UI确认待确认交互载荷，附带页面设计稿摘要供前端渲染。"""

    pages = ui_designs.get("pages") if isinstance(ui_designs, dict) else None
    pending_count = sum(
        1
        for page in (pages or [])
        if isinstance(page, dict) and page.get("status") != "confirmed"
    )
    # 内联每页 HTML 内容供前端 iframe srcDoc 渲染；workspace 缺失时仅返回路径。
    rendered_pages: list[dict[str, Any]] = []
    for page in (pages or []):
        if not isinstance(page, dict):
            continue
        entry = {**page}
        if workspace and page.get("html_path"):
            page_id = str(page.get("pageId") or "")
            html = load_wireframe(workspace, page_id)
            if html:
                entry["html"] = html
        rendered_pages.append(entry)
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="UI确认",
                question=(
                    "请逐页审核已生成的线框图设计稿并确认。"
                    "全部页面确认后，回复“确认全部设计稿”继续项目规划；"
                    "如需调整某页，可先在下方操作（换一换/对话调整）后再确认。"
                ),
                type="text",
                placeholder="例如：确认全部设计稿 / 第2页需要改成表格布局。",
            )
        ]
    )
    payload["mode"] = "ui_design_confirmation"
    payload["message"] = "请逐页确认线框图设计稿后再继续项目规划。"
    payload["pending_count"] = pending_count
    payload["pages"] = rendered_pages
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


async def _generate_one_wireframe(
    page: dict[str, Any],
    workspace: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """生成单个页面的线框图并落盘，返回该页的 ui_designs 条目。"""

    page_id = _page_id(page)
    entry = {
        "pageId": page_id,
        "name": str(page.get("name") or page_id),
        "path": str(page.get("path") or "/"),
        "description": str(page.get("description") or ""),
        "html_path": "",
        "status": "pending",
    }
    if not page_id or not workspace:
        return entry
    # 已存在落盘设计稿直接复用，避免恢复时重复调用 LLM。
    existing = load_wireframe(workspace, page_id)
    if existing:
        entry["html_path"] = persist_wireframe(workspace, page_id, existing)
        _emit_progress(f"已加载现有设计稿：{entry['name']}", pageId=page_id)
        return entry
    async with semaphore:
        _emit_progress(f"正在生成设计稿：{entry['name']}", pageId=page_id)
        try:
            html = await asyncio.to_thread(generate_page_wireframe, page)
            entry["html_path"] = persist_wireframe(workspace, page_id, html)
            _emit_progress(f"设计稿已生成：{entry['name']}", pageId=page_id)
        except Exception:
            logger.exception("ui_wireframe_generation_failed page_id=%s", page_id)
            _emit_progress(f"设计稿生成失败：{entry['name']}", pageId=page_id)
    return entry


async def _build_initial_ui_designs(state: ProjectState) -> dict[str, Any]:
    """并发为每个页面生成线框图并落盘，返回初始 ui_designs 状态。

    用有限并发生成（默认 3 路），避免模型服务或隧道并发限流；每生成完一页
    即通过 LangGraph stream 推送进度，前端可流式展示已就绪的设计稿。
    """

    workspace = str(state.get("workspace") or "").strip()
    pages = _page_list(state)
    valid_pages = [page for page in pages if _page_id(page)]
    total = len(valid_pages)
    _emit_progress(f"开始为 {total} 个页面生成线框图设计稿", total=total)
    semaphore = asyncio.Semaphore(_WIREFRAME_CONCURRENCY)
    tasks = [
        _generate_one_wireframe(page, workspace, semaphore) for page in valid_pages
    ]
    page_entries = await asyncio.gather(*tasks)
    _emit_progress("全部页面设计稿已生成，等待逐页确认", total=total)
    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": page_entries,
    }
    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": page_entries,
    }


async def ui_confirmation(state: ProjectState) -> dict:
    """生成各页线框图设计稿并等待用户逐页确认。"""

    existing = state.get("ui_designs")
    request = state.get("request", "")
    workspace = str(state.get("workspace") or "").strip()

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
            "clarification": _ui_design_confirmation_payload(existing, workspace),
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

    # 首次进入或重新生成：并发为每个页面生成线框图设计稿。
    ui_designs = await _build_initial_ui_designs(state)
    return {
        "phase": "ui_confirmation",
        "status": "requires_user_input",
        "ui_designs": ui_designs,
        "clarification": _ui_design_confirmation_payload(ui_designs, workspace),
        "timeline": ["ui_confirmation"],
    }
