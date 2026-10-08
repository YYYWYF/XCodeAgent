from __future__ import annotations

import asyncio

from langgraph.config import get_stream_writer

from app.agents.screenshot_ui_design import prepare_screenshot_ui_designs
from app.graph.state import ProjectState


def _emit_progress(message: str, **detail: object) -> None:
    """向 AG-UI custom stream 推送截图 UI 准备进度。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer(
        {
            "type": "screenshot_ui_preparation.progress",
            "node_name": "screenshot_ui_preparation",
            "message": message,
            "detail": detail,
        }
    )


async def screenshot_ui_preparation(state: ProjectState) -> dict:
    """异步执行截图到高保真 TSX 的准备层，再交回原 UI 确认流程。"""

    _emit_progress("正在分析截图视觉并生成页面设计稿")

    def forward_progress(message: str, detail: dict[str, object]) -> None:
        """把工作线程中的映射、逐页生成和后台审查阶段转为 AG-UI 进度。"""

        _emit_progress(message, **detail)

    result = await asyncio.to_thread(
        prepare_screenshot_ui_designs,
        dict(state),
        forward_progress,
    )
    ui_designs = result.get("ui_designs") if isinstance(result, dict) else None
    pages = ui_designs.get("pages") if isinstance(ui_designs, dict) else []
    failed = sum(
        1
        for page in (pages or [])
        if isinstance(page, dict) and page.get("status") == "generation_failed"
    )
    _emit_progress(
        "截图设计稿准备完成，进入原 UI 确认",
        total=len(pages or []),
        failed=failed,
    )
    return result
