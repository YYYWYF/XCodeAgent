from __future__ import annotations


def allocate_capability_ids(
    preferred_ids: dict[str, str],
    existing_ids: dict[str, str],
) -> dict[str, str]:
    """保留已有能力身份，为新增能力确定性分配当前智能体内未占用的 ID。"""

    allocated: dict[str, str] = {
        name: existing_ids[name]
        for name in preferred_ids
        if existing_ids.get(name)
    }
    # 先预留全部旧身份及本轮候选身份，避免前面的新增项占用后面的能力 ID。
    existing_id_values: set[str] = set(existing_ids.values())
    reserved_ids: set[str] = existing_id_values | set(preferred_ids.values())
    used_ids: set[str] = set(allocated.values())
    for name, preferred_id in preferred_ids.items():
        if name in allocated:
            continue
        capability_id = preferred_id
        if capability_id in existing_id_values or capability_id in used_ids:
            suffix = 2
            capability_id = f"{preferred_id}_{suffix}"
            while capability_id in reserved_ids or capability_id in used_ids:
                suffix += 1
                capability_id = f"{preferred_id}_{suffix}"
        allocated[name] = capability_id
        used_ids.add(capability_id)
    return allocated
