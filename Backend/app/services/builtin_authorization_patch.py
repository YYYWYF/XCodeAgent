"""独立调试补丁的分阶段操作和可重新读取的安全结果。"""

from pathlib import Path

from app.services.builtin_authorization_task_store import task_transaction

from app.services.application_config import read_application_config
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.authorization_workspace_projector import read_record
from app.services.builtin_authorization_bootstrap import refresh_builtin_authorization
from app.services.builtin_authorization_seed import read_confirmed_seed_plan
from app.services.builtin_authorization_seed_runtime import refresh_builtin_authorization_seed, run_builtin_authorization_seed
from app.services.builtin_authorization_state import builtin_binding, builtin_lock, schema_ready


def _query_patch_seed(workspace: str) -> dict:
    """复用已核验 Schema 只读查询原 Seed 幂等键，不允许初始化提交。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root), task_transaction(root):
        record = read_record(root, "state.json")
        application = read_application_config(root)
        binding = builtin_binding(application)
        if not schema_ready(root, application):
            raise ValueError("权限表状态已变化，请重新检查。")
        tables = compile_authorization_tables(root, binding["datasourceKey"])["tables"]
        return run_builtin_authorization_seed(root, record, tables, read_confirmed_seed_plan(root), query_only=True)


def refresh_patch(workspace: str) -> dict:
    """只查询已有操作，缺少操作编号时仅允许按已落盘的 Seed 身份核对。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root), task_transaction(root):
        record = read_record(root, "state.json")
        seed = record.get("seed")
        if isinstance(seed, dict):
            if seed.get("operationId"):
                return refresh_builtin_authorization_seed(str(root), seed["operationId"])
            # 刷新显式使用只读模式，不能因按键未找到而触发同键提交。
            return _query_patch_seed(str(root))
        if record.get("operationId"):
            return refresh_builtin_authorization(str(root), record["operationId"])
        raise ValueError("没有可查询的原操作，请核对数据库状态。")


def patch_view(workspace: str, result: dict | None = None) -> dict | None:
    """读取统一编排投影；人工查询仅更新原操作事实，不能推进后续 POST。"""

    from app.services.builtin_authorization_orchestrator import get_builtin_authorization_task

    view = get_builtin_authorization_task(workspace)
    if view is not None and result and not result.get("seed_ready") and result.get("failure_category"):
        view = {**view, "status": "needs_attention", "failureCategory": result["failure_category"],
                "message": "原操作结果待核对，请查看详情。", "nextAction": None}
    return view
