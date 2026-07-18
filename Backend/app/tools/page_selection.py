from __future__ import annotations

from typing import Any


def present_page_selection(
    pages: list[dict[str, Any]],
    selectedPageId: str | None = None,
) -> dict[str, Any]:
    """Return the simplest page-selection interaction payload.

    The real implementation can emit an AG-UI event and pause the graph with
    LangGraph interrupt. For the runnable demo, this function records the
    options and chooses either the upstream selected page or the first page.
    """

    options = [
        {
            "id": page.get("pageId"),
            "label": page.get("name") or page.get("pageId") or "未命名页面",
            "description": (
                f"{page.get('path') or '/'} · "
                f"{page.get('description') or page.get('name') or '待补充页面目标'}"
            ),
        }
        for page in pages
        if isinstance(page, dict) and page.get("pageId")
    ]
    valid_ids = {option["id"] for option in options}
    resolvedPageId = selectedPageId if selectedPageId in valid_ids else None
    if resolvedPageId is None and options:
        resolvedPageId = options[0]["id"]

    return {
        "type": "page_selection",
        "status": "auto_selected" if selectedPageId is None else "selected",
        "prompt": "请选择一个页面进行详细设计。",
        "options": options,
        "selectedPageId": resolvedPageId,
        "message": "当前最简版不阻塞等待用户选择；如未传入 selectedPageId，则默认选择第一个页面。",
    }
