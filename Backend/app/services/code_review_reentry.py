"""代码审查重入时刷新工作区事实，并保留经验证的审查与修复上下文。"""

from copy import deepcopy
from typing import Any

from app.domain.execution_recovery import RecoveryExecutionError
from app.services.node_recovery_context import current_node_recovery_context


def prepare_code_review_reentry(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """只处理 Native Recovery 首次目标调用，普通扫描和修复循环不重置。"""

    recovery = current_node_recovery_context()
    if recovery is None:
        return state, {}
    restored = dict(state)
    progress = recovery.internal_progress
    if progress is not None:
        # 进度由 checkpoint 证明归属；不得改换用户选择的审查方式或 Build 范围。
        for key in ("code_review_mode", "build_execution_scope", "build_run_id", "build_run_plan_path", "build_run_plan_sha256"):
            if progress.get(key) != state.get(key):
                raise RecoveryExecutionError("CODE_REVIEW_RETRY_BINDING_DRIFT", "审查进度与节点入口的审查方式、范围或 Build 绑定不一致。")
        restored.update({
            key: deepcopy(value) for key, value in progress.items()
            if key.startswith("code_review_") or key in {"code_changes", "code_change_sets"}
        })
    # 修复确认必须来自原入口，不能由失败进度补造确认后跳过用户选择。
    restored["code_review_repair_confirmation"] = deepcopy(state.get("code_review_repair_confirmation") or {})
    restored.update({
        "status": "in_progress", "error": None, "clarification": {},
        "code_review_retry": {}, "code_review_next_action": "",
        "acceptance_phase_confirmation": {},
    })
    from app.graph.nodes.workspace_inspection import inspect_workspace

    scanned = inspect_workspace(restored)
    fields = {key: value for key, value in scanned.items() if key.startswith("workspace_")}
    return {**restored, "workspace_snapshot": {}, **fields}, fields
