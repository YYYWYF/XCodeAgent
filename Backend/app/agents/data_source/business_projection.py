"""将正式接口设计投影为 Business Agent 使用的紧凑只读输入。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _identity(field: dict[str, Any]) -> tuple[str, ...]:
    """使用完整物理身份区分同一来源下的不同表或 Operation。"""

    if field.get("sourceType") == "database":
        keys = ("sourceType", "sourceId", "schema", "table")
    else:
        keys = ("sourceType", "sourceId", "directoryId", "operationId")
    return tuple(str(field.get(key) or "") for key in keys)


def _source_fields(value: Any) -> list[dict[str, Any]]:
    """收集绑定和映射中的物理来源，避免只按数据源 ID 合并。"""

    if isinstance(value, dict):
        own = [value] if value.get("sourceType") in {"database", "external_api"} else []
        return own + [field for item in value.values() for field in _source_fields(item)]
    if isinstance(value, list):
        return [field for item in value for field in _source_fields(item)]
    return []


def _sources(design: dict[str, Any]) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    """集中保存表结构和完整外部 Operation，并为物理身份分配稳定别名。"""

    sources: dict[str, Any] = {}
    aliases: dict[tuple[str, ...], str] = {}
    refs = _source_fields({key: value for key, value in design.items() if key != "sourceSnapshots"})
    for snapshot in design.get("sourceSnapshots") or []:
        details = snapshot.get("details") or {}
        source_type = snapshot["sourceType"]
        base = {"sourceType": source_type, "sourceId": snapshot["sourceId"]}
        if source_type == "external_api":
            identity = {**base, "directoryId": details.get("directoryId"),
                        "operationId": (details.get("operation") or {}).get("id")}
            candidates = [identity]
        else:
            candidates = [ref for ref in refs if ref.get("sourceType") == source_type
                          and ref.get("sourceId") == base["sourceId"] and ref.get("table")
                          and (not details.get("table") or ref["table"] == details["table"])
                          and ("schema" not in details or ref.get("schema", "") == details["schema"])]
            if not candidates:
                candidates = [{**base, "schema": details.get("schema"), "table": details.get("table")}]
        for candidate in candidates:
            key = _identity(candidate)
            if key in aliases:
                continue
            number = 1 + sum(item["sourceType"] == source_type for item in sources.values())
            alias = f"{source_type}_{number}"
            aliases[key] = alias
            source = {**base, **({"name": snapshot["name"]} if snapshot.get("name") else {})}
            if source_type == "external_api":
                source.update({"directoryId": details.get("directoryId"),
                               "connection": deepcopy(details.get("connection") or {}),
                               "operation": deepcopy(details.get("operation") or {})})
            else:
                table = candidate.get("table")
                columns = (details.get("schemas") or {}).get(table, details.get("columns") or [])
                source.update({"schema": candidate.get("schema", ""), "table": table,
                               "columns": _columns(columns)})
            sources[alias] = source
    return sources, aliases


def _columns(columns: list[Any]) -> dict[str, Any]:
    """将列名提升为键，只删除重复的必填标记和未设置的默认值。"""

    result = {}
    for column in columns:
        if not isinstance(column, dict):
            result[str(column)] = {}
            continue
        name = column.get("name") or column.get("column_name")
        result[str(name)] = {key: deepcopy(value) for key, value in column.items()
                             if key not in {"name", "column_name"}
                             and not (key == "required" and "nullable" in column)
                             and not (key == "defaultValue" and value is None)}
    return result


def _request(field: dict[str, Any]) -> str:
    """保留请求位置与完整字段路径，避免同名参数混淆。"""

    return f"{field['location']}.{field['path']}"


def _source_ref(field: dict[str, Any], aliases: dict[tuple[str, ...], str]) -> str:
    """把来源字段转换为别名引用；static 字段直读端点 staticData，不走外部别名。

    `sourceType == "static"` 的字段是接口自身持有的静态数据（`endpointContract.staticData`），
    不属于任何外部数据源——`_source_fields` 不收集它们（见 L23），aliases 里自然
    没有它们的 key。如果沿用 aliases 查询会抛 KeyError（实测报错
    `KeyError: ('static', '', '', '')`）。这里给 static 字段一个稳定的
    `static:<path>` 形式引用，调用方据此到 staticData 取值；其他 sourceType
    维持原别名查询，未解析来源在模型执行前明确失败。
    """

    if field.get("sourceType") == "static":
        return f"static:{field.get('path', '')}"
    alias = aliases[_identity(field)]
    path = field["column"] if field["sourceType"] == "database" else f"{field['section']}.{field['path']}"
    return f"{alias}.{path}"


def _business(rule: dict[str, Any], aliases: dict[tuple[str, ...], str]) -> dict[str, Any]:
    """保留业务说明、请求和来源依赖，暂不投影未实现的内置上下文。"""

    dependencies = {}
    if rule.get("sourceFields"):
        dependencies["sources"] = [_source_ref(item, aliases) for item in rule["sourceFields"]]
    if rule.get("endpointFields"):
        dependencies["requests"] = [_request(item) for item in rule["endpointFields"]]
    return {"business": rule["businessDescription"],
            **({"dependencies": dependencies} if dependencies else {})}


def _value(rule: dict[str, Any], aliases: dict[tuple[str, ...], str]) -> dict[str, Any]:
    """压缩统一右值，同时保留固定值的原始类型。"""

    if rule["kind"] == "endpoint":
        return {"request": _request(rule["endpointField"])}
    if rule["kind"] == "fixed":
        return {"fixed": deepcopy(rule["value"])}
    return _business(rule, aliases)


def _query(query: dict[str, Any], aliases: dict[tuple[str, ...], str]) -> dict[str, Any]:
    """保留查询树分组、重复条件及运算符，仅压缩字段身份和右值。"""

    items = []
    for item in query.get("items") or []:
        if item.get("kind") == "group":
            items.append(_query(item, aliases))
        else:
            items.append({"column": _source_ref(item, aliases), "operator": item["operator"],
                          **({"value": _value(item["right"], aliases)} if item.get("right") else {})})
    return {"join": query.get("join", "and"), "items": items}


def business_api_design(design: dict[str, Any]) -> dict[str, Any]:
    """生成 Business 专用投影，不修改正式接口设计或其他阶段的输入。"""

    sources, aliases = _sources(design)
    result: dict[str, Any] = {"sources": sources}
    if design.get("databaseOperation"):
        result["databaseOperation"] = design["databaseOperation"]
    if design.get("databaseQuery"):
        result["databaseQuery"] = _query(design["databaseQuery"], aliases)
    writes: dict[str, dict[str, Any]] = {}
    for item in design.get("databaseWrites") or []:
        alias = aliases[_identity(item)]
        values = writes.setdefault(alias, {})
        if item["column"] in values:
            raise ValueError("Business 投影包含重复的数据库写入列。")
        values[item["column"]] = _value(item["right"], aliases)
    if writes:
        groups = [{"source": alias, "values": values} for alias, values in writes.items()]
        result["databaseWrites"] = groups[0] if len(groups) == 1 else groups
    bindings = []
    for item in design.get("externalApiBindings") or []:
        target = item["externalField"]
        bindings.append({"target": _source_ref(target, aliases), "value": _value(item["right"], aliases)})
    if bindings:
        result["externalApiBindings"] = bindings
    mappings = {}
    for item in design.get("fieldMappings") or []:
        path = item["endpointField"]["path"]
        if path in mappings:
            raise ValueError("Business 投影包含重复的响应字段路径。")
        if item["mappingType"] == "value_mapping":
            mappings[path] = _value(item["right"], aliases)
        elif item.get("processingType") == "direct":
            mappings[path] = {"source": _source_ref(item["sourceFields"][0], aliases)}
        else:
            mappings[path] = _business(item, aliases)
    result["fieldMappings"] = mappings
    if design.get("implementationDescription"):
        result["implementationDescription"] = design["implementationDescription"]
    return result
