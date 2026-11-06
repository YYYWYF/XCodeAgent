"""Builtin 权限初始化的本地身份、互斥锁和只读成功判定。"""

from pathlib import Path
import re
from threading import Lock, RLock

from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.authorization_workspace_projector import RUNTIME, read_record
from app.utils.atomic_json import atomic_write_json

_LOCKS: dict[str, RLock] = {}
_GUARD = Lock()


def builtin_lock(root: Path) -> RLock:
    """串行化同一工作区的初始化及后续本地投影。"""

    with _GUARD:
        return _LOCKS.setdefault(str(root), RLock())


def uses_builtin_database(application: dict) -> bool:
    """只读取当前应用权威数据库模式，不按规划或历史状态推断。"""

    source = application.get("datasource")
    db = source.get("db") if isinstance(source, dict) else None
    return isinstance(source, dict) and source.get("type") == "database" and isinstance(db, dict) and db.get("useBuiltin") is True


def builtin_binding(application: dict) -> dict:
    """严格复核内置模式及 REGISTERED 完整绑定，不回退通用数据库。"""

    if not uses_builtin_database(application):
        raise ValueError("当前应用不是 Builtin 模拟数据库。")
    binding = ManagedDatabaseRegistration.model_validate(application.get("managedDatabase") or {})
    if binding.status != "REGISTERED":
        raise ValueError("模拟数据库尚未 REGISTERED，不能初始化权限表。")
    return {"datasourceKey": binding.datasource_key, "datasourceId": binding.datasource_id}


def schema_ready(root: Path, application: dict) -> bool:
    """路由只读可信本地成功事实，不触发网络、文件投影或重新 CREATE。"""

    try:
        binding = builtin_binding(application)
        record = read_record(root, "state.json")
        return (all(record.get(key) == value for key, value in binding.items())
                and record.get("schemaVersion") == 1 and record.get("datasourceMode") == "builtin"
                and record.get("status") == "schema_ready" and record.get("schemaReady") is True
                and isinstance(record.get("ddlFingerprint"), str)
                and bool(record.get("verifiedAt")))
    except (OSError, ValueError):
        return False


def save_state(root: Path, record: dict) -> None:
    """原子持久化非敏感事实；状态损坏或落盘失败不允许远程重放。"""

    read_record(root, "state.json")
    atomic_write_json(root / RUNTIME / "state.json", record)


def verified_seed(seed: object, compiled: dict) -> bool:
    """只接受已核验原操作及精确匹配的本地 Seed 身份。"""

    return (isinstance(seed, dict) and seed.get("status") == "seed_ready"
            and seed.get("operationStatus") == "SUCCEEDED" and seed.get("ready") is True
            and isinstance(seed.get("operationId"), str)
            and bool(re.fullmatch(r"[1-9][0-9]{0,18}", seed["operationId"]))
            and isinstance(seed.get("verifiedAt"), str) and bool(seed["verifiedAt"])
            and seed.get("seedFingerprint") == compiled["seedFingerprint"]
            and seed.get("idempotencyKey") == compiled["idempotencyKey"])


def seed_ready(root: Path, application: dict, technical_plan: dict) -> bool:
    """只读复核绑定、DDL 和本次实际行数据，失败时不推导权限运行态就绪。"""

    from app.services.authorization_table_projection import compile_authorization_tables
    from app.services.builtin_authorization_seed import compile_builtin_authorization_seed, read_confirmed_seed_plan

    try:
        if not schema_ready(root, application):
            return False
        record = read_record(root, "state.json")
        key = builtin_binding(application)["datasourceKey"]
        current_plan = read_confirmed_seed_plan(root)
        if (technical_plan.get("artifact_type") != "technical-plan"
                or technical_plan.get("confirmation_status") != "confirmed"
                or technical_plan.get("authorization_manifest") != current_plan.get("authorization_manifest")):
            return False
        compiled = compile_builtin_authorization_seed(application, current_plan, datasource_key=key)
        return (record.get("ddlFingerprint") == compile_authorization_tables(root, key)["ddlFingerprint"]
                and verified_seed(record.get("seed"), compiled))
    except (OSError, ValueError, TypeError):
        return False
