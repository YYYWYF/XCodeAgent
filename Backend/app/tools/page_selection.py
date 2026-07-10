from __future__ import annotations

from typing import Any


def present_page_selection(
    pages: list[dict[str, Any]],
    selected_page_id: str | None = None,
) -> dict[str, Any]:
    """Return the simplest page-selection interaction payload.

    The real implementation can emit an AG-UI event and pause the graph with
    LangGraph interrupt. For the runnable demo, this function records the
    options and chooses either the upstream selected page or the first page.
    """

    options = [
        {
            "id": page.get("id"),
            "label": page.get("name") or page.get("id") or "未命名页面",
            "description": (
                f"{page.get('path') or '/'} · "
                f"{page.get('description') or page.get('name') or '待补充页面目标'}"
            ),
        }
        for page in pages
        if isinstance(page, dict) and page.get("id")
    ]
    valid_ids = {option["id"] for option in options}
    resolved_page_id = selected_page_id if selected_page_id in valid_ids else None
    if resolved_page_id is None and options:
        resolved_page_id = options[0]["id"]

    return {
        "type": "page_selection",
        "status": "auto_selected" if selected_page_id is None else "selected",
        "prompt": "请选择一个页面进行详细设计。",
        "options": options,
        "selected_page_id": resolved_page_id,
        "message": "当前最简版不阻塞等待用户选择；如未传入 selected_page_id，则默认选择第一个页面。",
    }
