"""将单任务重试交给唯一的 Build 调度线程，HTTP 线程不修改运行状态。"""

from concurrent.futures import Future
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock

from app.services.build_scheduler import retryable_failed_task_ids

_lock = RLock()
_runs: dict[tuple[str, str], dict] = {}


def validate_task_retry(state: dict, task_id: str) -> None:
    """仅允许当前切片的瞬时失败重试，确认和修复计划不能借此绕过。"""
    from app.services.build_scheduler import resolve_execution_slice

    tasks = resolve_execution_slice(build_task_plan=state["build_task_plan"],
        tasks=state["tasks"], build_execution_scope=state.get("build_execution_scope"))["tasks"]
    if task_id not in retryable_failed_task_ids(tasks, state.get("build_results", [])):
        raise ValueError("该任务当前不可直接重试；请刷新状态，或使用对应的修复/确认入口。")
    if (state.get("clarification")
            or (state.get("build_summary") or {}).get("status") == "requires_confirmation"
            or (state.get("repair_task_plan") or {}).get("decision") == "requires_user_confirmation"):
        raise ValueError("Build 正在等待确认，请先处理当前确认。")


def _key(workspace: str, run_id: str) -> tuple[str, str]:
    """规范化运行登记键，防止工作区路径拼写造成重复调度。"""
    return str(Path(workspace).resolve()).casefold(), run_id


@contextmanager
def register_build_scheduler(state: dict):
    """登记整个调度生命周期，退出时明确拒绝尚未接收的重试。"""
    key = _key(state.get("workspace") or ".", state.get("active_run_id", ""))
    with _lock:
        if key in _runs:
            raise ValueError("该 Build 已有调度器。")
        _runs[key] = {"state": None, "requests": {}}
    try:
        yield
    finally:
        with _lock:
            entry = _runs.pop(key)
            for future in entry["requests"].values():
                if not future.done():
                    future.set_exception(ValueError("Build 已离开调度阶段，请刷新后重试。"))


def publish_build_retry_state(state: dict) -> None:
    """发布调度线程拥有的快照，供重试请求校验而非直接修改。"""
    with _lock:
        entry = _runs.get(_key(state.get("workspace") or ".", state.get("active_run_id", "")))
        if entry is not None:
            entry["state"] = deepcopy(state)


def enqueue_build_task_retry(workspace: str, run_id: str, task_id: str) -> Future:
    """按任务去重并等待调度器实际落盘，避免只返回虚假的入队成功。"""
    with _lock:
        entry = _runs.get(_key(workspace, run_id))
        if not entry or entry["state"] is None:
            raise ValueError("Build 调度器当前不可接收重试，请刷新后重试。")
        validate_task_retry(entry["state"], task_id)
        requests = entry["requests"]
        if task_id not in requests:
            requests[task_id] = Future()
        return requests[task_id]


def consume_build_task_retries(state: dict, commit) -> int:
    """在调度线程中重置目标、持久化并确认请求，其他任务状态保持原样。"""
    from app.services.build_scheduler import reset_failed_tasks_for_retry
    from app.services.build_task_planner import replace_build_task_plan_tasks

    with _lock:
        entry = _runs[_key(state.get("workspace") or ".", state.get("active_run_id", ""))]
        requests = entry["requests"]
        if not requests:
            return 0
        count = 0
        for task_id, future in list(requests.items()):
            try:
                validate_task_retry(state, task_id)
                tasks = reset_failed_tasks_for_retry(state["tasks"], {task_id})
                updated = {**state, "tasks": tasks,
                    "build_task_plan": replace_build_task_plan_tasks(state["build_task_plan"], tasks)}
                commit(updated)
                state.update(updated)
                count += 1
                if not future.done():
                    future.set_result({"taskId": task_id, "sourceRunId": state["active_run_id"]})
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            del requests[task_id]
        entry["state"] = deepcopy(state)
        return count
