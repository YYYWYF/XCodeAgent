from __future__ import annotations

import re
from typing import Any

from app.services.ui_design_manifest import inspect_ui_code_bindings

from .shared_shell import (
    SharedShell,
    compose_shared_shell,
    remove_generated_sidebar,
    split_page_contract,
)


_SHELL_STYLE_MARKER = "\nconst screenshotShellStyles:"
_SHELL_IMPORT = "import * as ScreenshotReact from 'react';\n"
_CONTENT_COMPONENT = re.compile(
    r"<section style=\{screenshotShellStyles\.content\}>"
    r"<([A-Za-z_$][\w$]*)\s*/></section>"
)


def _transferred_shell_contract(
    original: str,
    content: str,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把移除旧侧栏时丢失的真实产品绑定转交共享壳，避免仅保留外观。"""

    _, shell_actions, shell_items = split_page_contract(page, pages, shell)
    before = inspect_ui_code_bindings(original)
    after = inspect_ui_code_bindings(content)
    lost_actions = set(before["actions"]) - set(after["actions"])
    lost_items = set(before["information_items"]) - set(after["information_items"])
    action_ids = {str(item.get("actionId") or "") for item in shell_actions}
    item_ids = {str(item.get("itemId") or "") for item in shell_items}
    shell_actions.extend(
        item for item in (page.get("actions") or []) if isinstance(item, dict)
        and str(item.get("actionId") or "") in lost_actions
        and str(item.get("actionId") or "") not in action_ids
    )
    shell_items.extend(
        item for item in (page.get("information_items") or []) if isinstance(item, dict)
        and str(item.get("itemId") or "") in lost_items
        and str(item.get("itemId") or "") not in item_ids
    )
    return shell_actions, shell_items


def extract_sidebar_controls_for_shared_shell(
    code: str,
    *,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    content_page: dict[str, Any],
    shell_actions: list[dict[str, Any]],
    shell_items: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
    """将旧侧栏内的页面专属契约迁移到统一侧栏，避免丢功能或整页退出共享壳。"""

    content = remove_generated_sidebar(code, pages)
    if not content:
        return None
    before = inspect_ui_code_bindings(code)
    after = inspect_ui_code_bindings(content)
    lost_actions = set(before["actions"]) - set(after["actions"])
    lost_items = set(before["information_items"]) - set(after["information_items"])
    page_actions = {
        str(item.get("actionId") or ""): item
        for item in (
            page.get("actions") if isinstance(page.get("actions"), list) else []
        )
        if isinstance(item, dict) and item.get("actionId")
    }
    page_items = {
        str(item.get("itemId") or ""): item
        for item in (
            page.get("information_items")
            if isinstance(page.get("information_items"), list)
            else []
        )
        if isinstance(item, dict) and item.get("itemId")
    }
    if lost_actions - page_actions.keys() or lost_items - page_items.keys():
        return None

    next_actions = list(shell_actions)
    known_action_ids = {str(item.get("actionId") or "") for item in next_actions}
    for action_id in sorted(lost_actions):
        if action_id not in known_action_ids:
            next_actions.append(page_actions[action_id])
            known_action_ids.add(action_id)
    next_items = list(shell_items)
    known_item_ids = {str(item.get("itemId") or "") for item in next_items}
    for item_id in sorted(lost_items):
        if item_id not in known_item_ids:
            next_items.append(page_items[item_id])
            known_item_ids.add(item_id)

    # 已迁入外壳的操作和信息必须从内容区契约中移除，最终完整页面仍在外壳合成后统一校验。
    next_content_page = {
        **content_page,
        "actions": [
            item
            for item in (
                content_page.get("actions")
                if isinstance(content_page.get("actions"), list)
                else []
            )
            if str(item.get("actionId") or "") not in lost_actions
        ],
        "information_items": [
            item
            for item in (
                content_page.get("information_items")
                if isinstance(content_page.get("information_items"), list)
                else []
            )
            if str(item.get("itemId") or "") not in lost_items
        ],
    }
    return content, next_content_page, next_actions, next_items


def _shell_region(code: str) -> str:
    """只读取共享壳的样式与侧栏结构，避免内容区变动触发无关重写。"""

    start = code.find(_SHELL_STYLE_MARKER)
    end = code.find("</aside>", start)
    return code[start : end + len("</aside>")] if start >= 0 and end > start else ""


def refreshed_shared_shell_code(
    code: str,
    *,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
) -> str:
    """在任务级截图观察更新后，仅重组旧页面的共享侧栏并保留内容源码。"""

    if not shell.enabled or "function ScreenshotSharedPage()" not in code:
        return ""
    start = code.find(_SHELL_STYLE_MARKER)
    component = _CONTENT_COMPONENT.search(code[start:]) if start >= 0 else None
    if component is None:
        return ""
    content = code[:start]
    if content.startswith(_SHELL_IMPORT):
        content = content[len(_SHELL_IMPORT) :]
    content += f"\nexport default {component.group(1)};\n"
    actions, items = _transferred_shell_contract(code, content, page, pages, shell)
    candidate = compose_shared_shell(
        content,
        page=page,
        pages=pages,
        shell=shell,
        shell_actions=actions,
        shell_items=items,
    )
    return candidate if _shell_region(candidate) != _shell_region(code) else ""


def recompose_page_with_shared_shell(
    code: str,
    *,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
) -> str:
    """将已生成页的旧共享壳或独立侧栏重组为同一任务级侧栏。"""

    if not shell.enabled:
        return code
    if "function ScreenshotSharedPage()" in code:
        return refreshed_shared_shell_code(code, page=page, pages=pages, shell=shell) or code
    content = remove_generated_sidebar(code, pages)
    if not content:
        raise ValueError("页面含有无法安全分离的侧边栏，未覆盖原设计稿。")
    actions, items = _transferred_shell_contract(code, content, page, pages, shell)
    return compose_shared_shell(
        content,
        page=page,
        pages=pages,
        shell=shell,
        shell_actions=actions,
        shell_items=items,
    )
