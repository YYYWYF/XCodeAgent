"""一次前端集成编码运行与只读收尾，复用平台真实改动检查。"""

from typing import Any

from app.agents.model_operations import model_operation
from app.agents.small_task import invoke_small_task_agent, normalize_small_task_result
from app.agents.small_task.frontend_integration import finalize_frontend_repair
from app.agents.tool_activity_stream import ToolActivityCallback
from app.observability.build_instrumentation import integration_repair_trace
from app.services.engineering_acceptance_verifier import unauthorized_batch_paths
from app.services.run_workers import run_worker
from app.services.small_task_scope import _path_matches_task
from app.workspace.code_changes import WorkspaceSnapshot, diff_workspace_snapshots, snapshot_workspace


def execute_frontend_integration_task(
    *, packet: dict[str, Any], state: dict[str, Any], task: dict[str, Any],
    workspace: str | None, before: WorkspaceSnapshot | None, batch: list[dict[str, Any]],
    on_tool_activity: ToolActivityCallback | None,
) -> dict[str, Any]:
    """完整编码只执行一次，格式恢复不得访问工具或重新执行编辑。"""
    receipts: list[dict[str, str]] = []

    def observe_tool(activity: dict[str, Any]) -> None:
        """记录工具名、状态及安全路径，继续向现有 AG-UI 发送工具活动。"""
        if activity.get("status") in {"completed", "failed"} and len(receipts) < 24:
            receipts.append({
                key: str(activity[key])[:300] for key in ("tool", "status", "path") if activity.get(key)
            })
        if on_tool_activity is not None:
            on_tool_activity(activity)

    with model_operation("repair_frontend_integration_code", business_stage="integration_test"), integration_repair_trace(
        "small_task_repair", state=state, task_id=packet["taskId"],
        repair_round=int(task.get("repair_attempt") or (int(state.get("repair_iteration") or 0) + 1)),
    ) as trace, run_worker():
        note = invoke_small_task_agent(
            packet=packet, workspace=workspace, selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=observe_tool,
        )
        normalized = normalize_small_task_result(note)
        with run_worker():
            if normalized.get("failureCode") == "invalid_agent_output":
                current = snapshot_workspace(workspace)
                files = diff_workspace_snapshots(before, current, source_tool=packet["source"]) if before and current else []
                if unauthorized_batch_paths({"files": files}, batch):
                    trace.reason = "unauthorized_diff"
                else:
                    diff = [
                        {"path": item["path"], "change": str(item.get("changeType") or "")}
                        for item in files if _path_matches_task(str(item.get("path") or ""), task)
                    ]
                    trace.reason = "invalid_result"
                    try:
                        recovered = finalize_frontend_repair(note=note, receipts=receipts, authorized_diff=diff)
                    except Exception:
                        # 模型异常沿用调用 Span 留痕，仍可用已核实差异进入真实复测。
                        recovered = None
                    with run_worker():
                        if recovered is not None:
                            normalized = normalize_small_task_result(recovered)
                            trace.reason = "result_recovered"
                        elif diff and not any(status in note for status in ("requires_user_confirmation", "requires_workflow")):
                            # 仅证明授权修改已落盘。主图仍须跑原失败检查并重评质量门禁。
                            trace.reason = "verified_diff_pending_retest"
                            normalized = normalize_small_task_result(
                                '{"status":"completed","summary":"已核实授权修改，等待集成复测。"}'
                            )
        if normalized["status"] in {"requires_user_confirmation", "requires_workflow"}:
            trace.status = "awaiting_user"
        elif normalized["status"] == "failed":
            trace.status = "failed"
        return normalized
