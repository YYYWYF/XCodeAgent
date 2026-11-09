"""受生命周期管理的 Builtin Schema → Seed 自动编排，未知结果永不重放。"""

import hashlib
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import portalocker

from app.services.application_config import read_application_config
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.builtin_authorization_bootstrap import run_builtin_authorization_bootstrap
from app.services.builtin_authorization_seed import canonical_json, compile_builtin_authorization_seed, read_confirmed_seed_plan
from app.services.builtin_authorization_state import builtin_binding, builtin_lock, save_state, schema_ready, seed_ready
from app.services.builtin_authorization_task_store import (
    AuthorizationTask, task_input_scope, read_task, save_task, task_transaction, read_operation_record,
)
from app.workspace.task_documents import load_confirmed_build_task_plan

_registry_lock = threading.RLock()
_workers: dict[str, tuple[threading.Event, Future]] = {}
_blocked: set[str] = set()
_executor: ThreadPoolExecutor | None = None
_closed = False
ACTIVE = {"pending", "running", "waiting"}
BACKOFF = (2, 3, 5, 10)
logger = logging.getLogger(__name__)


def _now() -> str:
    """生成 UTC 调度时间，不参与业务输入比较。"""

    return datetime.now(UTC).isoformat()


def _inputs(root: Path) -> tuple[str, dict, dict]:
    """重新读取确认产物，摘要覆盖绑定、DDL、管理员、Manifest 与完整 DAG。"""

    application = read_application_config(root)
    binding = builtin_binding(application)
    plan = read_confirmed_seed_plan(root)
    compiled = compile_builtin_authorization_seed(application, plan, datasource_key=binding["datasourceKey"])
    dag = load_confirmed_build_task_plan(root)
    if not dag or dag.get("status") != "ready":
        raise ValueError("权限初始化需要已确认且有效的 Build DAG。")
    payload = {"binding": binding, "ddl": compile_authorization_tables(root, binding["datasourceKey"])["ddlFingerprint"],
               "seed": compiled["seedFingerprint"], "manifest": plan["authorization_manifest"], "dag": dag,
               "authEnabled": (application.get("auth") or {}).get("enable")}
    return hashlib.sha256(canonical_json(payload)).hexdigest(), application, plan


def _attention(category: str) -> dict:
    """返回固定脱敏错误，拒绝猜测损坏或失配状态的成功事实。"""

    return {"status": "needs_attention", "stage": "schema", "schemaReady": False, "seedReady": False,
            "failureCategory": category, "nextAction": None, "message": "权限初始化结果待核对，请查看原操作。"}


def workflow_task_result(view: dict) -> dict:
    """保持 Graph 既有输出键，以受控字段携带独立编排状态。"""

    return {"status": "reused" if view["seedReady"] else "manual_required", "executor": "builtin_saas",
            "datasource_mode": "builtin", "schema_ready": view["schemaReady"], "seed_ready": view["seedReady"],
            "operation_id": view.get("schemaOperationId"), "seed_operation_id": view.get("seedOperationId"),
            "initialization_status": view["status"], "initialization_stage": view["stage"],
            "failure_category": view.get("failureCategory"), "next_action": view.get("nextAction"),
            "requires_manual_action": view["status"] not in ACTIVE and view["status"] != "ready",
            "message": view["message"], "initialized_tables": view.get("initializedTables", [])}


def get_builtin_authorization_task(workspace: str) -> dict | None:
    """纯本地投影，编排快照不能替代 Schema/Seed 的当前输入核验。"""

    root = Path(workspace).expanduser().resolve()
    try:
        task = read_task(root)
        record = read_operation_record(root)
        if task is None and not record:
            return None
        fingerprint, application, plan = _inputs(root)
        binding = builtin_binding(application)
        ddl = compile_authorization_tables(root, binding["datasourceKey"])
        schema = schema_ready(root, application) and record.get("ddlFingerprint") == ddl["ddlFingerprint"]
        ready = schema and seed_ready(root, application, plan)
        if not ready and task and (task.inputFingerprint != fingerprint or not record and task.operationStateExpected
                                   or task.seedStateExpected and "seed" not in record):
            return _attention("inputs_changed" if record else "operation_state_missing")
        seed = record.get("seed") or {}
        status = "ready" if ready else task.status if task and task.status != "ready" else "needs_attention"
        stage = "done" if ready else task.stage if task else "seed" if schema else "schema"
        view = {"status": status, "stage": stage, "schemaReady": schema, "seedReady": ready,
                "schemaOperationId": record.get("operationId"), "seedOperationId": seed.get("operationId"),
                "failureCategory": task.failureCategory if task else None,
                "updatedAt": task.updatedAt if task else None, "nextAction": None,
                "message": "权限表与权限数据均已核验就绪" if ready else
                           "正在自动处理，无需手动刷新。" if status in ACTIVE else "权限初始化结果待核对，请查看原操作。"}
        if task and (task.failureCategory == "timeout" or task.failureCategory == "worker_failed" and (
                         record.get("operationId") or seed.get("idempotencyKey")) or
                     task.failureCategory == "operation_unconfirmed" and schema and not seed):
            view["nextAction"] = "resume"
        if ready:
            compiled = compile_builtin_authorization_seed(application, plan, datasource_key=binding["datasourceKey"])
            tables = compiled["request"]["tables"]
            view.update(createdCount=seed.get("createdCount"), existingCount=seed.get("existingCount"),
                        resourceCount=len(tables[1]["rows"]), roleGrantCount=len(tables[2]["rows"]))
        if schema:
            view["initializedTables"] = ddl["tables"]
        for field in ("schemaOperationId", "seedOperationId"):
            value = view[field]
            if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 19 or value.startswith("0"):
                view.pop(field)
        return view
    except (OSError, ValueError, TypeError):
        return _attention("state_or_inputs_invalid")


def tick(root: Path, generation: str, *, execute=run_builtin_authorization_bootstrap) -> bool:
    """短事务内只推进一次原子动作；返回是否需要后续只读查询或阶段衔接。"""

    with builtin_lock(root), task_transaction(root):
        task = read_task(root)
        if task is None or task.generation != generation or task.status not in ACTIVE:
            return False

        with task_input_scope(root, task, lambda: _inputs(root)[0]) as guard:
            try:
                guard()
                record = read_operation_record(root)
                _, application, plan = _inputs(root)
                schema = schema_ready(root, application)
                if not read_operation_record(root):
                    binding = builtin_binding(application)
                    ddl = compile_authorization_tables(root, binding["datasourceKey"])
                    # 仅建立原服务使用的 Schema 元数据，不提前推导远端已受理。
                    save_state(root, {"schemaVersion": 1, "datasourceMode": "builtin", **binding,
                                      "ddlFingerprint": ddl["ddlFingerprint"], "schemaReady": False, "status": "pending"})
                # 进入原子动作前标记已开始，状态丢失不能退回首次运行。
                task.status, task.updatedAt, task.operationStateExpected = "running", _now(), True
                task.seedStateExpected = task.seedStateExpected or "seed" in record
                save_task(root, task)
                result = execute(str(root), plan, schema_only=not schema)
                guard()
                record = read_operation_record(root)
                seed = record.get("seed") or {}
                if seed_ready(root, application, plan):
                    task.status, task.stage, task.failureCategory = "ready", "done", None
                elif result.get("schema_ready") and not schema:
                    task.status, task.stage, task.failureCategory = "running", "seed", None
                else:
                    failure = result.get("failure_category")
                    operation_status = seed.get("operationStatus") if schema else record.get("status", "").upper()
                    # 网络故障只允许已有身份的 GET 退避；首次 POST 失败不能进入提交重试。
                    transient_query = failure in {
                        "seed_timeout", "seed_network_error", "seed_http_500", "seed_http_502", "seed_http_503", "seed_http_504"}
                    lost_receipt = seed.get("status") in {"submitting", "unknown"} and (
                        not failure or failure == seed.get("failureCategory") and result.get("failure_phase") == "seed_state_write_failed")
                    query_retry = schema and (transient_query or lost_receipt)
                    schema_query_retry = not schema and failure == "schema_query_transient" and (
                        record.get("operationId") or record.get("status") == "pending")
                    waiting = (operation_status in {"RUNNING", "SUCCEEDED"} and not failure
                               or query_retry or schema_query_retry)
                    task.status = "waiting" if waiting else "needs_attention"
                    verifying = schema and (operation_status == "SUCCEEDED" or failure in {
                        "seed_invalid_verification", "seed_operation_identity_mismatch"})
                    task.stage = "verify" if verifying else "seed" if schema else "schema"
                    task.failureCategory = failure if not waiting else None
                    if not waiting and not failure:
                        task.failureCategory = "unknown_without_identity" if not record.get("operationId") else "operation_unconfirmed"
                task.updatedAt = _now()
                save_task(root, task)
                return task.status in ACTIVE
            except (OSError, ValueError, TypeError):
                current = read_task(root)
                if current is None or current.generation != generation:
                    return False
                task.status, task.failureCategory, task.updatedAt = "needs_attention", "inputs_changed", _now()
                save_task(root, task)
                return False


def _worker(root: Path, generation: str, stop: threading.Event, *, clock=time.monotonic,
            backoff=BACKOFF, limit=600) -> None:
    """受界 worker 释放所有锁后等待；超时保留原操作身份供显式恢复。"""

    started, attempt = clock(), 0
    try:
        while not stop.is_set():
            if clock() - started >= limit:
                with builtin_lock(root), task_transaction(root):
                    task = read_task(root)
                    if task and task.generation == generation and task.status in ACTIVE:
                        task.status, task.failureCategory, task.updatedAt = "needs_attention", "timeout", _now()
                        save_task(root, task)
                return
            try:
                if not tick(root, generation):
                    return
            except portalocker.exceptions.LockException:
                pass
            if stop.wait(backoff[min(attempt, len(backoff) - 1)]):
                return
            attempt += 1
    except Exception:
        # 不记录异常正文，避免外部异常携带 SQL 或管理员数据；保留持久身份供 attach 接管。
        logger.error("Builtin authorization worker stopped generation=%s", generation)
        try:
            with builtin_lock(root), task_transaction(root):
                task = read_task(root)
                if task and task.generation == generation and task.status in ACTIVE:
                    task.status, task.failureCategory, task.updatedAt = "needs_attention", "worker_failed", _now()
                    save_task(root, task)
        except (OSError, ValueError, portalocker.exceptions.LockException):
            logger.error("Builtin authorization worker state unavailable generation=%s", generation)


def ensure_builtin_authorization_task(workspace: str, *, source: str) -> dict:
    """按工作区复用任务并启动受管 worker，不等待 SaaS 完成。"""

    global _executor
    root = Path(workspace).expanduser().resolve()
    key = str(root)
    try:
        with _registry_lock:
            if _closed or key in _blocked:
                return _attention("task_stopped")
            for finished in [name for name, (_, future) in _workers.items() if future.done()]:
                _workers.pop(finished)
            if key in _workers:
                return get_builtin_authorization_task(key) or _attention("state_missing")
            if sum(not future.done() for _, future in _workers.values()) >= 32:
                return _attention("worker_capacity")
            _inputs(root)  # 先验证真实工作区，不能为无效请求创建运行目录。
            with builtin_lock(root), task_transaction(root):
                fingerprint, application, plan = _inputs(root)
                if seed_ready(root, application, plan):
                    return get_builtin_authorization_task(key) or _attention("state_missing")
                task = read_task(root)
                if task and (task.inputFingerprint != fingerprint or task.status not in ACTIVE):
                    return get_builtin_authorization_task(key) or _attention("inputs_changed")
                if task is None:
                    task = AuthorizationTask(generation=uuid4().hex, inputFingerprint=fingerprint,
                                             status="pending", updatedAt=_now())
                    save_task(root, task)
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="builtin-authorization")
            stop = threading.Event()
            _workers[key] = stop, _executor.submit(_worker, root, task.generation, stop)
        return get_builtin_authorization_task(key) or _attention("state_missing")
    except portalocker.exceptions.LockException:
        return get_builtin_authorization_task(key) or _attention("worker_busy")
    except (OSError, ValueError, TypeError):
        return _attention("state_or_inputs_invalid")


def resume_builtin_authorization_task(workspace: str) -> dict | None:
    """仅接管已有任务或操作证据，不把 attach 当作首次初始化授权。"""

    root = Path(workspace).expanduser().resolve()
    try:
        if read_task(root) is None and not read_operation_record(root):
            return None
        with builtin_lock(root), task_transaction(root):
            task = read_task(root)
            if task is None and not read_operation_record(root):
                return None
            view = get_builtin_authorization_task(str(root))
            if task and view and view.get("nextAction") == "resume":
                task.status, task.failureCategory, task.updatedAt = "waiting", None, _now()
                save_task(root, task)
        return ensure_builtin_authorization_task(str(root), source="resume")
    except (OSError, ValueError, TypeError, portalocker.exceptions.LockException):
        return _attention("state_or_inputs_invalid")


def stop_builtin_authorization_task(workspace: str) -> None:
    """封锁新调度并等待当前安全 tick 退出，远端操作证据保持不变。"""

    key = str(Path(workspace).expanduser().resolve())
    with _registry_lock:
        _blocked.add(key)
        worker = _workers.pop(key, None)
        if worker:
            worker[0].set()
    if worker:
        worker[1].cancel()
        if not worker[1].cancelled():
            worker[1].result()
    root = Path(key)
    if root.is_dir() and read_task(root) is not None:
        with builtin_lock(root), task_transaction(root, timeout=60):
            task = read_task(root)
            if task and task.status in ACTIVE:
                task.status, task.updatedAt = "cancelled", _now()
                save_task(root, task)


def cancel_builtin_authorization_deletion(workspace: str) -> None:
    """删除准备回滚后解除本地栅栏，仅恢复已被停止的原任务。"""

    root = Path(workspace).expanduser().resolve()
    with _registry_lock:
        _blocked.discard(str(root))
    try:
        if read_task(root) is None:
            return
        with builtin_lock(root), task_transaction(root):
            task = read_task(root)
            if task is None or task.status != "cancelled":
                return
            task.status, task.updatedAt = "waiting", _now()
            save_task(root, task)
        resume_builtin_authorization_task(str(root))
    except (OSError, ValueError, portalocker.exceptions.LockException):
        logger.error("Builtin authorization deletion rollback needs review")


def active_builtin_authorization_count() -> int:
    """退出检查保留权限初始化及其停止收口线程，避免线程池尚在工作时升级。"""
    with _registry_lock:
        return sum(not future.done() for _, future in _workers.values())


def shutdown_builtin_authorization_tasks() -> None:
    """在 Backend lifespan 退出时有序停止所有受管线程。"""

    global _closed, _executor
    with _registry_lock:
        _closed = True
        workers = list(_workers.values())
        for stop, _ in workers:
            stop.set()
        executor, _executor = _executor, None
    if executor:
        executor.shutdown(wait=True, cancel_futures=True)
    with _registry_lock:
        _workers.clear()


def start_builtin_authorization_tasks() -> None:
    """应用生命周期启动时开放调度，不扫描磁盘或主动启动工作区。"""

    global _closed
    with _registry_lock:
        _closed = False
        _blocked.clear()
