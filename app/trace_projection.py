"""读取本地 LangSmith 对象中的有效事实，不访问其网络客户端。"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def optional_identity(value: Any) -> str | None:
    """只接收有效单行字符串，不通过其他身份或随机值补齐。"""
    if isinstance(value, UUID):
        value = str(value)
    return value if isinstance(value, str) and value.strip() and len(value) <= 1024 and "\n" not in value and "\r" not in value else None


def token_count(value: Any) -> int | None:
    """保留真实零值，拒绝布尔、负数、浮点和字符串。"""
    return value if type(value) is int and value >= 0 else None


def trace_metadata(tree: Any) -> dict[str, Any]:
    """只读取本地 metadata，不触碰会懒创建客户端的 client 属性。"""
    value = getattr(tree, "metadata", None)
    return value if isinstance(value, dict) else {}


def utc_datetime(value: Any) -> datetime | None:
    """将带时区的追踪时间转成 UTC，拒绝无法确认时区的值。"""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def trace_time(tree: Any, metadata: dict[str, Any], attribute: str, fallback: str | None) -> str | None:
    """优先 SDK 实际边界并统一显示北京时间；只转换时区，不改变时刻或耗时。"""
    value = utc_datetime(fallback)
    if metadata.get("model_started") is not False:
        value = utc_datetime(getattr(tree, attribute, None)) or value
    return value.astimezone(BEIJING_TIMEZONE).isoformat() if value is not None else None


def trace_usage(tree: Any, metadata: dict[str, Any], fallback: dict[str, Any]) -> tuple[int | None, ...]:
    """整份优先选择追踪用量，缺字段保持未知，不跨快照拼造完整统计。"""
    outputs = getattr(tree, "outputs", None)
    raw = outputs.get("usage") if isinstance(outputs, dict) else None
    canonical = metadata.get("usage_metadata")
    candidates = (
        (canonical, ("input_tokens", "output_tokens", "total_tokens")),
        (raw, ("prompt_tokens", "completion_tokens", "total_tokens")),
        (fallback, ("prompt_tokens", "completion_tokens", "total_tokens")),
    )
    for usage, keys in candidates:
        if isinstance(usage, dict):
            values = tuple(token_count(usage.get(key)) for key in keys)
            if any(value is not None for value in values):
                return values
    return None, None, None


def trace_status(tree: Any, metadata: dict[str, Any], fallback: str) -> str:
    """优先明确执行状态，其次读取 SDK 终态，缺失时沿用本次执行结果。"""
    status = metadata.get("status")
    if status in ("completed", "failed", "cancelled", "interrupted", "awaiting_user"):
        return status
    error = getattr(tree, "error", None)
    if isinstance(error, str) and error.strip():
        return "failed"
    # end_time 只表示执行已结束，失败和取消也会有结束时间，不能据此推断成功。
    return fallback


def trace_metric(metadata: dict[str, Any], field: str, fallback: int | None) -> int | None:
    """读取有效的非负实测毫秒数，缺失或非法时使用当前观测。"""
    value = token_count(metadata.get(field))
    return value if value is not None else fallback


def trace_duration(tree: Any, metadata: dict[str, Any], fallback: int | None) -> int | None:
    """优先单调时钟实测值，再用当前观测，最后才使用有效 UTC 时间差。"""
    value = token_count(metadata.get("duration_ms"))
    if value is not None or fallback is not None:
        return value if value is not None else fallback
    if metadata.get("model_started") is False:
        return None
    start, end = (utc_datetime(getattr(tree, field, None)) for field in ("start_time", "end_time"))
    return int((end - start).total_seconds() * 1000) if start is not None and end is not None and end >= start else None
