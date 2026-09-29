"""静态 JSON 来源的结构推断及当前契约校验，不访问外部数据源。"""

import json
import math
import re
from typing import Any, Literal

from pydantic import Field
from app.domain.api_design_fields import ApiDesignModel


class StaticBinding(ApiDesignModel):
    """静态内容由所属 Endpoint 持有，无全局数据源身份。"""
    source_type: Literal["static"] = Field(alias="sourceType")


class StaticSourceField(ApiDesignModel):
    """保存静态 JSON 叶子路径和推断类型。"""
    source_type: Literal["static"] = Field(default="static", alias="sourceType")
    path: str = Field(min_length=1, max_length=1024)
    type: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)


def static_fields(data: Any) -> list[dict[str, Any]]:
    """检查 JSON 大小及同构结构，逐层生成包含数组标记的可映射路径。"""
    if not isinstance(data, (dict, list)):
        raise ValueError("静态数据必须是 JSON 对象或对象列表。")
    if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 262144:
        raise ValueError("静态数据不能超过 256 KiB。")
    if isinstance(data, list) and any(not isinstance(item, dict) for item in data):
        raise ValueError("静态数据根列表的每项必须是对象。")

    def visit(value: Any, path: str, depth: int) -> dict[str, str]:
        """合并同构数组的字段；禁止含糊路径和无法推断类型的空容器。"""
        if depth > 12:
            raise ValueError("静态数据最多嵌套 12 层。")
        if isinstance(value, dict):
            if not value:
                raise ValueError("请填写静态数据，空对象无法推断字段。")
            result = {}
            for key, child in value.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in {"__proto__", "prototype", "constructor"}:
                    raise ValueError("静态字段名须使用字母、数字和下划线，且不能以数字开头或使用保留名称。")
                result.update(visit(child, f"{path}.{key}" if path else key, depth + 1))
            return result
        if isinstance(value, list):
            if not value or len(value) > 1000:
                raise ValueError("静态列表需包含 1 至 1000 项，空列表无法推断字段。")
            expected = visit(value[0], f"{path}[]", depth + 1)
            for child in value[1:]:
                if visit(child, f"{path}[]", depth + 1) != expected:
                    raise ValueError("同一静态列表中的字段结构和类型必须一致。")
            return expected
        if value is None:
            raise ValueError("静态字段暂不支持 null，请填写明确类型的值。")
        if isinstance(value, bool):
            kind = "boolean"
        elif isinstance(value, (int, float)):
            if not math.isfinite(value) or abs(value) > 9007199254740991:
                raise ValueError("静态数值必须是有限数，且不能超过安全数值范围。")
            kind = "integer" if int(value) == value else "number"
        elif isinstance(value, str):
            kind = "string"
        else:
            raise ValueError("静态数据包含非 JSON 类型。")
        return {path: kind}

    return [{"sourceType": "static", "path": path, "type": kind, "description": ""}
            for path, kind in visit(data, "", 0).items()]


def validate_static_design(draft: dict[str, Any]) -> None:
    """正式确认与就绪复检共享静态内容、单来源边界及路径快照校验。"""
    binding = draft.get("sourceBinding") or {}
    mappings = draft.get("fieldMappings", [])
    if not isinstance(binding, dict) or not isinstance(mappings, list) or any(not isinstance(item, dict) for item in mappings):
        raise ValueError("字段映射或来源结构无效。")
    sources = []
    for mapping in mappings:
        fields = mapping.get("sourceFields", [])
        if not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields):
            raise ValueError("来源字段必须是对象列表。")
        sources.extend(fields)
    if binding.get("sourceType") != "static":
        if draft.get("staticData") is not None or any(source.get("sourceType") == "static" for source in sources):
            raise ValueError("静态内容和字段必须绑定静态数据来源。")
        return
    if any(draft.get(key) for key in ("databaseOperation", "databaseQuery", "databaseWrites", "externalApiBindings", "implementationDescription", "sourceSnapshots")):
        raise ValueError("静态数据不支持数据库操作、外部调用或接口映射说明。")
    available = {field["path"]: field for field in static_fields(draft.get("staticData"))}
    for mapping in draft.get("fieldMappings", []):
        for source in mapping.get("sourceFields", []):
            actual = available.get(source.get("path"))
            if source.get("sourceType") != "static" or not actual or source.get("type") != actual["type"]:
                raise ValueError("静态来源字段已失效，请重新选择字段并确认类型。")
            if mapping.get("processingType") == "direct":
                endpoint = mapping.get("endpointField", {})
                if source["path"].count("[]") != str(endpoint.get("path", "")).count("[]"):
                    raise ValueError("直接映射的列表层级不一致，请使用业务处理说明转换规则。")
                if actual["type"] == "number" and endpoint.get("type") == "integer":
                    raise ValueError("小数不能直接映射到整数返回字段。")
