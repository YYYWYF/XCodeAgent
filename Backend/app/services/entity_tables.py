"""使用已确认的实体 DDL，代理 SaaS 单表创建与检查。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.domain.entity_tables import EntityTableRequest, OperationReceipt, TableReadiness
from app.domain.managed_database_operations import parse_database_operations
from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.entity_ddl import require_confirmed_entity_ddl, run_entity_ddl_action
from app.services.entity_table_binding import bind_plan_entity_tables
from app.services.managed_database_client import ManagedDatabaseClient
from app.services.backend_process_registry import backend_launch_lock
from app.services.project_launcher import inspect_project_preview
from app.workspace.endpoint_design_documents import technical_plan_path
from app.workspace.entity_ddl_documents import require_ddl_plan_version


def prepare_entity_table(request: EntityTableRequest) -> dict[str, Any]:
    """读取当前正式 DDL 和注册绑定，签发应用于本次建表确认的指纹。"""

    application = ApplicationConfigRepository(request.workspace_root).load()
    datasource = application.get("datasource") or {}
    if datasource.get("type") != "database" or (datasource.get("db") or {}).get("useBuiltin") is not True:
        raise ValueError("当前实体建表入口仅用于模拟数据库。")
    binding = ManagedDatabaseRegistration.model_validate(application.get("managedDatabase") or {})
    if binding.status != "REGISTERED":
        raise ValueError("模拟数据库尚未注册成功。")
    workspace = Path(request.workspace_root).expanduser().resolve()
    plan_path = technical_plan_path(workspace).resolve()
    if not plan_path.is_relative_to(workspace):
        raise ValueError("技术规划路径超出当前工作区。")
    try:
        plan_bytes = plan_path.read_bytes()
        plan = json.loads(plan_bytes.decode("utf-8"))
    except (OSError, ValueError):
        raise ValueError("请先完成并确认技术规划，再使用实体建表 DDL。") from None
    if not isinstance(plan, dict) or plan.get("artifact_type") != "technical-plan" or plan.get("confirmation_status") != "confirmed":
        raise ValueError("技术规划尚未确认，不能执行实体建表。")
    # 只校验已确认产物的绑定，不能在这里给旧产物补前缀或静默迁移。
    bound = bind_plan_entity_tables(plan, application)
    if bound.get("entities") != plan.get("entities"):
        raise ValueError("实体尚未固定真实表名，请重新确认技术规划。")
    matches = [item for item in plan["entities"] if item["id"] == request.entity_id]
    if len(matches) != 1:
        raise ValueError("当前技术规划中不存在唯一的目标实体。")
    entity = matches[0]
    plan_digest = hashlib.sha256(plan_bytes).hexdigest()
    ddl = require_confirmed_entity_ddl(workspace, entity, plan_digest)
    require_ddl_plan_version(plan_path, plan_digest)
    # 把应用绑定与完整当前规划纳入确认指纹，上游变更后旧确认不能执行。
    fingerprint = json.dumps({"ddl": ddl.model_dump(), "binding": binding.model_dump()},
                             ensure_ascii=False, sort_keys=True).encode()
    return {
        "entityId": request.entity_id, "entityName": entity.get("name") or request.entity_id,
        "datasourceKey": binding.datasource_key, "datasourceId": binding.datasource_id,
        "tableName": ddl.table_name, "sql": ddl.sql,
        "previewHash": hashlib.sha256(fingerprint).hexdigest(),
    }


def run_entity_table_action(request: EntityTableRequest, *, client: ManagedDatabaseClient | None = None) -> dict[str, Any]:
    """执行一次明确动作；查询只读，不自动重试、轮询或创建远端资源。"""

    # DDL 审阅和保存是本地动作，不进入模拟数据库配置校验或执行。
    if request.action in {"preview_ddl", "revise_ddl", "confirm_ddl"}:
        return {"action": request.action, "ddlPreview": run_entity_ddl_action(request)}
    preview = prepare_entity_table(request)
    result: dict[str, Any] = {"action": request.action, "preview": preview}
    if request.action == "prepare":
        return result
    if request.action in {"execute", "rebuild"} and (not request.confirmed or request.preview_hash != preview["previewHash"]):
        raise ValueError("请先预览并明确确认当前 SQL；设计变化后需要重新加载和确认。")
    if request.action == "rebuild" and not request.allow_data_loss:
        raise ValueError("重建将清空该表全部数据，请明确确认允许丢弃数据后再提交。")
    remote = client or ManagedDatabaseClient(Settings.from_env())
    datasource_key = preview["datasourceKey"]
    if request.action in {"execute", "rebuild"}:
        if request.action == "rebuild":
            # 与受控预览启动共用锁，检查已停止后再受理；不代替 SaaS 的应用占用。
            with backend_launch_lock(Path(request.workspace_root).expanduser().resolve()):
                if inspect_project_preview(request.workspace_root)["running"]:
                    raise ValueError("当前应用预览仍在运行，请先停止预览服务，再确认重建表。")
                # 等待启动锁期间设计可能变化，删除数据前再次核对用户确认的版本。
                if prepare_entity_table(request)["previewHash"] != request.preview_hash:
                    raise ValueError("等待期间实体设计已变化，请重新加载 SQL 后确认重建。")
                raw_receipt = remote.submit_table(datasource_key, mode="REBUILD", sql=preview["sql"], allow_data_loss=True)
        else:
            raw_receipt = remote.submit_table(datasource_key, mode="CREATE", sql=preview["sql"])
        try:
            receipt = OperationReceipt.model_validate(raw_receipt)
        except ValidationError:
            raise ValueError("SaaS 已响应，但受理结果无效；请先刷新原操作记录，不要重复提交。") from None
        result["receipt"] = receipt.model_dump()
        return result
    operations = remote.list_table_operations(datasource_key)
    history = parse_database_operations(operations)
    selected = next((item for item in history if item.targets_table(preview["tableName"])), None)
    result["operation"] = selected.model_dump() if selected else None
    raw_readiness = remote.check_tables(datasource_key, [preview["sql"]])
    try:
        readiness = TableReadiness.model_validate(raw_readiness)
    except ValidationError:
        raise ValueError("SaaS 结构检查结果不符合当前合同。") from None
    if readiness.datasourceId != preview["datasourceId"]:
        raise ValueError("SaaS 检查结果的数据源与已注册绑定不一致。")
    if any(item.tableName != preview["tableName"] for item in readiness.tables):
        raise ValueError("SaaS 检查结果包含非目标表。")
    if readiness.ready and (len(readiness.tables) != 1 or readiness.tables[0].status != "SYNCED"):
        raise ValueError("SaaS 检查结果未完整确认当前实体表。")
    if prepare_entity_table(request)["previewHash"] != preview["previewHash"]:
        raise ValueError("结构检查期间实体设计或应用绑定已变化，请重新加载后检查。")
    result["readiness"] = readiness.model_dump()
    return result
