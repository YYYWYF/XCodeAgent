"""集中管理测试重入：失败后重开测试轮次，暂停续接保留原有进度。"""

from copy import deepcopy
from typing import Any

from app.domain.execution_recovery import RecoveryExecutionError, WorkflowReentryReason
from app.services.node_recovery_context import current_node_recovery_context


def prepare_integration_test_reentry(
    state: dict[str, Any], node_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """只处理已验证的首次节点重入，正常测试循环保持原有行为。"""

    recovery = current_node_recovery_context()
    if recovery is None:
        return state, {}
    restored = dict(state)
    progress = recovery.internal_progress
    if progress is not None:
        for key in ("build_execution_scope", "build_run_id", "build_run_plan_path", "build_run_plan_sha256"):
            if progress.get(key) != state.get(key):
                raise RecoveryExecutionError("INTEGRATION_RETRY_BINDING_DRIFT", "测试进度与节点入口的范围或 Build 绑定不一致。")
        restored.update({
            key: deepcopy(value) for key, value in progress.items()
            if key.startswith("integration_") or key.startswith("small_task_")
            or key in {"repair_tasks", "repair_task_plan", "repair_iteration", "max_repair_iterations", "code_changes", "code_change_sets"}
        })
    if node_name == "small_task_repair":
        tasks = restored.get("small_task_tasks") or restored.get("repair_tasks") or []
        tasks = [
            {**deepcopy(task), "status": "completed" if task.get("status") in {"completed", "already_satisfied"} else "pending"}
            for task in tasks if isinstance(task, dict)
        ]
        restored.update({"small_task_tasks": tasks, "repair_tasks": tasks, "repair_return_node": "integration_test"})
    else:
        # 源码可能已由上一轮修复改变，重新执行检查，不能复用旧通过结果。
        restored.update({"integration_build_checks_completed": False, "integration_build_results": [], "test_results": [], "test_report": {}})
    restored.update({"status": "in_progress", "error": None, "quality_gate_passed": False})
    if recovery.reentry_reason in {WorkflowReentryReason.FAILURE_RETRY, WorkflowReentryReason.BUSINESS_RETRY}:
        # 只有用户显式重试失败测试才开启新预算；内部修复循环没有恢复上下文，
        # 暂停继续也不走此分支，避免自动循环借重入无限补充修复额度。
        restored.update({
            "repair_iteration": 0, "repair_tasks": [], "repair_task_plan": {},
            "repair_task_plan_path": None, "repair_return_node": "integration_test",
            "small_task_tasks": [], "small_task_results": [],
            "small_task_handoff": {}, "small_task_handoff_submission": {},
            "small_task_route": "", "integration_next_action": "",
            "clarification": {}, "frontend_performance_decision": "",
            "test_report_path": None, "test_report_json_path": None,
            "needs_revision": False, "revision_requests": [],
        })
        # 保留 code_changes/code_change_sets/small_task_code_change_sets：重启测试
        # 只丢弃旧检查与旧任务，不能回滚磁盘代码或丢掉已经完成的变更归属。
    from app.graph.nodes.workspace_inspection import inspect_workspace

    scanned = inspect_workspace(restored)
    fields = {key: value for key, value in scanned.items() if key.startswith("workspace_")}
    # 重置值也必须提交主图；仅传给节点会让未被节点返回的旧任务与预算留在 checkpoint。
    updates = {key: value for key, value in restored.items() if key not in state or value != state[key]}
    updates.update({"workspace_snapshot": {}, **fields})
    return {**restored, **updates}, updates
