"""字段取值规则的依赖校验与用户可读投影。"""

from typing import Any

from app.domain.api_design import BusinessQueryRight, EndpointQueryRight, FixedQueryRight


def validate_value_references(right: Any, nodes: list[dict[str, Any]]) -> None:
    """逐一校验规则依赖的当前请求快照，禁止引用响应或伪造字段。"""
    fields = right.endpoint_fields if isinstance(right, BusinessQueryRight) else [right.endpoint_field] if isinstance(right, EndpointQueryRight) else []
    expected = {(item.get("location"), item.get("path")): {key: item.get(key, default) for key, default in (
        ("side", "request"), ("location", ""), ("path", ""), ("type", "unknown"), ("required", False), ("description", ""),
    )} for item in nodes if item.get("side") == "request"}
    for field in fields:
        if expected.get((field.location, field.path)) != field.model_dump(by_alias=True):
            raise ValueError(f"取值规则引用的接口参数不属于当前 Endpoint：{field.path}。")


def value_rule_summary(right: Any) -> str:
    """把结构化依赖、规则和缺值策略完整投影到 Markdown 与构建摘要。"""
    if not isinstance(right, dict):
        return "未配置值来源"
    if right.get("kind") == "endpoint":
        field = right.get("endpointField") or {}
        return f"接口参数 {field.get('location')}.{field.get('path')}"
    if right.get("kind") == "fixed":
        return f"固定值 {right.get('value')!r}"
    if right.get("kind") != "business":
        return "未配置值来源"
    dependencies = [f"{field.get('location')}.{field.get('path')}" for field in right.get("endpointFields", [])]
    labels = {"current_user_id": "当前用户 ID", "current_time": "当前时间"}
    dependencies.extend(labels.get(key, key) for key in right.get("builtinFields", []))
    missing = {"error": "报错", "omit": "省略目标值（查询时跳过该条件）", "default": f"默认值 {right.get('defaultValue')!r}"}.get(right.get("missingBehavior", "error"))
    return f"业务规则：{right.get('businessDescription', '')}；依赖：{'、'.join(dependencies) or '无'}；缺值时：{missing}"
