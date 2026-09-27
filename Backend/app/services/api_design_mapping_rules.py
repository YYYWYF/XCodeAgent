"""当前 Endpoint 映射的来源遍历与身份校验。"""

from typing import Any


def mapping_sources(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """读取当前格式的全部真实来源，不读取历史单来源字段。"""
    return [item for item in mapping.get("sourceFields", []) if isinstance(item, dict)]


def mapping_business_description(mapping: dict[str, Any]) -> str:
    """读取来源处理或纯业务说明，统一空值与首尾空白处理。"""
    value = mapping.get("businessDescription")
    return value.strip() if isinstance(value, str) else ""


def mapping_processing_type(mapping: dict[str, Any]) -> str:
    """读取当前来源映射的处理类型，避免消费者各自解释字段。"""
    return str(mapping.get("processingType") or "")


def source_identity(source: dict[str, Any]) -> tuple[str, ...]:
    """生成包含用途或 Operation 区段的稳定来源身份。"""
    keys = ("sourceType", "sourceId", "schema", "table", "column", "usage") if source.get("sourceType") == "database" else ("sourceType", "sourceId", "directoryId", "operationId", "section", "path")
    return tuple(str(source.get(key) or "") for key in keys)


def validate_unique_sources(sources: list[dict[str, Any]]) -> None:
    """拒绝同一映射内重复选择真实来源。"""
    identities = [source_identity(source) for source in sources]
    if len(set(identities)) != len(identities):
        raise ValueError("字段映射包含重复来源。")
