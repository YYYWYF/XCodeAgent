"""前端静态数据模块的确定性业务契约检查。"""

from __future__ import annotations

from typing import Any

from app.services.business_acceptance_verifiers.common import (
    shape_matches,
    verification_result,
)
from app.services.business_acceptance_verifiers.typescript_ast import (
    TypeScriptAstError,
    inspect_typescript_sources,
)


def verify_static_data_contract_source(
    files: dict[str, str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """验证静态字段、种子数据、类型化操作和禁止的真实 HTTP 依赖。"""

    try:
        model = inspect_typescript_sources(files)
    except TypeScriptAstError as exc:
        return verification_result(
            "blocked",
            f"当前 TypeScript AST 无法安全解析静态数据模块：{exc}。",
            facts={"reason_code": "verifier_unsupported_syntax"},
        )
    direct_http_calls = [
        call
        for calls in model.calls_by_path.values()
        for call in calls
        if call == "fetch" or call == "axios" or call.startswith("axios.")
    ]
    if direct_http_calls:
        return verification_result(
            "failed",
            "静态数据模块调用真实 HTTP service。",
            facts={"direct_http_calls": list(dict.fromkeys(direct_http_calls))},
        )
    entity = expected.get("entity") if isinstance(expected.get("entity"), dict) else {}
    fields = [
        item for item in entity.get("fields", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    missing = [
        str(field["name"])
        for field in fields
        if str(field["name"]) not in model.object_keys
    ]
    if missing:
        return verification_result(
            "failed",
            "静态数据模块缺少实体字段：" + "、".join(missing),
            facts={"fields": [str(item["name"]) for item in fields if item.get("name")]},
        )
    static_design = expected.get("static_design") if isinstance(expected.get("static_design"), dict) else {}
    seed_rows = static_design.get("seed_rows") if isinstance(static_design.get("seed_rows"), list) else []
    for index, row in enumerate(seed_rows[:100]):
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if not _literal_present(model.literal_values, value):
                return verification_result("failed", f"静态种子数据缺少第 {index + 1} 行值：{key}。")
    exported_functions = model.functions
    exported = [function.export_symbol for function in exported_functions]
    endpoints = [item for item in expected.get("endpoints", []) if isinstance(item, dict)]
    operations = [item for item in expected.get("operations", []) if isinstance(item, dict)]
    if endpoints and not exported:
        return verification_result("failed", "静态数据模块未导出任何业务操作函数。")
    operation_errors: list[str] = []
    checked_operations: list[dict[str, str]] = []
    for operation in operations:
        endpoint_id = str(operation.get("endpoint_id") or "")
        operation_kind = str(operation.get("operation_kind") or "").lower()
        leaf = endpoint_id.rsplit(".", 1)[-1].lower()
        candidate = next(
            (
                name
                for name in exported
                if leaf and leaf in name.lower()
                or operation_kind and operation_kind in name.lower()
            ),
            "",
        )
        if not candidate:
            operation_errors.append(
                f"静态数据模块缺少 {operation_kind or '业务'} 操作导出（{endpoint_id}）。"
            )
            continue
        function = next(item for item in exported_functions if item.export_symbol == candidate)
        request_schema = _endpoint_schema(endpoints, endpoint_id, "request_schema")
        response_schema = _endpoint_schema(endpoints, endpoint_id, "response_schema")
        if request_schema and (
            not function.request_type
            or not shape_matches(request_schema, function.request_shape)
        ):
            operation_errors.append(f"静态操作 {candidate} 的请求结构不匹配（{endpoint_id}）。")
        if response_schema and (
            not function.response_type
            or not shape_matches(response_schema, function.response_shape)
        ):
            operation_errors.append(f"静态操作 {candidate} 的响应结构不匹配（{endpoint_id}）。")
        checked_operations.append({"endpoint_id": endpoint_id, "export_symbol": candidate})
    if operation_errors:
        return verification_result(
            "failed",
            "；".join(operation_errors),
            facts={"fields": [str(item["name"]) for item in fields], "operations": checked_operations},
        )
    return verification_result(
        "passed",
        "静态数据模块已包含正式字段和种子数据，且未调用真实 HTTP service。",
        facts={
            "fields": [str(item["name"]) for item in fields],
            "exports": exported[:100],
            "operations": checked_operations,
        },
    )


def _endpoint_schema(endpoints: list[dict[str, Any]], endpoint_id: str, key: str) -> dict[str, Any] | None:
    """读取静态操作对应 endpoint 的结构化请求或响应 schema。"""

    endpoint = next(
        (item for item in endpoints if str(item.get("endpoint_id") or "") == endpoint_id),
        {},
    )
    value = endpoint.get(key)
    return value if isinstance(value, dict) else None


def _literal_present(literals: set[str], value: Any) -> bool:
    """检查种子值是否作为 AST 字面量存在于模块中。"""

    if value is None:
        return "null" in literals
    if isinstance(value, bool):
        text = str(value).casefold()
    else:
        text = str(value).strip()
    if not text:
        return True
    return text in literals
