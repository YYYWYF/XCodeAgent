"""从 Builtin 权限本地事实投影预览提醒。"""

from pathlib import Path

from app.services.authorization_table_projection import compile_authorization_tables
from app.services.builtin_authorization_seed import read_confirmed_seed_plan
from app.services.builtin_authorization_state import builtin_binding, schema_ready, seed_ready, uses_builtin_database
from app.services.builtin_authorization_task_store import AuthorizationTask, read_operation_record, read_task

MESSAGES = {
    "pending": "权限数据尚未初始化，权限相关功能暂不可正常使用。请在「权限数据库初始化」卡查看当前状态。",
    "running": "权限数据仍在初始化，权限相关功能暂不可正常使用。请在「权限数据库初始化」卡查看进度。",
    "needs_attention": "权限初始化尚未完成，权限相关功能暂不可正常使用。请在「权限数据库初始化」卡查看详情并完成后续处理。",
    "unknown": "暂时无法确认权限初始化状态，权限相关功能可能无法正常使用。请在「权限数据库初始化」卡刷新状态。",
}


def _advisory(status: str, schema: bool = False) -> dict:
    """只发布固定字段和文案，不投影原操作数据。"""

    ready = status == "ready"
    return {
        "applicable": True,
        "status": status,
        "schemaReady": schema,
        "seedReady": ready,
        "severity": "warning",
        "title": "权限功能尚未就绪",
        "message": MESSAGES.get(status, ""),
        "nextAction": "open_authorization_status",
    }


def _pending_status(record: dict, task: AuthorizationTask | None) -> str:
    """优先提示需人工处理的事实，再区分当前执行与尚未提交。"""

    task_status = task.status if task else None
    operation_status = record.get("status")
    seed_status = (record.get("seed") or {}).get("status")
    if (task and (task.operationStateExpected and not record or task.seedStateExpected and "seed" not in record)):
        return "unknown"
    if (task_status in {"failed", "needs_attention", "cancelled", "ready"}
            or seed_status in {"failed", "unknown", "seed_ready"}
            or operation_status in {"failed", "unknown"}):
        return "needs_attention"
    if (task_status in {"running", "waiting"} or seed_status in {"submitting", "running", "succeeded"}
            or operation_status in {"submitting", "running", "succeeded"}):
        return "running"
    return "pending" if task or record else "unknown"


def get_preview_authorization_advisory(root: Path) -> dict | None:
    """动态读取权限初始化摘要，读取异常仅影响提示。"""

    from app.services.application_config import read_application_config

    schema = False
    try:
        application = read_application_config(root)
        if not uses_builtin_database(application) or application["authorization"]["enabled"] is not True:
            return None
        record = read_operation_record(root)
        if record:
            binding = builtin_binding(application)
            if any(record.get(key) != value for key, value in binding.items()):
                return _advisory("needs_attention")
            schema = (schema_ready(root, application) and record["ddlFingerprint"]
                      == compile_authorization_tables(root, binding["datasourceKey"])["ddlFingerprint"])
            if record["status"] == "schema_ready" and not schema:
                return _advisory("needs_attention")
        seed = record.get("seed") or {}
        if schema and seed.get("status") == "seed_ready" and seed_ready(root, application, read_confirmed_seed_plan(root)):
            return _advisory("ready", True)
        return _advisory(_pending_status(record, read_task(root)), schema)
    except Exception:
        # 状态投影与进程启动、健康探测及修复结果独立。
        return _advisory("unknown", schema)
