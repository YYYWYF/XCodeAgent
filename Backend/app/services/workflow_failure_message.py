"""从当前节点的失败事实提取恢复展示原因，不改变业务执行结果。"""

from typing import Any


def workflow_failure_message(state: dict[str, Any]) -> str | None:
    """当前 Build 优先使用收尾错误或本轮失败任务，其他节点保留自身错误。"""
    summary = state.get("build_summary")
    if state.get("phase") == "build" and state.get("status") == "failed" and isinstance(summary, dict) and summary.get("status") == "failed":
        reason = summary.get("finalization_error")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
        # Build 必须从本轮切片的失败任务取原因，不能沿用上一节点的 message。
        execution_slice = state.get("build_execution_slice")
        tasks = execution_slice.get("tasks") if isinstance(execution_slice, dict) else None
        tasks = tasks if isinstance(tasks, list) else state.get("tasks", [])
        tasks = tasks if isinstance(tasks, list) else []
        reasons: list[str] = []
        for task in tasks:
            if not isinstance(task, dict) or task.get("status") != "failed":
                continue
            detail = task.get("failure_reason")
            if isinstance(detail, str) and detail.strip():
                reasons.append(f"{task.get('id') or '构建任务'}：{detail.strip()[:400]}")
        if reasons:
            return "代码构建失败：" + "；".join(reasons[:3])
        bootstrap = state.get("authorization_bootstrap_result")
        old_notice = bootstrap.get("message") if isinstance(bootstrap, dict) else None
        reason = state.get("error")
        if isinstance(reason, str) and reason.strip() and reason != old_notice:
            return reason.strip()
        return "代码构建失败，请查看构建任务详情后重试。"
    reason = state.get("error")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None
