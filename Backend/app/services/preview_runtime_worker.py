"""将独立预览的同步工作接入现有 Agent、模型和命令取消边界。"""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from threading import Event
from typing import ParamSpec, TypeVar

from langchain_core.runnables.config import ensure_config, set_config_context

from app.services.run_workers import bind_run_workers, run_worker
from app.services.workspace_process_registry import workspace_process_registry


P = ParamSpec("P")
T = TypeVar("T")


async def run_preview_worker(
    run_id: str, workspace: str, cancelled: Event,
    function: Callable[P, T], *args: P.args, **kwargs: P.kwargs,
) -> T:
    """绑定真实工作身份，取消后等待同步线程及其清理实际退出。"""

    def invoke() -> T:
        """同时传播 ContextVar 和 RunnableConfig，覆盖 DeepAgent 的嵌套上下文。"""
        if cancelled.is_set():
            raise RuntimeError("预览修复已请求停止。")
        with bind_run_workers(run_id, workspace), run_worker():
            config = ensure_config({"metadata": {"run_id": run_id, "workspace": workspace}})
            with set_config_context(config) as context:
                return context.run(function, *args, **kwargs)

    worker = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        workspace_process_registry.cancel_run(run_id)
        # 外层取消不能提前释放维护身份；同步模型和工具仍须真正完成收尾。
        with suppress(Exception):
            await asyncio.shield(worker)
        raise
