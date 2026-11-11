"""在四表 Schema 之上串行受理和核验独立的权限 Seed 操作。"""

from datetime import UTC, datetime
from pathlib import Path

from app.services.builtin_authorization_task_store import task_transaction

from app.config import Settings
from app.services.application_config import read_application_config
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.authorization_workspace_projector import read_record
from app.services.builtin_authorization_seed import (
    SeedContractError, compile_builtin_authorization_seed, read_confirmed_seed_plan,
    seed_request_fingerprint, validate_seed_transport,
)
from app.services.builtin_authorization_seed_client import ManagedAuthorizationSeedClient, SeedDetail, SeedRemoteError
from app.services.builtin_authorization_state import builtin_binding, builtin_lock, save_state, schema_ready, verified_seed
from app.workspace.task_documents import load_confirmed_build_task_plan

_SUBMIT_REJECTIONS = {
    "seed_idempotency_conflict", "seed_resource_busy",
    *(f"seed_http_{code}" for code in (400, 401, 403, 404, 409, 413, 422, 429)),
}


def _summary(record: dict, tables: list[str], message: str, *, status: str = "manual_required",
             ready: bool = False, next_action: str = "review", failure: str | None = None) -> dict:
    """投影两个阶段的独立安全摘要，不公开请求指纹、载荷或管理员列表。"""

    seed = record.get("seed")
    seed = seed if isinstance(seed, dict) else {}
    return {"status": status, "executor": "builtin_saas", "datasource_mode": "builtin",
            "schema_ready": True, "seed_ready": ready, "operation_id": record.get("operationId"),
            "operation_status": "SCHEMA_READY", "initialized_tables": tables,
            "seed_operation_id": seed.get("operationId"), "seed_operation_status": seed.get("operationStatus"),
            "next_action": next_action, "failure_category": failure,
            "requires_manual_action": next_action in {"continue", "lookup_seed", "review"}, "message": message}


def _current_inputs(root: Path, record: dict) -> tuple[dict, dict, dict, dict]:
    """读取正式配置及规划，要求 Schema、绑定和 DDL 仍然有效。"""

    application = read_application_config(root)
    binding = builtin_binding(application)
    schema = compile_authorization_tables(root, binding["datasourceKey"])
    if (not schema_ready(root, application) or any(record.get(field) != value for field, value in binding.items())
            or record.get("ddlFingerprint") != schema["ddlFingerprint"]):
        raise ValueError("权限 Seed 对应的 Schema 或数据库绑定已变化。")
    plan = read_confirmed_seed_plan(root)
    compiled = compile_builtin_authorization_seed(application, plan, datasource_key=binding["datasourceKey"])
    return binding, schema, plan, compiled


def _verify_current(root: Path, record: dict, binding: dict, plan: dict, compiled: dict) -> None:
    """在网络边界复核正式 Manifest、实际行及原 Schema 状态。"""

    from app.services.builtin_authorization_task_store import check_task_inputs

    check_task_inputs()
    try:
        current_binding, _, current_plan, current_compiled = _current_inputs(root, record)
        if (current_binding == binding and current_compiled["seedFingerprint"] == compiled["seedFingerprint"]
                and current_plan["authorization_manifest"]["fingerprint"] == plan["authorization_manifest"]["fingerprint"]
                and read_record(root, "state.json") == record):
            return
    except (OSError, ValueError, TypeError):
        pass
    raise SeedRemoteError("seed_inputs_changed_during_operation")


def _save_detail(root: Path, record: dict, seed: dict, binding: dict,
                 compiled: dict, detail: SeedDetail) -> dict:
    """只持久化匹配原身份、四表和实际请求指纹的 GET 结果。"""

    request = compiled["request"]
    key = binding["datasourceKey"]
    tables = [table["tableName"] for table in request["tables"]]
    row_count = sum(len(table["rows"]) for table in request["tables"])
    total = detail.createdCount + detail.existingCount
    ready = detail.status == "SUCCEEDED"
    if (detail.datasourceKey != key or detail.requestFingerprint != seed_request_fingerprint(key, request)
            or set(detail.tableNames) != set(tables)
            or seed.get("operationId", detail.operationId) != detail.operationId):
        raise SeedRemoteError("seed_operation_identity_mismatch")
    if (detail.verified != ready or (ready and (total != row_count or detail.errorCode is not None))
            or (not ready and total != 0)):
        raise SeedRemoteError("seed_invalid_verification")
    next_seed = {**seed, "operationId": detail.operationId, "operationStatus": detail.status,
                 "status": "seed_ready" if ready else detail.status.lower(), "ready": ready,
                 "createdCount": detail.createdCount, "existingCount": detail.existingCount}
    next_seed.pop("failureCategory", None)
    next_seed.pop("verifiedAt", None)
    if ready:
        next_seed["verifiedAt"] = datetime.now(UTC).isoformat()
    save_state(root, {**record, "seed": next_seed})
    updated = {**record, "seed": next_seed}
    if ready:
        return _summary(updated, tables, "权限表和权限数据均已核验就绪。", status="executed", ready=True, next_action="none")
    if detail.status == "RUNNING":
        return _summary(updated, tables, "权限数据正在写入，请手动刷新原操作。", next_action="refresh_seed")
    return _summary(updated, tables, "原权限数据操作失败或结果未知，请人工核对原操作。",
                    failure=f"seed_operation_{detail.status.lower()}")


def run_builtin_authorization_seed(root: Path, record: dict, tables: list[str], technical_plan: dict | None = None,
                                   *, client: ManagedAuthorizationSeedClient | None = None,
                                   refresh_id: str | None = None, query_only: bool = False) -> dict:
    """在调用方锁内协调原操作，明确查无记录时按原键和原载荷幂等提交。"""

    stage = "seed_input_invalid"
    seed = record.get("seed")
    read_only = query_only or refresh_id is not None
    try:
        binding, schema, plan, compiled = _current_inputs(root, record)
        key = binding["datasourceKey"]
        if tables != schema["tables"]:
            raise ValueError("权限 Seed 四表与当前 Schema 不符。")
        if technical_plan is not None and technical_plan.get("authorization_manifest") != plan["authorization_manifest"]:
            raise ValueError("当前工作流的 Manifest 已失效。")
        identity = {field: compiled[field] for field in ("seedFingerprint", "idempotencyKey")}
        if "seed" in record:
            if not isinstance(seed, dict) or any(not isinstance(seed.get(field), str) for field in identity):
                raise ValueError("本地 Seed 状态无效。")
            if seed["seedFingerprint"] != identity["seedFingerprint"]:
                if refresh_id is not None:
                    raise SeedRemoteError("seed_input_changed")
                return _summary(record, tables, "权限数据输入已变化，请人工核对增量初始化，原操作保持不变。",
                                failure="seed_input_changed")
            if seed["idempotencyKey"] != identity["idempotencyKey"] or seed.get("status") not in {
                "submitting", "running", "succeeded", "unknown", "failed", "seed_ready"
            }:
                raise ValueError("本地 Seed 操作身份无效。")
            if ("operationId" in seed and not isinstance(seed["operationId"], str)
                    or seed["status"] == "seed_ready" and not verified_seed(seed, compiled)):
                raise ValueError("本地 Seed 操作编号或核验状态无效。")
            if refresh_id is None and verified_seed(seed, compiled):
                return _summary(record, tables, "权限表和权限数据已就绪，复用原核验结果。",
                                status="reused", ready=True, next_action="none")

        if refresh_id is not None and (not seed or seed.get("operationId") != refresh_id):
            raise SeedRemoteError("seed_invalid_identity")

        request = compiled["request"]
        validate_seed_transport(request)
        # 所有可提交路径都要求正式 DAG；纯查询动作继续保持只读。
        if not read_only:
            dag = load_confirmed_build_task_plan(root)
            if not dag or dag.get("status") != "ready":
                raise ValueError("权限 Seed 需要当前已确认 Build DAG。")
        stage = "seed_client_configuration"
        remote = client or ManagedAuthorizationSeedClient(Settings.from_env())
        stage = "seed_query_unconfirmed"
        _verify_current(root, record, binding, plan, compiled)
        operation_id = seed.get("operationId") if seed else None
        detail = remote.query(key, operation_id=operation_id,
                              idempotency_key=identity["idempotencyKey"])
        _verify_current(root, record, binding, plan, compiled)
        if detail is None and operation_id is not None and not read_only:
            # 原 ID 丢失不能直接触发提交，必须再按原幂等键核对远端事实。
            detail = remote.query(key, idempotency_key=identity["idempotencyKey"])
            _verify_current(root, record, binding, plan, compiled)
        if detail is not None:
            stage = "seed_state_write_failed"
            return _save_detail(root, record, seed or identity, binding, compiled, detail)
        if read_only:
            return _summary(record, tables, "未查到原权限数据操作，请重试初始化以按原幂等键协调。",
                            next_action="lookup_seed", failure="seed_operation_not_found")
        # 本地 submitting/unknown 仅记录进度；SaaS 在资源锁和唯一键下保证同键同载荷只执行一次。
        # 保留已有受理身份，避免重新提交的回执静默替换曾经确认的原操作 ID。
        stage = "seed_state_write_failed"
        marker = {**(seed or {}), **identity, "status": "submitting", "ready": False}
        marker.pop("failureCategory", None)
        submitting = {**record, "seed": marker}
        save_state(root, submitting)
        record = submitting
        seed = marker
        _verify_current(root, record, binding, plan, compiled)
        stage = "seed_submit_unconfirmed"
        try:
            receipt = remote.submit(key, request)
            if operation_id is not None and receipt.operationId != operation_id:
                raise SeedRemoteError("seed_operation_identity_mismatch")
        except SeedRemoteError as error:
            _verify_current(root, record, binding, plan, compiled)
            # 明确拒绝记录本次失败；传输和回执异常仅记录受理结果未确认。
            status = "failed" if error.category in _SUBMIT_REJECTIONS else "unknown"
            seed = {**marker, "status": status, "failureCategory": error.category}
            updated = {**record, "seed": seed}
            save_state(root, updated)
            record = updated
            raise
        _verify_current(root, record, binding, plan, compiled)
        stage = "seed_state_write_failed"
        updated = {**record, "seed": {**marker, "operationId": receipt.operationId,
                                      "operationStatus": receipt.status, "status": receipt.status.lower()}}
        save_state(root, updated)
        return _summary(updated, tables, "权限数据请求已受理，请刷新原操作以核验结果。", next_action="refresh_seed")
    except SeedContractError as error:
        return _summary(record, tables, str(error), failure="seed_saas_contract_incompatible")
    except (OSError, ValueError, TypeError) as error:
        failure = error.category if isinstance(error, SeedRemoteError) else stage
        stale = failure in {"seed_input_changed", "seed_inputs_changed_during_operation", "seed_invalid_identity"}
        if refresh_id is not None and stale:
            raise ValueError("权限数据原操作与当前应用不符。") from None
        if stale and failure == "seed_inputs_changed_during_operation":
            return {**_summary({}, [], "权限初始化期间应用输入已变化，请核对原操作。", failure=failure),
                    "schema_ready": False}
        next_action = "review"
        if isinstance(seed, dict) and seed.get("operationId") and failure in {
            "seed_timeout", "seed_network_error", "seed_http_500", "seed_http_503", "seed_state_write_failed"
        }:
            next_action = "refresh_seed"
        if isinstance(seed, dict) and "operationId" not in seed and seed.get("status") in {"submitting", "unknown"}:
            next_action = "lookup_seed"
        return {**_summary(record, tables, "权限数据初始化待核对，请检查当前输入或查询原操作。",
                           next_action=next_action, failure=failure), "failure_phase": stage}


def refresh_builtin_authorization_seed(workspace: str, operation_id: str,
                                       *, client: ManagedAuthorizationSeedClient | None = None) -> dict:
    """严格按持久化 Seed ID 单次查询，不执行 Schema 查询或 Seed POST。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root), task_transaction(root):
        record = read_record(root, "state.json")
        seed = record.get("seed")
        if not isinstance(seed, dict) or seed.get("operationId") != operation_id:
            raise ValueError("Seed 操作编号与当前工作区不符。")
        _, schema, _, _ = _current_inputs(root, record)
        return run_builtin_authorization_seed(root, record, schema["tables"], client=client, refresh_id=operation_id)
