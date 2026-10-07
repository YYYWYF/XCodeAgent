"""只读识别当前 UI 确认门中已失去后台 worker 的生成任务。"""

from typing import Any

from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path


def interrupted_ui_design_pages(state: dict[str, Any], workspace: str) -> list[str]:
    """以磁盘状态和当前进程池判断孤立页面，不修改产物或自动启动生成。"""

    manifest = load_ui_designs_json(ui_designs_json_path(state))
    if not isinstance(manifest, dict) or manifest.get("confirmation_status") != "pending_user_confirmation":
        return []
    plan = state.get("product_plan") or {}
    pages = plan.get("pages") or []
    current_ids = {page.get("pageId") for page in pages if isinstance(page, dict)}
    active_ids = get_ui_design_generation_pool().pending_page_ids(workspace)
    # 只报告正式计划仍持有、磁盘仍在生成而当前池已不持有的任务；活跃 worker 不重试。
    return sorted({
        str(page["pageId"])
        for page in manifest.get("pages", [])
        if isinstance(page, dict) and page.get("pageId") in current_ids
        and page.get("pageId") and page.get("status") in {"queued", "generating"}
        and page["pageId"] not in active_ids
    })
