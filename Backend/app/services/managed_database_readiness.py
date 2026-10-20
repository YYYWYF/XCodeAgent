"""把可信的全应用表检查投影为可导航的启动状态，不传递 SQL 或连接信息。"""

from datetime import UTC, datetime
from typing import Any

from app.domain.entity_tables import TableReadiness


def database_check_report(
    entities: tuple[tuple[str, str, str], ...], *, status: str, message: str,
    readiness: TableReadiness | None = None,
) -> dict[str, Any]:
    """通过已冻结的实体身份关联真实表，只投射有界差异和本次检查时间。"""

    entries = {entry.tableName: entry for entry in readiness.tables} if readiness else {}
    rows = []
    for entity_id, label, table_name in entities:
        entry = entries.get(table_name)
        rows.append({
            "entityId": entity_id, "label": label[:200], "tableName": table_name,
            "status": entry.status if entry else "CHECKING" if status == "checking" else "NOT_CHECKED",
            "differenceCount": len(entry.differences) if entry else 0,
            "differences": [{
                "category": item.category[:64], "target": item.target[:128],
                "expected": item.expected[:256] if item.expected is not None else None,
                "actual": item.actual[:256] if item.actual is not None else None,
            } for item in entry.differences[:8]] if entry else [],
        })
    return {
        "status": status, "message": message,
        "checkedAt": datetime.now(UTC).isoformat() if status != "checking" else None,
        "entities": rows,
    }
