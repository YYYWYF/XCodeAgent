"""将本次已确认映射中的数据库身份绑定到生成 Agent。"""

from typing import Any

from app.agents.data_source.prompt_context import task_endpoint_designs


def task_database_source_ids(tasks: list[dict[str, Any]]) -> tuple[str, ...]:
    """从已确认映射提取数据库身份，拒绝缺失或跨库任务以免生成时误绑连接。"""

    source_ids: set[str] = set()
    for task in tasks:
        for design in task_endpoint_designs(task):
            for snapshot in design.get("sourceSnapshots") or []:
                if not isinstance(snapshot, dict) or snapshot.get("sourceType") != "database":
                    continue
                source_id = str(snapshot.get("sourceId") or "").strip()
                if not source_id:
                    raise ValueError("后端任务的数据库映射缺少 sourceId，请重新确认接口映射。")
                source_ids.add(source_id)
    if len(source_ids) > 1:
        raise ValueError("后端任务引用多个数据库，请重新确认当前应用的接口映射。")
    return tuple(sorted(source_ids))
