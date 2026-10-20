"""跟踪 Graph 同步线程和 Agent 调用的真实生命周期，不把 asyncio 取消当成退出。"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction, isfunction
from threading import Condition
from time import monotonic
from pathlib import Path
import os

from langgraph.graph import StateGraph

_run: ContextVar[tuple[str, str]] = ContextVar("workflow_worker_run", default=("", ""))
_condition = Condition()
_workers: dict[str, tuple[str, int]] = {}


def _current_worker_identity() -> tuple[str, str]:
    """优先使用显式运行绑定，嵌套 Context 重建后从当前 RunnableConfig 取回同次身份。"""
    identity = _run.get()
    if identity[0]:
        return identity
    from langchain_core.runnables.config import ensure_config

    # LangGraph/DeepAgent 的嵌套调用会继续传递 RunnableConfig，但不保证保留外层 ContextVar。
    metadata = ensure_config().get("metadata", {})
    run_id, workspace = metadata.get("run_id"), metadata.get("workspace")
    if not isinstance(run_id, str) or not run_id or not isinstance(workspace, str) or not workspace:
        return ("", "")
    return run_id, os.path.normcase(str(Path(workspace).expanduser().resolve()))


def current_run_id() -> str:
    """读取随 Graph 和线程池传播的精确运行身份。"""
    return _current_worker_identity()[0]


@contextmanager
def bind_run_workers(run_id: str, workspace: str):
    """只在本次 Graph producer 内绑定运行，避免缓存 Agent 固定到旧身份。"""
    workspace_key = os.path.normcase(str(Path(workspace).expanduser().resolve())) if workspace else ""
    token = _run.set((run_id, workspace_key))
    try:
        yield
    finally:
        _run.reset(token)


@contextmanager
def run_worker():
    """登记真实工作直到 finally 完成，并拒绝取消之后才调度的迟到工作。"""
    from app.services.workspace_process_registry import workspace_process_registry

    run_id, workspace = _current_worker_identity()
    if not run_id:
        yield
        return
    with _condition:
        if workspace_process_registry.is_run_cancelled(run_id):
            raise RuntimeError("当前运行已停止，拒绝继续执行 Agent 或工具。")
        _workers[run_id] = (workspace, _workers.get(run_id, (workspace, 0))[1] + 1)
    try:
        yield
    finally:
        with _condition:
            count = _workers[run_id][1] - 1
            if count:
                _workers[run_id] = (workspace, count)
            else:
                _workers.pop(run_id, None)
            _condition.notify_all()


def active_worker_runs(workspace: str = "") -> set[str]:
    """让控制面看到外层 Task 已取消但仍在清理文件的同步节点。"""
    with _condition:
        return {run_id for run_id, (root, _) in _workers.items() if not workspace or root == workspace}


def wait_run_workers(run_id: str, timeout_seconds: float) -> bool:
    """等待同步节点及嵌套 Agent 的真实退出，超时明确返回失败。"""
    deadline = monotonic() + timeout_seconds
    with _condition:
        while run_id in _workers:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            _condition.wait(remaining)
    return True


class WorkerStateGraph(StateGraph):
    """在节点完整函数边界登记同步线程，包含 Agent 结束后的回滚与写盘。"""

    def add_node(self, node, action=None, **kwargs):
        """保留节点签名和 LangGraph 配置注入，只包装普通同步函数。"""
        target = action if action is not None else node
        if isfunction(target) and not iscoroutinefunction(target):
            @wraps(target)
            def tracked(*args, **call_kwargs):
                """等待节点的全部收尾工作完成后才释放退出登记。"""
                with run_worker():
                    return target(*args, **call_kwargs)
            if action is None:
                return super().add_node(node.__name__, tracked, **kwargs)
            action = tracked
        return super().add_node(node, action, **kwargs)
