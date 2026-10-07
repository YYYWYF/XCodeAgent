"""单测和修复节点重入时复用可证明的进度，并刷新当前工作区事实。"""

from copy import deepcopy
from typing import Any

from app.domain.execution_recovery import RecoveryExecutionError
from app.services.node_recovery_context import current_node_recovery_context


def prepare_unit_test_reentry(
    state: dict[str, Any], node_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """只在已验证的目标节点首轮重入时恢复内部状态，不改变节点入口身份。"""

    recovery = current_node_recovery_context()
    if recovery is None:
        return state, {}
    progress = recovery.internal_progress
    restored = dict(state)
    if progress is not None:
        # 来源归属由外层 checkpoint 证明；业务范围及冻结 Build 绑定还必须一致。
        for key in ("build_execution_scope", "build_run_id", "build_run_plan_path", "build_run_plan_sha256"):
            if progress.get(key) != state.get(key):
                raise RecoveryExecutionError("UNIT_TEST_RETRY_BINDING_DRIFT", "单测进度与原节点入口的范围或 Build 绑定不一致。")
        restored.update({
            key: deepcopy(value) for key, value in progress.items()
            if key.startswith("unit_test_") or key.startswith("small_task_")
            or key in {"repair_tasks", "code_changes", "code_change_sets"}
        })
    if node_name == "unit_test_repair":
        tasks = restored.get("small_task_tasks") or restored.get("repair_tasks") or []
        # SmallTask 已会跳过 completed；未完成任务恢复为 pending，失败重试重新计费。
        tasks = [
            {**deepcopy(task), "status": "completed" if task.get("status") in {"completed", "already_satisfied"} else "pending"}
            for task in tasks if isinstance(task, dict)
        ]
        restored.update({"small_task_tasks": tasks, "repair_tasks": tasks})
        if progress is not None:
            restored["unit_test_repair_charged_checks"] = []
    # 不沿用旧通过标记；已有测试由生成器按源码摘要校验缓存，命令仍真实复测。
    restored.update({
        "status": "in_progress", "error": None, "clarification": {},
        "unit_test_gate_passed": False, "unit_test_quality_gate_passed": False,
        "unit_test_next_action": "", "test_phase_confirmation": {},
    })
    if node_name == "unit_test":
        restored.update({"unit_test_results": [], "unit_test_report": {}, "unit_test_generation": {}})
    from app.graph.nodes.workspace_inspection import inspect_workspace

    scanned = inspect_workspace(restored)
    fields = {key: value for key, value in scanned.items() if key.startswith("workspace_")}
    return {**restored, "workspace_snapshot": {}, **fields}, fields
