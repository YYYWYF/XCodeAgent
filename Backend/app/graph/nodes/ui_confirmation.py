"""UI确认节点：为需求 spec 中每个页面生成 React 设计稿代码并等待用户逐页确认。

节点位于 requirements 与 project_planning 之间。首次进入时准备设计稿落盘目录
（工作区 .xcodeagent/ui-design/），再为每个页面调用 antd-ui-design 技能生成
真实视觉的 React + antd5 设计稿 .tsx，写入 .xcodeagent/ui-design/pages/<PageKey>/
index.tsx，并把源码内联到 ui_designs.pages[].code 供前端 DesignRenderer 编译渲染；
随后返回 ui_design_confirmation 待确认交互，用户逐页确认全部通过后才放行进入
项目规划。

方案 B：不再 clone 模板工程、不再启动 dev server、不再注册 BIZ_MENUS——渲染由
前端 DesignRenderer（同源 iframe + 预打包 antd5 runtime + sucrase 编译）完成。
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
    delete_page_code,
    derive_page_key,
    generate_adjusted_page_react_code,
    generate_page_react_code,
    load_page_code,
    load_template_source,
    menu_path_for_page,
    pages_dir,
    persist_page_code,
    resolve_adjust_target_pages,
    route_path_for_page,
)
from app.services.ui_design_project_setup import setup_ui_design_project
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.spec_documents import workspace_root, write_ui_designs_json


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
        # 内联 .tsx 源码：方案 B 下前端 DesignRenderer 直接消费此字段编译渲染，
        # 不再依赖独立 dev server。code_path 仍保留给后续还原链路。
        "code": "",
        "status": "pending",
    }
    if not page_id or not project_dir:
        return entry
    # 已存在落盘设计稿直接复用，避免恢复时重复调用 LLM。
    existing = load_page_code(project_dir, page_key)
    if existing:
        entry["code_path"] = str(pages_dir(project_dir) / page_key / "index.tsx")
        entry["code"] = existing
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
            # generate_page_react_code 内部已做完整校验（非空 + 未定义引用 +
            # esbuild 语法）并自动修复重试，返回的代码保证可渲染；校验全部
            # 失败时抛 ValueError，由下方 except 捕获标记 generation_failed。
            code = await asyncio.to_thread(
                generate_page_react_code, page, page_key, project_dir
            )
            entry["code_path"] = persist_page_code(project_dir, page_key, code)
            entry["code"] = code
            ready_pages.append(entry)
            _emit_progress(
                f"设计稿已生成：{entry['name']}（{len(ready_pages)}/{total}）",
                pageId=page_id,
                ready=len(ready_pages),
                total=total,
                pages=list(ready_pages),
            )
        except Exception as exc:
            logger.exception("ui_design_generation_failed page_id=%s", page_id)
            entry["status"] = "generation_failed"
            entry["error"] = str(exc)[:500]
            ready_pages.append(entry)
            _emit_progress(
                f"设计稿生成失败：{entry['name']}（{len(ready_pages)}/{total}）",
                pageId=page_id,
                ready=len(ready_pages),
                total=total,
                pages=list(ready_pages),
            )
    return entry


async def _build_pending_ui_designs(state: ProjectState) -> dict[str, Any]:
    """首次进入 UI 确认节点：只为每个页面准备骨架条目，不生成设计稿。

    每页带 page_key/menu_path/route_path 但 code 为空、status=pending。用户在前端
    逐页"选模板"（套用模板代码）或"换一换"（调 LLM 生成）后才产生设计稿——
    通过 ui_design_action 单页动作路径即时回填。确认全部后放行项目规划。
    """

    workspace = str(workspace_root(state))
    pages = _page_list(state)
    valid_pages = [page for page in pages if _page_id(page)]
    total = len(valid_pages)

    # 准备设计稿落盘目录（方案 B：仅 mkdir，无 clone/install/launch）。
    setup = await asyncio.to_thread(setup_ui_design_project, workspace)
    project_dir = str(setup.get("project_dir") or "")
    used_keys: set[str] = {"DefaultPage"}
    entries: list[dict[str, Any]] = []
    for page in valid_pages:
        page_id = _page_id(page)
        page_key = derive_page_key(page, used_keys)
        menu_path = menu_path_for_page(page, page_key)
        route_path = route_path_for_page(menu_path)
        entries.append(
            {
                "pageId": page_id,
                "name": str(page.get("name") or page_id),
                "path": str(page.get("path") or "/"),
                "description": str(page.get("description") or ""),
                "page_key": page_key,
                "menu_path": menu_path,
                "route_path": route_path,
                "code_path": "",
                "code": "",
                "status": "pending",
            }
        )
    _emit_progress(
        f"已准备 {total} 个页面，请逐页选择模板或换一换生成设计稿",
        ready=0,
        total=total,
        pages=list(entries),
    )
    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": entries,
    }


async def _build_initial_ui_designs(state: ProjectState) -> dict[str, Any]:
    """准备设计稿目录并并发为每个页面生成 React 设计稿，返回初始 ui_designs 状态。

    方案 B：不再 clone 模板工程、不再启动 dev server。setup_ui_design_project
    只确保 .xcodeagent/ui-design 目录存在。每生成完一页即通过 LangGraph stream
    推送带当前已生成页面（含内联 code 源码）的进度，前端 DesignRenderer 流式渲染。
    """

    workspace = str(workspace_root(state))
    pages = _page_list(state)
    valid_pages = [page for page in pages if _page_id(page)]
    total = len(valid_pages)

    # 准备设计稿落盘目录（方案 B：仅 mkdir，无 clone/install/launch）。
    _emit_progress("正在准备设计稿目录", ready=0, total=total, pages=[])
    setup = await asyncio.to_thread(setup_ui_design_project, workspace)
    project_dir = str(setup.get("project_dir") or "")
    if setup.get("status") != "ready":
        _emit_progress(
            f"设计稿目录未就绪：{setup.get('message')}（代码仍会生成）",
            ready=0, total=total, pages=[],
        )
    else:
        _emit_progress("设计稿目录已就绪", ready=0, total=total, pages=[])
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
    _emit_progress(
        "全部页面设计稿已生成，等待逐页确认",
        ready=total,
        total=total,
        pages=list(page_entries),
    )
    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": page_entries,
    }


def _apply_template_to_page(
    page: dict[str, Any], page_key: str, project_dir: str, template_id: str
) -> dict[str, Any]:
    """用页面模板源码作为单页设计稿，落盘并返回更新后的页面条目。

    选模板路径：直接读模板 index.tsx 作 code（不调 LLM、不走 validate），
    因为模板是成熟可运行的 Pro 组件页面。落盘后与 LLM 生成的设计稿一样
    可被前端 DesignRenderer 渲染、被 build 阶段 _ui_design_reference_instruction
    读取还原。失败时标记 generation_failed，不阻断其他页。
    """

    entry = {
        "pageId": _page_id(page),
        "name": str(page.get("name") or _page_id(page)),
        "path": str(page.get("path") or "/"),
        "description": str(page.get("description") or ""),
        "page_key": page_key,
        "menu_path": menu_path_for_page(page, page_key),
        "route_path": route_path_for_page(menu_path_for_page(page, page_key)),
        "code_path": "",
        "code": "",
        "status": "pending",
        "template_id": template_id,
    }
    if not project_dir:
        return entry
    try:
        code = load_template_source(template_id)
        entry["code_path"] = persist_page_code(project_dir, page_key, code)
        entry["code"] = code
        entry["template_source_path"] = (
            f"src/renderer/src/templates/{template_id}"
        )
        # 选模板成功即视为该页设计稿已确认：模板是成熟可运行代码，无需用户
        # 再手动确认。前端据 status=confirmed 在左侧列表标记已完成。
        entry["status"] = "confirmed"
    except Exception as exc:
        logger.exception("ui_design_template_failed page_id=%s", _page_id(page))
        entry["status"] = "generation_failed"
        entry["error"] = str(exc)[:500]
    return entry


async def _apply_adjust_pages(
    state: ProjectState,
    pages: list[dict[str, Any]],
    project_dir: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """多页调整：顺序遍历 pageIds，对每页基于现有设计稿 + 调整指令重新生成。

    一个 run 内顺序处理（不并发，避免 LLM 限流/写冲突），每页完成推送流式进度，
    前端显示"正在调整第 X/N 页"。失败的页标记 generation_failed，继续处理其他页。
    返回更新后的 ui_designs（所有选中页都已调整，其余页原样保留）。
    """

    raw_ids = action.get("pageIds")
    page_ids = (
        [str(pid).strip() for pid in raw_ids if str(pid).strip()]
        if isinstance(raw_ids, list)
        else []
    )
    instruction = str(action.get("instruction") or "").strip()
    if not instruction:
        return {
            "confirmation_status": "pending_user_confirmation",
            "pages": pages,
        }

    # 用户未通过 @页面名 指定目标时，让大模型根据 instruction + 所有页面信息
    # 自行判断需要调整哪些页面。
    if not page_ids:
        _emit_progress(
            "正在分析需要调整的页面…",
            ready=0,
            total=0,
            pages=list(pages),
            adjust_total=0,
            adjust_ready=0,
        )
        spec_pages = _page_list(state)
        page_ids = await asyncio.to_thread(
            resolve_adjust_target_pages, spec_pages, instruction
        )
        if not page_ids:
            _emit_progress(
                "未能从指令中识别出需要调整的页面",
                ready=0,
                total=0,
                pages=list(pages),
                adjust_total=0,
                adjust_ready=0,
            )
            return {
                "confirmation_status": "pending_user_confirmation",
                "pages": pages,
            }

    total = len(page_ids)

    # pageId → 页面条目索引，便于按 id 定位。
    index_by_id: dict[str, int] = {}
    for i, p in enumerate(pages):
        pid = _page_id(p) or str(p.get("pageId") or "")
        if pid:
            index_by_id[pid] = i

    _emit_progress(
        f"开始调整 {total} 个页面的设计稿",
        ready=0,
        total=total,
        pages=list(pages),
        adjust_total=total,
        adjust_ready=0,
    )

    for seq, page_id in enumerate(page_ids):
        idx = index_by_id.get(page_id)
        if idx is None:
            _emit_progress(
                f"跳过未知页面：{page_id}（第 {seq + 1}/{total} 页）",
                ready=seq,
                total=total,
                pages=list(pages),
                adjust_total=total,
                adjust_ready=seq,
            )
            continue
        target = pages[idx]
        page_key = str(target.get("page_key") or "").strip()
        name = str(target.get("name") or page_id)
        # 现有设计稿：优先落盘文件，其次条目内联 code。
        prev_code = ""
        if page_key and project_dir:
            prev_code = load_page_code(project_dir, page_key) or ""
        if not prev_code:
            prev_code = str(target.get("code") or "")
        if not prev_code:
            _emit_progress(
                f"跳过无设计稿的页面：{name}（第 {seq + 1}/{total} 页）",
                ready=seq,
                total=total,
                pages=list(pages),
                adjust_total=total,
                adjust_ready=seq,
            )
            continue

        _emit_progress(
            f"正在调整设计稿（第 {seq + 1}/{total} 页）：{name}",
            ready=seq,
            total=total,
            pages=list(pages),
            adjust_total=total,
            adjust_ready=seq,
            adjust_current=page_id,
        )
        try:
            spec_page = next(
                (p for p in _page_list(state) if _page_id(p) == page_id),
                target,
            )
            code = await asyncio.to_thread(
                generate_adjusted_page_react_code,
                spec_page,
                page_key,
                project_dir,
                prev_code,
                instruction,
            )
            code_path = persist_page_code(project_dir, page_key, code)
            pages[idx] = {
                **target,
                "code": code,
                "code_path": code_path,
                "status": "confirmed",
            }
            _emit_progress(
                f"设计稿已调整：{name}（第 {seq + 1}/{total} 页完成）",
                ready=seq + 1,
                total=total,
                pages=list(pages),
                adjust_total=total,
                adjust_ready=seq + 1,
            )
        except Exception as exc:
            logger.exception("ui_design_adjust_failed page_id=%s", page_id)
            pages[idx] = {
                **target,
                "status": "generation_failed",
                "error": str(exc)[:500],
            }
            _emit_progress(
                f"设计稿调整失败：{name}（第 {seq + 1}/{total} 页）",
                ready=seq + 1,
                total=total,
                pages=list(pages),
                adjust_total=total,
                adjust_ready=seq + 1,
            )

    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": pages,
    }


async def _apply_ui_design_action(
    state: ProjectState, existing: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    """处理单页"选模板"或"重新生成"动作，增量更新 ui_designs 并返回新状态。

    只更新动作指定的那一页，其余页面（含已确认状态）原样保留。处理完后
    返回 requires_user_input 重放确认卡，不推进到 project_planning。
    """

    workspace = str(workspace_root(state))
    setup = await asyncio.to_thread(setup_ui_design_project, workspace)
    project_dir = str(setup.get("project_dir") or "")

    page_id = str(action.get("pageId") or "").strip()
    action_type = str(action.get("action") or "").strip()
    pages = existing.get("pages") if isinstance(existing, dict) else None
    pages = [p for p in (pages or []) if isinstance(p, dict)]

    # 多页调整：顺序遍历 pageIds，对每页基于现有设计稿 + 调整指令重新生成。
    # 一个 run 内顺序处理（不并发），每页完成推送进度，前端显示"第 X/N 页"。
    if action_type == "adjust_pages":
        return await _apply_adjust_pages(state, pages, project_dir, action)

    # 派生 page_key：优先复用条目已有的 page_key，否则按 pageId 重新派生。
    used_keys: set[str] = {"DefaultPage"}
    for p in pages:
        key = str(p.get("page_key") or "").strip()
        if key:
            used_keys.add(key)

    target_index = -1
    for i, p in enumerate(pages):
        if _page_id(p) == page_id or str(p.get("pageId") or "") == page_id:
            target_index = i
            break
    if target_index < 0:
        _emit_progress(
            f"未找到待操作页面：{page_id}",
            ready=len(pages),
            total=len(pages),
            pages=list(pages),
        )
        return existing

    target_page = pages[target_index]
    page_key = str(target_page.get("page_key") or "").strip()
    if not page_key:
        # 找不到原始 page 时用 requirement_spec 里的页面信息派生
        spec_page = next(
            (p for p in _page_list(state) if _page_id(p) == page_id),
            {"pageId": page_id, "name": page_id},
        )
        page_key = derive_page_key(spec_page, used_keys)

    if action_type == "select_template":
        template_id = str(action.get("templateId") or "").strip()
        _emit_progress(
            f"正在套用页面模板：{target_page.get('name') or page_id}",
            pageId=page_id,
            ready=len(pages),
            total=len(pages),
            pages=list(pages),
        )
        updated = _apply_template_to_page(
            target_page, page_key, project_dir, template_id
        )
    else:  # regenerate
        _emit_progress(
            f"正在生成设计稿：{target_page.get('name') or page_id}",
            pageId=page_id,
            ready=len(pages),
            total=len(pages),
            pages=list(pages),
        )
        # 删除已落盘设计稿，绕过 load_page_code 复用，强制重新调 LLM。
        delete_page_code(project_dir, page_key)
        spec_page = next(
            (p for p in _page_list(state) if _page_id(p) == page_id),
            target_page,
        )
        updated = {
            "pageId": page_id,
            "name": str(target_page.get("name") or page_id),
            "path": str(target_page.get("path") or "/"),
            "description": str(target_page.get("description") or ""),
            "page_key": page_key,
            "menu_path": str(target_page.get("menu_path") or menu_path_for_page(spec_page, page_key)),
            "route_path": str(target_page.get("route_path") or route_path_for_page(str(target_page.get("menu_path") or menu_path_for_page(spec_page, page_key)))),
            "code_path": "",
            "code": "",
            "status": "pending",
        }
        try:
            code = await asyncio.to_thread(
                generate_page_react_code, spec_page, page_key, project_dir
            )
            updated["code_path"] = persist_page_code(project_dir, page_key, code)
            updated["code"] = code
            # 换一换成功即视为该页设计稿已确认：用户主动触发生成，无需再手动确认。
            updated["status"] = "confirmed"
            _emit_progress(
                f"设计稿已重新生成：{updated['name']}",
                pageId=page_id,
                ready=len(pages),
                total=len(pages),
                pages=list(pages),
            )
        except Exception as exc:
            logger.exception("ui_design_regenerate_failed page_id=%s", page_id)
            updated["status"] = "generation_failed"
            updated["error"] = str(exc)[:500]
            _emit_progress(
                f"设计稿重新生成失败：{updated['name']}",
                pageId=page_id,
                ready=len(pages),
                total=len(pages),
                pages=list(pages),
            )

    # 增量更新：只替换目标页，其余页（含已确认状态）原样保留。
    # 重新生成时 updated 不含 template_id，{**target_page, **updated} 会保留旧
    # template_id——这里显式清除，恢复为纯 LLM 设计稿。
    if action_type == "regenerate":
        updated = {k: v for k, v in updated.items() if k not in {"template_id", "template_source_path"}}
        pages[target_index] = {**target_page, **updated}
        pages[target_index].pop("template_id", None)
        pages[target_index].pop("template_source_path", None)
    else:
        pages[target_index] = {**target_page, **updated}
    return {
        "confirmation_status": "pending_user_confirmation",
        "pages": pages,
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
        _persist_ui_designs(state, existing)
        return {
            "phase": "ui_confirmation",
            "status": "requires_user_input",
            "ui_designs": existing,
            "clarification": _ui_design_confirmation_payload(existing),
            "timeline": ["ui_confirmation"],
        }

    # 单页动作路径：用户逐页"选模板"或"重新生成"，即时更新该页设计稿后重放确认卡。
    # 不推进到 project_planning——只有"确认全部设计稿"才放行。动作处理完清除
    # ui_design_action，避免下次恢复时残留动作被重复执行。
    action = state.get("ui_design_action")
    if (
        isinstance(action, dict)
        and isinstance(existing, dict)
        and existing.get("confirmation_status") == "pending_user_confirmation"
        and _has_explicit_user_submission(state)
    ):
        ui_designs = await _apply_ui_design_action(state, existing, action)
        _persist_ui_designs(state, ui_designs)
        return {
            "phase": "ui_confirmation",
            "status": "requires_user_input",
            "ui_designs": ui_designs,
            "ui_design_action": None,
            "clarification": _ui_design_confirmation_payload(ui_designs),
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
        _persist_ui_designs(state, ui_designs)
        return {
            "phase": "ui_confirmation",
            "status": "completed",
            "ui_designs": ui_designs,
            "clarification": _ui_design_confirmed_payload(ui_designs),
            "timeline": ["ui_confirmation"],
        }

    # 首次进入：只准备页面骨架，不生成设计稿。用户在前端逐页"选模板"或
    # "换一换"后才产生设计稿（经 ui_design_action 单页动作路径回填）。
    ui_designs = await _build_pending_ui_designs(state)
    _persist_ui_designs(state, ui_designs)
    return {
        "phase": "ui_confirmation",
        "status": "requires_user_input",
        "ui_designs": ui_designs,
        "clarification": _ui_design_confirmation_payload(ui_designs),
        "timeline": ["ui_confirmation"],
    }


def _persist_ui_designs(state: ProjectState, ui_designs: dict[str, Any]) -> None:
    """把 ui_designs 落盘到工作区 specs/ui-designs.json，供主 workflow build 阶段读取。

    写盘失败只记日志不中断节点：设计稿代码文件已落盘，索引缺失时 build 阶段降级
    为不读设计稿，不影响主流程。
    """

    if not isinstance(ui_designs, dict) or not ui_designs:
        return
    try:
        write_ui_designs_json(state, ui_designs)
    except Exception:
        logger.exception("ui_designs_persist_failed")
