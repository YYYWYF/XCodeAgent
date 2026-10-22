"""验证 AI 从空配置生成的全量映射，未确定内容显式保留未配置。"""

import copy
import hashlib
import json
from typing import Any

from pydantic import TypeAdapter

from app.domain.api_design import (
    BusinessDescriptionFieldMapping, DatabaseQuery, DatabaseWriteMapping,
    DraftFieldMapping, SourceMapping, UnconfiguredFieldMapping,
)
from app.domain.endpoint_design_assist import GeneratedMapping, MappingCandidate
from app.services.api_design import (
    _endpoint_field_snapshot, _query_conditions, _validate_database_condition,
    _validate_database_writes, _validate_field_mappings, _validate_query_references,
)

_MAPPING = TypeAdapter(DraftFieldMapping)


def digest(value: Any) -> str:
    """使用规范 JSON 摘要校验基线，拒绝非 JSON 数值。"""
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def field_key(field: dict[str, Any]) -> str:
    """按请求方向、区域及路径生成当前字段身份。"""
    return ":".join(str(field.get(key, "")) for key in ("side", "location", "path"))


def _column(value: dict[str, Any], context: dict[str, Any], *, usage: str | None = None) -> None:
    """禁止候选伪造来源身份、列名称、方向或当前列类型。"""
    selection = context["selection"]
    if any(value.get(key) != selection[key] for key in ("sourceType", "sourceId", "schema", "table")):
        raise ValueError("AI 建议引用了当前选定表之外的来源。")
    column = next((item for item in context["columns"] if item["name"] == value.get("column")), None)
    if column is None or value.get("type") != column["type"]:
        raise ValueError("AI 建议中的数据库字段不存在或类型已变化。")
    if usage and value.get("usage", "read") != usage:
        raise ValueError("返回字段只能读取当前数据表。")


def _validate_generation_policy(after: Any) -> None:
    """全量生成使用公开默认策略，不继承旧规则或新增隐藏依赖。"""
    if not isinstance(after, dict):
        return
    rule = after.get("right") or after
    if rule.get("builtinFields", []):
        raise ValueError("AI 生成不能新增内置参数依赖。")
    if rule.get("missingBehavior", "error") != "error" or rule.get("defaultValue") is not None:
        raise ValueError("AI 生成不能配置隐藏缺值策略或默认值。")


def validate_candidate(candidate: MappingCandidate, context: dict[str, Any]) -> Any:
    """逐项验证受限建议并返回当前契约的规范表示。"""
    area, key, value = candidate.area, candidate.key, candidate.value
    if area == "request":
        raise ValueError("数据库映射不支持外部入参建议。")
    operation = context["databaseOperation"]
    fields = context["endpointFields"]
    if area == "description":
        if key != "implementationDescription" or not isinstance(value, str) or len(value) > 4000:
            raise ValueError("接口映射说明格式无效。")
        return value.strip()
    if area == "query":
        if key != "databaseQuery" or operation == "create":
            raise ValueError("当前操作不支持查询条件建议。")
        query = DatabaseQuery.model_validate(value) if value is not None else None
        if operation in {"update", "delete"} and not _query_conditions(query):
            raise ValueError("修改或删除必须保留有效查询条件。")
        for condition in _query_conditions(query):
            _column(condition.model_dump(by_alias=True), context)
            _validate_database_condition(condition)
            _validate_generation_policy(condition.model_dump(by_alias=True))
        _validate_query_references(query, fields)
        return query.model_dump(mode="json", by_alias=True, exclude_none=True) if query else None
    if area == "write":
        if operation not in {"create", "update"}:
            raise ValueError("当前操作不支持写入建议。")
        if key not in {item["name"] for item in context["columns"]}:
            raise ValueError("写入目标列不存在。")
        if value is None:
            return None
        write = DatabaseWriteMapping.model_validate(value)
        if write.column != key:
            raise ValueError("写入建议目标身份不一致。")
        _column(write.model_dump(by_alias=True), context)
        _validate_database_writes([write], fields)
        _validate_generation_policy(value)
        return write.model_dump(mode="json", by_alias=True, exclude_none=True)
    target = next((item for item in fields if item.get("side") == "response" and field_key(item) == key), None)
    if target is None:
        raise ValueError("返回建议不属于当前接口的返回字段。")
    if value is None:
        return None
    mapping = _MAPPING.validate_python(value)
    if isinstance(mapping, (UnconfiguredFieldMapping, BusinessDescriptionFieldMapping)):
        raise ValueError("返回建议必须使用明确的取值或来源规则。")
    if field_key(mapping.endpoint_field.model_dump(by_alias=True)) != key:
        raise ValueError("返回建议目标身份不一致。")
    subset = [item for item in fields if item.get("side") == "request"] + [target]
    _validate_field_mappings([mapping], subset, operation)
    if isinstance(mapping, SourceMapping):
        for source in mapping.source_fields:
            _column(source.model_dump(by_alias=True), context, usage="read")
    _validate_generation_policy(value)
    return mapping.model_dump(mode="json", by_alias=True, exclude_none=True)


def merge_candidates(draft: dict[str, Any], candidates: list[MappingCandidate], context: dict[str, Any]) -> dict[str, Any]:
    """在副本上整体应用所选候选，失败时不返回部分修改。"""
    result = copy.deepcopy(draft)
    seen = set()
    for candidate in candidates:
        identity = (candidate.area, candidate.key)
        if identity in seen:
            raise ValueError("同一目标不能同时应用多条建议。")
        seen.add(identity)
        value = validate_candidate(candidate, context)
        # 空返回建议表示清空取值；仍保留正式接口字段的原始目标快照。
        if candidate.area == "return" and value is None:
            target = next(item for item in context["endpointFields"] if field_key(item) == candidate.key)
            value = {"endpointField": _endpoint_field_snapshot(target), "mappingType": "unconfigured"}
        if candidate.area == "query":
            if value is None:
                result.pop("databaseQuery", None)
            else:
                result["databaseQuery"] = value
        elif candidate.area == "description":
            result["implementationDescription"] = value
        else:
            name = "databaseWrites" if candidate.area == "write" else "fieldMappings"
            items = result.setdefault(name, [])
            indices = [index for index, item in enumerate(items) if (item.get("column") if candidate.area == "write" else field_key(item.get("endpointField", {}))) == candidate.key]
            if len(indices) > 1:
                raise ValueError("草稿包含重复目标，请先手动修正。")
            if indices:
                if value is None:
                    items.pop(indices[0])
                else:
                    items[indices[0]] = value
            elif value is not None:
                items.append(value)
    columns = [item.get("column") for item in result.get("databaseWrites", []) if item.get("column")]
    if len(columns) != len(set(columns)):
        raise ValueError("组合后的写入目标列重复。")
    if any(item.area == "write" for item in candidates) and draft["databaseOperation"] in {"create", "update"} and not result.get("databaseWrites"):
        raise ValueError("新增或修改至少需要一项写入配置。")
    return result


def blank_mapping_draft(context: dict[str, Any]) -> dict[str, Any]:
    """清空所有可生成配置；仅保留来源与接口身份，操作取本轮识别结果。"""
    draft = context["draft"]
    return {"apiContractId": draft["apiContractId"], "endpointId": draft["endpointId"],
            "sourceBinding": context["selection"], "databaseOperation": context["databaseOperation"],
            "implementationDescription": "", "databaseWrites": [],
            # 请求占位快照供现有前端的依赖校验使用；旧请求业务说明同样清空。
            "fieldMappings": [{"mappingType": "unconfigured", "endpointField": _endpoint_field_snapshot(field)}
                              for field in sorted(context["endpointFields"], key=lambda field: field["side"] != "response")]}


def complete_mapping(candidates: list[MappingCandidate], context: dict[str, Any]) -> dict[str, Any]:
    """从空配置组装完整映射，缺少信息的返回项始终保持未配置。"""
    blank = blank_mapping_draft(context)
    clean_context = {**context, "draft": blank}
    merged = merge_candidates(blank, candidates, clean_context)
    return GeneratedMapping.model_validate({key: merged.get(key) for key in
        ("databaseOperation", "databaseQuery", "databaseWrites", "fieldMappings", "implementationDescription")}).model_dump(by_alias=True)


def validate_generated_mapping(mapping: GeneratedMapping, context: dict[str, Any]) -> dict[str, Any]:
    """重新验证整个替换内容，拒绝越界目标与伪造未配置字段快照。"""
    # 组合约束必须按生成的新操作检查，不能沿用替换前草稿的操作。
    context = {**context, "databaseOperation": mapping.database_operation}
    candidates = [MappingCandidate(area="description", key="implementationDescription", value=mapping.implementation_description, reason="完整映射")]
    if mapping.database_query is not None:
        candidates.append(MappingCandidate(area="query", key="databaseQuery", value=mapping.database_query, reason="完整映射"))
    for write in mapping.database_writes:
        candidates.append(MappingCandidate(area="write", key=write.get("column", ""), value=write, reason="完整映射"))
    expected = {field_key(field): _endpoint_field_snapshot(field) for field in context["endpointFields"]}
    seen = set()
    for value in mapping.field_mappings:
        key = field_key(value.get("endpointField", {}))
        if key not in expected or key in seen:
            raise ValueError("完整映射包含重复或越界返回字段。")
        seen.add(key)
        if value.get("mappingType") == "unconfigured":
            parsed = UnconfiguredFieldMapping.model_validate(value)
            if parsed.endpoint_field.model_dump(by_alias=True) != expected[key]:
                raise ValueError("未配置字段的契约快照已变化。")
        else:
            candidates.append(MappingCandidate(area="return", key=key, value=value, reason="完整映射"))
    if seen != set(expected):
        raise ValueError("完整映射必须包含当前全部契约字段，未知取值请明确标为未配置。")
    return complete_mapping(candidates, context)


def incomplete_mapping_questions(mapping: dict[str, Any], operation: str) -> list[str]:
    """对未配置内容补充可见提示，避免静默沿用旧配置。"""
    questions = [f"返回字段 {item['endpointField']['path']} 尚未配置，请补充其取值规则。"
                 for item in mapping["fieldMappings"] if item["mappingType"] == "unconfigured" and item["endpointField"]["side"] == "response"]
    if operation in {"update", "delete"} and not (mapping.get("databaseQuery") or {}).get("items"):
        questions.append("请明确修改或删除的查询条件；当前尚未配置，保存确认前必须补全。")
    if operation in {"create", "update"} and not mapping["databaseWrites"]:
        questions.append("请明确写入字段及取值；当前尚未配置，保存确认前必须补全。")
    return questions
