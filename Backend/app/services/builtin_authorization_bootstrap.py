"""Builtin 权限四表首次初始化：先只读核验，再一次受理，未知结果禁止重发。"""

import re
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.entity_tables import OperationReceipt, TableReadiness
from app.services.application_config import read_application_config
from app.services.authorization_bootstrap import authorization_bootstrap_enabled
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.authorization_workspace_projector import ensure_projection, read_record
from app.services.builtin_authorization_state import builtin_binding, builtin_lock, save_state, schema_ready
from app.services.managed_database_client import ManagedDatabaseRegistrationError
from app.services.managed_table_client import ManagedTableClient


def _result(record: dict, status: str, message: str, **extra) -> dict:
    """仅向 AG-UI 暴露安全摘要，不把远程 SQL 或内部异常公开。"""

    return {"status": status, "executor": "builtin_saas", "datasource_mode": "builtin",
            "schema_ready": status in {"executed", "reused"} and record.get("schemaReady") is True, "seed_ready": False,
            "operation_id": record.get("operationId"),
            "operation_status": record.get("status", "unknown").upper(),
            "requires_manual_action": status in {"failed", "manual_required"},
            "message": message, **extra}


def _check(remote: ManagedTableClient, binding: dict, compiled: dict) -> TableReadiness:
    """要求四个目标与应用绑定精确匹配，不能用单纯表存在代替归属结构检查。"""

    check = TableReadiness.model_validate(remote.request(binding["datasourceKey"], "tables/check", body={"sqls": compiled["sqls"]}))
    # SaaS 在资源占用期间合法返回空逐表结果，不能将执行中误判为响应损坏。
    if not check.ready and not check.tables and check.operationStatus in {"RUNNING", "UNKNOWN"} and check.datasourceId == binding["datasourceId"]:
        return check
    if check.datasourceId != binding["datasourceId"] or len(check.tables) != 4 or {item.tableName for item in check.tables} != set(compiled["tables"]):
        raise ValueError("权限表检查结果与当前四表绑定不符。")
    if check.ready and any(item.status != "SYNCED" for item in check.tables):
        raise ValueError("权限表结构检查结果不完整。")
    return check


def _operation(raw: object, targets: list[str], operation_id: str | None = None) -> dict:
    """校验批次身份和状态，只保存逐表状态，不持久化原始执行异常。"""

    if (not isinstance(raw, dict) or not isinstance(raw.get("operationId"), str)
            or not re.fullmatch(r"[1-9][0-9]*", raw["operationId"])):
        raise ValueError("权限批次操作编号无效。")
    if (raw.get("mode") != "CREATE_BATCH" or raw.get("status") not in {"RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"}
            or not isinstance(raw.get("tableNames"), list) or len(raw["tableNames"]) != 4 or set(raw["tableNames"]) != set(targets)
            or operation_id is not None and raw["operationId"] != operation_id):
        raise ValueError("权限批次详情与当前四表不符。")
    execution = raw.get("executionResult")
    if not isinstance(execution, dict) or not isinstance(execution.get("steps"), list) or len(execution["steps"]) > 4:
        raise ValueError("权限批次执行结果不符合当前操作详情合同。")
    steps = []
    for step in execution["steps"]:
        if not isinstance(step, dict) or step.get("tableName") not in targets or step.get("status") not in {"RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN", "PENDING", "SKIPPED"}:
            raise ValueError("权限批次逐表结果无效。")
        steps.append({"tableName": step["tableName"], "status": step["status"]})
    return {"operationId": raw["operationId"], "status": raw["status"], "steps": steps}


def _ready(root: Path, record: dict) -> dict:
    """只有远端完整结构核验后记录 Schema 就绪，明确 Seed 未执行。"""

    binding = builtin_binding(read_application_config(root))
    compiled = compile_authorization_tables(root, binding["datasourceKey"])
    if (any(record.get(key) != value for key, value in binding.items())
            or compiled["ddlFingerprint"] != record["ddlFingerprint"]):
        raise ValueError("权限表检查期间数据库绑定或 DDL 已变化。")
    record.update(status="schema_ready", schemaReady=True, seedReady=False, verifiedAt=datetime.now(UTC).isoformat())
    save_state(root, record)
    return _result(record, "executed", "权限数据库初始化已就绪。", initialized_tables=compiled["tables"])


def run_builtin_authorization_bootstrap(workspace: str, technical_plan: dict, *, client: ManagedTableClient | None = None, explicit_recovery: bool = False) -> dict:
    """读取权威配置并串行执行；普通 Build 仅自动提交一次，显式恢复才允许明确失败重试。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root):
        record = {}
        stage = "binding"
        try:
            application = read_application_config(root)
            binding = builtin_binding(application)
            if not authorization_bootstrap_enabled(technical_plan, application_config=application):
                raise ValueError("权限未启用或 TechnicalPlan 尚未确认。")
            stage = "ddl"
            compiled = compile_authorization_tables(root, binding["datasourceKey"])
            stage = "state"
            record = read_record(root, "state.json")
            if record and any(record.get(key) != value for key, value in binding.items()):
                raise ValueError("模拟数据库绑定已改变，请人工核对并确认新绑定初始化。")
            if record and record.get("ddlFingerprint") != compiled["ddlFingerprint"]:
                raise ValueError("权限 DDL 已变化，请人工核对；不会自动重建已有表。")
            stage = "projection"
            ensure_projection(root, binding["datasourceKey"])
            if schema_ready(root, application):
                return _result(record, "reused", "权限数据库初始化已就绪（复用已有结果）。", initialized_tables=compiled["tables"])
            record = record or {"schemaVersion": 1, "datasourceMode": "builtin", **binding,
                                "ddlFingerprint": compiled["ddlFingerprint"], "schemaReady": False, "seedReady": False}
            remote = client or ManagedTableClient(Settings.from_env())
            stage = "remote"
            return _run_remote(root, record, binding, compiled, remote, explicit_recovery)
        except (OSError, ValueError, ManagedDatabaseRegistrationError):
            # 外部 HTTP/模型错误均不公开正文；提交前已持久化 submitting，重启亦不会重发。
            reason = {
                "binding": "请核对 Builtin 模式、REGISTERED 绑定、权限开关及已确认 TechnicalPlan。",
                "ddl": "请核对四条完整 CREATE TABLE，以及 role_resource、role_member 的显式 id 自增主键。",
                "state": "本地状态、数据库绑定或 DDL 已变化，请人工核对；不会自动迁移或重建。",
                "projection": "七个权限受管文件缺失、存在未知修改或缺少模板安装来源摘要，请解决冲突。",
                "remote": "请核对 SaaS 配置及原操作编号和逐表结果；未确认结果前不会重复建表。",
            }[stage]
            return _result(record, "manual_required", "权限表初始化待核对：" + reason,
                           failure_category=f"builtin_authorization_{stage}_unconfirmed")


def _run_remote(root: Path, record: dict, binding: dict, compiled: dict, remote: ManagedTableClient, explicit_recovery: bool) -> dict:
    """编排 SaaS 只读检查、持久受理和单次有界查询，跨重启复用同一操作。"""

    key = binding["datasourceKey"]
    # 已有操作先读原操作终态，执行中不做结构核验，也绝不再次提交。
    if record.get("operationId"):
        result = _query_operation(root, record, binding, compiled, remote)
        if record.get("status") != "failed" or not explicit_recovery:
            return result
    check = _check(remote, binding, compiled)
    # 即使本地受理结果丢失，远端仍可通过完整归属和结构证明已就绪。
    if not record.get("operationId") and check.ready and check.operationStatus not in {"RUNNING", "UNKNOWN"}:
        return _ready(root, record)
    if not record.get("operationId") and record.get("status") in {"submitting", "unknown"}:
        # 不能把较早的失败历史误认成本次丢失的受理结果；只有完整结构核验可解除未知保护。
        return _result(record, "manual_required", "原权限批次受理结果未知，请人工核对；不会再次提交。")
    # 新工作区也先查历史。任何在途/未知资源操作均禁止新建，已失败批次需显式恢复。
    history = remote.request(key, "table-operations?limit=100")
    if not isinstance(history, list) or len(history) > 100:
        raise ValueError("权限初始化操作记录不符合合同。")
    for item in history:
        if not isinstance(item, dict) or item.get("status") not in {"RUNNING", "UNKNOWN", "SUCCEEDED", "FAILED"}:
            raise ValueError("权限初始化操作记录状态无效。")
        if item["status"] in {"RUNNING", "UNKNOWN"}:
            if item.get("mode") == "CREATE_BATCH" and set(item.get("tableNames") or []) == set(compiled["tables"]):
                operation = _operation(item, compiled["tables"])
                record.update(operationId=operation["operationId"], status=operation["status"].lower(), steps=operation["steps"])
                save_state(root, record)
            return _result(record, "manual_required", "数据库存在执行中或待核对操作，请恢复原操作，不能重复建表。")
        if item.get("mode") == "CREATE_BATCH" and set(item.get("tableNames") or []) == set(compiled["tables"]) and not explicit_recovery:
            operation = _operation(item, compiled["tables"])
            record.update(operationId=operation["operationId"], status=operation["status"].lower(), steps=operation["steps"])
            save_state(root, record)
            return _result(record, "manual_required", "已有权限批次未通过结构检查，请显式恢复或人工核对。")
    if record.get("status") in {"submitting", "unknown", "running"}:
        return _result(record, "manual_required", "原权限批次受理结果未知，请人工核对；不会再次提交。")
    if record.get("status") == "failed" and not explicit_recovery:
        return _result(record, "failed", "权限批次失败，请核对逐表结果后显式恢复。")
    if check.operationStatus in {"RUNNING", "UNKNOWN"} or any(table.status not in {"NOT_CREATED", "SYNCED"} for table in check.tables):
        return _result(record, "manual_required", "权限表归属、结构或在途操作未确认，不允许 CREATE/REBUILD。")
    # 先写未知保护再 POST：进程在收到 202 前退出也不会触发第二次提交。
    if builtin_binding(read_application_config(root)) != binding:
        raise ValueError("提交前应用数据库绑定已变化。")
    if (read_application_config(root).get("authorization") or {}).get("enabled") is not True:
        raise ValueError("提交前应用权限已关闭。")
    record.update(status="submitting")
    if record.get("operationId"):
        # 新提交前隔离上次明确失败 ID，避免新响应丢失后误查询旧失败并再次重放。
        record["previousOperationId"] = record.pop("operationId")
    save_state(root, record)
    try:
        receipt = OperationReceipt.model_validate(remote.batch_create_tables(key, compiled["sqls"]))
    except (ValueError, ManagedDatabaseRegistrationError):
        record.update(status="unknown")
        save_state(root, record)
        return _result(record, "manual_required", "权限批次受理结果未知，请核对原操作；不会自动重发。")
    record.update(operationId=receipt.operationId, status="running")
    save_state(root, record)
    return _result(record, "manual_required", "权限建表请求已受理，请点击“刷新执行状态”查看结果。", requires_manual_action=False)


def _query_operation(root: Path, record: dict, binding: dict, compiled: dict, remote: ManagedTableClient) -> dict:
    """仅查询原批次一次，成功后核验结构；不会轮询或提交 SQL。"""

    operation_id = record["operationId"]
    operation = _operation(remote.request(binding["datasourceKey"], f"table-operations/{operation_id}"), compiled["tables"], operation_id)
    record.update(status=operation["status"].lower(), steps=operation["steps"])
    save_state(root, record)
    if operation["status"] == "SUCCEEDED":
        verified = _check(remote, binding, compiled)
        if verified.ready and verified.operationStatus not in {"RUNNING", "UNKNOWN"}:
            return _ready(root, record)
        return _result(record, "manual_required", "权限批次已成功，但四表结构尚未全部确认，请刷新或核对。")
    if operation["status"] == "RUNNING":
        return _result(record, "manual_required", "权限表正在创建，请稍后手动刷新执行状态。", requires_manual_action=False)
    return _result(record, "manual_required", "权限批次失败或结果未知，请核对原操作；刷新不会重复建表。")


def refresh_builtin_authorization(workspace: str, operation_id: str, *, client: ManagedTableClient | None = None) -> dict:
    """按本地绑定和指定原操作刷新状态，仅允许写入核验事实，禁止建表或推进工作流。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root):
        binding = builtin_binding(read_application_config(root))
        compiled = compile_authorization_tables(root, binding["datasourceKey"])
        record = read_record(root, "state.json")
        if (record.get("operationId") != operation_id
                or any(record.get(key) != value for key, value in binding.items())
                or record.get("ddlFingerprint") != compiled["ddlFingerprint"]):
            raise ValueError("原操作编号、数据库绑定或 DDL 已变化，请核对当前应用。")
        return _query_operation(root, record, binding, compiled, client or ManagedTableClient(Settings.from_env()))


def ensure_builtin_build_projection(state: dict) -> None:
    """仅对已启用权限的 Builtin Build 做本地适配，绝不访问 SaaS 或执行 SQL。"""

    root = Path(state.get("workspace") or "").expanduser().resolve()
    if not state.get("workspace") or not (root / WORKSPACE_ARTIFACT_DIR / "application.json").is_file():
        return
    application = read_application_config(root)
    from app.services.builtin_authorization_state import uses_builtin_database
    if not uses_builtin_database(application) or (application.get("authorization") or {}).get("enabled") is not True:
        return
    with builtin_lock(root):
        binding = builtin_binding(application)
        record = read_record(root, "state.json")
        compiled = compile_authorization_tables(root, binding["datasourceKey"])
        if record and (any(record.get(key) != value for key, value in binding.items()) or record.get("ddlFingerprint") != compiled["ddlFingerprint"]):
            raise ValueError("Builtin 权限绑定或 DDL 已变化，请人工核对。")
        ensure_projection(root, binding["datasourceKey"])
