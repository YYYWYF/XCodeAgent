"""独立调试补丁的分阶段操作和可重新读取的安全结果。"""

import re
from pathlib import Path

from app.services.application_config import read_application_config
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.authorization_workspace_projector import read_record
from app.services.builtin_authorization_bootstrap import refresh_builtin_authorization, run_builtin_authorization_bootstrap
from app.services.builtin_authorization_seed import compile_builtin_authorization_seed, read_confirmed_seed_plan
from app.services.builtin_authorization_seed_runtime import refresh_builtin_authorization_seed, run_builtin_authorization_seed
from app.services.builtin_authorization_state import builtin_binding, builtin_lock, schema_ready, seed_ready, uses_builtin_database
from app.workspace.task_documents import load_confirmed_build_task_plan


def check_patch_schema(workspace: str) -> dict:
    """校验正式产物，检查或首次创建权限四表。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root):
        plan = read_confirmed_seed_plan(root)
        dag = load_confirmed_build_task_plan(root)
        if not dag or dag.get("status") != "ready":
            raise ValueError("权限初始化补丁需要已确认且有效的 Build DAG。")
        return run_builtin_authorization_bootstrap(str(root), plan, schema_only=True)


def write_patch_seed(workspace: str) -> dict:
    """复用已核验 Schema 并提交或查询权限数据，不重新进入建表分支。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root):
        record = read_record(root, "state.json")
        application = read_application_config(root)
        binding = builtin_binding(application)
        if not schema_ready(root, application):
            raise ValueError("权限表状态已变化，请重新检查。")
        tables = compile_authorization_tables(root, binding["datasourceKey"])["tables"]
        return run_builtin_authorization_seed(root, record, tables, read_confirmed_seed_plan(root))


def refresh_patch(workspace: str) -> dict:
    """只查询已有操作，缺少操作编号时仅允许按已落盘的 Seed 身份核对。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root):
        record = read_record(root, "state.json")
        seed = record.get("seed")
        if isinstance(seed, dict):
            if seed.get("operationId"):
                return refresh_builtin_authorization_seed(str(root), seed["operationId"])
            # 已存在的保护记录只能进入按幂等键查询分支。
            return write_patch_seed(str(root))
        if record.get("operationId"):
            return refresh_builtin_authorization(str(root), record["operationId"])
        raise ValueError("没有可查询的原操作，请核对数据库状态。")


def patch_view(workspace: str, result: dict | None = None) -> dict | None:
    """由实时结果或当前绑定的本地核验记录生成补丁卡，不接触 SaaS。"""

    root = Path(workspace).expanduser().resolve()
    with builtin_lock(root):
        record = read_record(root, "state.json")
        seed = record.get("seed")
        seed = seed if isinstance(seed, dict) else {}
        if result is None:
            if not record:
                return None
            application = read_application_config(root)
            if not uses_builtin_database(application) or not (application.get("authorization") or {}).get("enabled"):
                return None
            binding = builtin_binding(application)
            if any(record.get(field) != value for field, value in binding.items()):
                return None
            ddl = compile_authorization_tables(root, binding["datasourceKey"])
            if record.get("ddlFingerprint") != ddl["ddlFingerprint"]:
                return None
            schema_verified = schema_ready(root, application)
            data_verified = schema_verified and bool(seed) and seed_ready(root, application, read_confirmed_seed_plan(root))
            result = {
                "schema_ready": schema_verified, "seed_ready": data_verified,
                "operation_id": record.get("operationId"),
                "operation_status": str(record.get("status") or "").upper(),
                "seed_operation_id": seed.get("operationId"),
                "seed_operation_status": seed.get("operationStatus"),
                "failure_category": seed.get("failureCategory"),
            }

        schema_verified = result.get("schema_ready") is True
        data_verified = result.get("seed_ready") is True
        schema_status = result.get("operation_status")
        seed_status = result.get("seed_operation_status")
        schema_id = result.get("operation_id")
        seed_id = result.get("seed_operation_id")
        failure = result.get("failure_category")
        http = re.fullmatch(r"seed_http_(\d{3})", str(failure or ""))
        waiting_for_schema = not schema_verified and schema_status == "RUNNING"
        waiting_for_seed = schema_verified and seed_status == "RUNNING" and not failure
        uncertain_seed = schema_verified and (
            seed_status == "UNKNOWN" or seed.get("status") in {"submitting", "unknown"}
            or seed_id and result.get("next_action") == "refresh_seed" and failure
        )
        stage = "verify" if data_verified or seed_id and failure else "write" if schema_verified else "schema"
        action = None
        if data_verified:
            status, message = "ready", "权限数据已回读核验。"
        elif waiting_for_schema or waiting_for_seed:
            status, message, action = "waiting", (
                "权限数据写入已受理，等待执行完成。" if schema_verified else "建表已受理，等待执行完成。"
            ), "refresh"
        elif uncertain_seed:
            message = (
                f"原写入操作查询失败（HTTP {http[1]}），写入结果待核对。"
                if http and seed_id else "权限数据结果待核对，请查询原操作。"
            )
            status, action = "unknown", "refresh"
        elif not schema_verified and str(record.get("status") or "").lower() in {"submitting", "unknown"}:
            status, message = "unknown", result.get("message") or "建表结果待核对。"
            action = "refresh" if schema_id else None
        elif schema_verified and not seed and not failure:
            status, message, action = "continue", "四张权限表已核验，可以继续写入权限数据。", "run"
        else:
            status, message = "failed", result.get("message") or "权限初始化未完成，请核对操作结果。"
            if http and result.get("failure_phase") == "seed_query_unconfirmed" and not seed:
                message = f"查询原写入记录失败（HTTP {http[1]}），本次未提交写入。"
                action = "run"

        view = {"stage": stage, "status": status, "message": message, "schemaReady": schema_verified,
                "schemaOperationId": schema_id, "seedOperationId": seed_id, "nextAction": action}
        if data_verified:
            application = read_application_config(root)
            binding = builtin_binding(application)
            plan = read_confirmed_seed_plan(root)
            compiled = compile_builtin_authorization_seed(application, plan, datasource_key=binding["datasourceKey"])
            # 只在当前输入仍与核验记录匹配时展示计数。
            if seed.get("seedFingerprint") == compiled["seedFingerprint"]:
                tables = compiled["request"]["tables"]
                view.update(createdCount=seed.get("createdCount"), existingCount=seed.get("existingCount"),
                            resourceCount=len(tables[1]["rows"]), roleGrantCount=len(tables[2]["rows"]))
        return view
