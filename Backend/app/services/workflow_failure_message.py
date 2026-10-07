"""从当前节点的失败事实提取恢复展示原因，不改变业务执行结果。"""

from typing import Any


def workflow_failure_message(state: dict[str, Any]) -> str | None:
    """仅当前 Build 失败时优先使用收尾错误，避免沿用扫描成功摘要。"""
    summary = state.get("build_summary")
    if state.get("phase") == "build" and state.get("status") == "failed" and isinstance(summary, dict) and summary.get("status") == "failed":
        reason = summary.get("finalization_error")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    reason = state.get("error")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None
