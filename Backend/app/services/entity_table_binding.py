"""由权威应用配置确定实体的真实表名，不调用模型或远端注册接口。"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.data_source_policy import uses_builtin_database
from app.services.entity_definitions import (
    entity_database_table_name, entity_table_name,
)


def bind_plan_entity_tables(plan: dict[str, Any], application: dict[str, Any]) -> dict[str, Any]:
    """在技术规划确认前固定模拟库表名；外部数据库保持现有绑定方式。"""

    if not uses_builtin_database(application):
        return plan
    raw_registration = application.get("managedDatabase")
    if not isinstance(raw_registration, dict):
        raise ValueError("模拟数据库尚未完成应用注册，无法确定实体真实表名。")
    registration = ManagedDatabaseRegistration.model_validate(raw_registration)
    if registration.status != "REGISTERED":
        raise ValueError("模拟数据库注册尚未确认成功，无法确定实体真实表名。")
    entities = deepcopy(plan.get("entities", []))
    if not isinstance(entities, list):
        raise ValueError("技术规划实体必须是对象列表。")
    owners: dict[str, str] = {}
    entity_ids: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or not entity["id"].strip():
            raise ValueError("技术规划实体必须有稳定的非空 ID。")
        entity_id = entity["id"]
        if entity_id in entity_ids:
            raise ValueError(f"技术规划实体 ID 重复：{entity_id}。")
        entity_ids.add(entity_id)
        expected = f"{registration.datasource_key}_{entity_table_name(entity_id)}"
        # 统一校验字符和长度，超长不截断、冲突不随机改名。
        entity_database_table_name({"table_name": expected})
        if re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", expected) is None:
            raise ValueError("内置表名必须为小写字母、数字或下划线，且长度不超过 64。")
        existing = entity.get("table_name")
        if existing is not None and existing != expected:
            raise ValueError(f"实体 {entity_id} 的真实表名与当前数据源 Key 或实体身份不一致。")
        if expected in owners and owners[expected] != entity_id:
            raise ValueError(f"实体 {entity_id} 与 {owners[expected]} 的真实表名冲突：{expected}。")
        owners[expected] = entity_id
        entity["table_name"] = expected
    return {**deepcopy(plan), "entities": entities}


def bound_entity_table_errors(entity: dict[str, Any], detail: dict[str, Any], *, builtin: bool) -> list[str]:
    """阻止模拟库实体详情把固定目标换成未加前缀或其他实体的表。"""

    if not builtin:
        return []
    if "table_name" not in entity:
        return ["内置数据库实体缺少已绑定的真实表名，请重新确认技术规划。"]
    expected = entity_database_table_name(entity)
    database = _record(detail.get("database_design"))
    generation = _record(database.get("table_generation"))
    proposal = _record(generation.get("proposal"))
    selected = _record(database.get("selected_table"))
    target = _record(detail.get("table_design"))
    names = [
        detail.get("table_name"), target.get("name"), database.get("matched_table"),
        proposal.get("name"), selected.get("name"),
    ]
    for binding in database.get("bindings") or []:
        if isinstance(binding, dict):
            names.append(binding.get("table"))
    for operation in database.get("database_operations") or []:
        if isinstance(operation, dict):
            table = operation.get("table")
            names.append(table.get("name") if isinstance(table, dict) else table)
    if detail.get("table_name") != expected or any(
        name is not None and name != "" and name != expected for name in names
    ):
        return [f"实体 {entity.get('id')} 必须使用已绑定的真实表名 {expected}，不能重新指定目标表。"]
    return []


def _record(value: Any) -> dict[str, Any]:
    """只读取结构化对象，畸形提交交给原有实体方案校验报告。"""

    return value if isinstance(value, dict) else {}
