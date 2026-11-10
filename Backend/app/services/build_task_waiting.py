"""区分可由任务重试恢复的 Build 等待，与阶段失败和正式确认。"""


def awaiting_build_task_retry(state: dict) -> bool:
    """只识别已提交的任务重试等待状态，不把其他用户确认门当作重试入口。"""
    return (state.get("phase") == "build"
            and state.get("status") == "requires_user_input"
            and (state.get("build_summary") or {}).get("status") == "awaiting_task_retry"
            and not state.get("clarification"))


def task_retry_wait_summary(summary: dict, tasks: list[dict], results: list[dict], events: list[str]) -> dict:
    """仅将全部失败均可直接重试的自然阻塞转为等待，保留阶段错误的失败语义。"""
    from app.services.build_scheduler import retryable_failed_task_ids

    fatal_events = {"scheduler:invalid_dag", "scheduler:cancelled", "scheduler:iteration_budget_exhausted"}
    failed_ids = {str(task["id"]) for task in tasks if task.get("status") == "failed"}
    retryable_ids = retryable_failed_task_ids(tasks, results)
    if (summary.get("status") == "failed" and failed_ids and failed_ids <= retryable_ids
            and not fatal_events.intersection(events)):
        return {**summary, "status": "awaiting_task_retry"}
    return summary
