"""约束实体展示文案的中文语义，不改写英文技术标识或正式产物。"""

from __future__ import annotations

import re
from typing import Any


_CHINESE_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def validate_entity_display_language(plan: dict[str, Any], *, builtin: bool) -> list[str]:
    """校验技术规划实体及字段的中文展示文案，允许中文中保留技术缩写。"""

    if not builtin or plan.get("artifact_type") != "technical-plan":
        return []
    errors: list[str] = []
    entities = plan.get("entities")
    for index, entity in enumerate(entities if isinstance(entities, list) else []):
        if not isinstance(entity, dict):
            continue
        owner = f"实体 {entity.get('id') or index + 1}"
        display_values = [(owner, key, entity.get(key)) for key in ("name", "description")]
        fields = entity.get("fields")
        for field_index, field in enumerate(fields if isinstance(fields, list) else []):
            if isinstance(field, dict):
                field_owner = f"{owner} 字段 {field.get('name') or field_index + 1}"
                display_values.extend((field_owner, key, field.get(key)) for key in ("label", "description"))
        # 只检查面向用户与数据库注释的文案，不根据展示名称重命名技术标识。
        for target, key, value in display_values:
            if not isinstance(value, str) or not value.strip() or _CHINESE_CHARACTER.search(value) is None:
                errors.append(f"TechnicalPlan {target} 的 {key} 必须是非空中文文案（可包含 ID、API 等技术缩写）。")
    return errors
