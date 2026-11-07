"""UI 单页模型任务只生成内存候选，文件提交由生成池核对任务归属后完成。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.services.page_templates import load_template_source
from app.services.ui_design_generator import (
    UiDesignStreamCancelled, generate_adjusted_page_react_code, generate_page_react_code,
)
from app.services.ui_design_manifest import build_ui_page_manifest
from app.services.run_workers import run_worker

logger = logging.getLogger(__name__)


async def run_ui_design_thread(function, *args, **kwargs):
    """UI 节点的同步 Agent 纳入真实线程登记，协程取消后仍等待线程完整退出。"""
    def tracked():
        """覆盖模型调用、校验及函数收尾的完整同步生命周期。"""
        with run_worker():
            return function(*args, **kwargs)

    future = asyncio.create_task(asyncio.to_thread(tracked))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        await asyncio.gather(future, return_exceptions=True)
        raise


@dataclass(frozen=True)
class UiDesignGenerationTask:
    """保存单页当前产品事实和明确生成动作，不携带前一次请求的取消状态。"""

    workspace: str
    project_id: str
    project_dir: str
    page_id: str
    spec_page: dict[str, Any]
    page_key: str
    action: str
    template_id: str = ""


class UiDesignGenerationCancelled(RuntimeError):
    """任务已撤销，候选不得提交到设计稿文件或清单。"""


def generate_page_entry(task: UiDesignGenerationTask, *, should_cancel: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """调用真实模型并校验候选，取消或失败时完整保留已有设计稿。"""

    try:
        if should_cancel():
            raise UiDesignGenerationCancelled("页面设计生成已取消。")
        if task.action == "select_template":
            if not task.project_dir or not task.template_id:
                raise ValueError("模板生成缺少 project_dir 或 template_id")
            code = generate_adjusted_page_react_code(
                task.spec_page, task.page_key, task.project_dir, load_template_source(task.template_id),
                "Treat the supplied template only as a visual reference. Replace its business semantics "
                "with exactly the ProductPlan information items and actions; preserve no undeclared fields or actions.",
                should_cancel=should_cancel, business_operation="adapt_ui_design_template",
            )
        else:
            code = generate_page_react_code(task.spec_page, task.page_key, task.project_dir, should_cancel=should_cancel)
        if should_cancel():
            raise UiDesignGenerationCancelled("页面设计生成已取消。")
        return build_ui_page_manifest(task.spec_page, page_key=task.page_key, code=code,
            status="confirmed", template_id=task.template_id,
            template_source_path=f"src/renderer/src/templates/{task.template_id}" if task.template_id else "")
    except (UiDesignGenerationCancelled, UiDesignStreamCancelled) as exc:
        raise UiDesignGenerationCancelled("页面设计生成已取消。") from exc
    except Exception as exc:
        if should_cancel():
            raise UiDesignGenerationCancelled("页面设计生成已取消。") from exc
        logger.exception("ui_design_generation_failed page_id=%s", task.page_id)
        return build_ui_page_manifest(task.spec_page, page_key=task.page_key,
            status="generation_failed", template_id=task.template_id, error=str(exc))
