"""Build 的有限并发派发与逐任务完成收集。"""

import contextvars
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from copy import deepcopy
from collections.abc import Callable
from typing import Any

from app.services.build_scheduler import normalize_task_results
from app.services.run_workers import current_run_id
from app.services.workspace_process_registry import workspace_process_registry

Task = dict[str, Any]
ExecutionResult = tuple[list[Task], list[Task]]


class BuildDispatch:
    """持有整轮执行线程池，只在存在空闲槽位时提交任务。"""

    def __init__(self, execute: Callable[..., ExecutionResult], limit: int):
        """登记单任务执行入口和并发上限。"""
        self.execute = execute
        self.limit = limit
        self.pending: dict[Future, Task] = {}
        self.run_id = current_run_id()
        self.pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="build-task")

    def __enter__(self) -> "BuildDispatch":
        """让线程池生命周期覆盖整个 Build 调度循环。"""
        return self

    def __exit__(self, *args: Any) -> None:
        """退出前等待已启动任务收尾。"""
        self.pool.shutdown(wait=True, cancel_futures=True)

    def cancelled(self) -> bool:
        """沿用当前运行的取消登记，阻止停止后补入任务。"""
        return bool(self.run_id and workspace_process_registry.is_run_cancelled(self.run_id))

    def submit(self, state: Task, tasks: list[Task], callback: Callable) -> None:
        """为每个任务复制派发时的状态及 LangGraph 上下文。"""
        if len(tasks) + len(self.pending) > self.limit:
            raise ValueError("Build 派发超过并发上限。")
        for task in tasks:
            future = self.pool.submit(
                contextvars.copy_context().run,
                self.execute, deepcopy(state), [deepcopy(task)],
                on_batch_tool_activity=callback,
            )
            self.pending[future] = task

    def completed(self) -> tuple[Task, ExecutionResult] | None:
        """取一个已完成任务，其余任务继续占用槽位直到结果提交。"""
        done, _ = wait(self.pending, timeout=0.2, return_when=FIRST_COMPLETED)
        if not done:
            return None
        future = next(item for item in self.pending if item in done)
        task = self.pending.pop(future)
        try:
            return task, future.result()
        except Exception as exc:
            # 校验或文档收尾异常同样归属该任务，其余在途结果仍需逐个提交。
            results = normalize_task_results(dispatched_tasks=[task], raw_results=[{
                "task_id": task["id"], "owner": task.get("owner"),
                "status": "failed", "failure_category": "runner_crash",
                "failure_reason": f"{type(exc).__name__}: {exc}",
            }])
            return task, (results, [])
