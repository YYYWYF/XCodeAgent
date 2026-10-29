"""从当前已确认技术规划生成只读 DDL，不读取连接凭据或执行 SQL。"""

import json
from typing import Any

from app.services.entity_table_sql import compile_entity_table, create_table_sql
from app.workspace.endpoint_design_documents import technical_plan_path


def preview_entity_ddl(workspace_root: str, entity_id: str) -> dict[str, Any]:
    """复用统一 MySQL 编译器，为普通实体详情提供可复制的完整建表语句。"""

    try:
        plan = json.loads(technical_plan_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("请先完成并确认技术规划，再查看建表 DDL。") from None
    if (not isinstance(plan, dict) or plan.get("artifact_type") != "technical-plan"
            or plan.get("confirmation_status") != "confirmed"):
        raise ValueError("请先确认当前技术规划，再查看建表 DDL。")
    entities = plan.get("entities")
    matches = [item for item in entities if isinstance(item, dict) and item.get("id") == entity_id] \
        if isinstance(entities, list) else []
    if len(matches) != 1:
        raise ValueError("当前技术规划中不存在唯一的目标实体。")
    entity = matches[0]
    if not isinstance(entity.get("fields"), list) or not entity["fields"]:
        raise ValueError("当前实体尚未生成字段清单，请先完善并确认技术规划。")
    table = compile_entity_table(entity)
    return {"entityId": entity_id, "entityName": entity.get("name") or entity_id,
            "tableName": table["name"], "sql": create_table_sql(table)}
