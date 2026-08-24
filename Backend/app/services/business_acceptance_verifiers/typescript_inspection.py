"""基于 TypeScript AST 的前端 API 与页面业务契约检查。"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from app.services.business_acceptance_verifiers.common import shape_matches, verification_result
from app.services.business_acceptance_verifiers.typescript_ast import (
    TypeScriptAstError,
    TypeScriptAstModel,
    TypeScriptCall,
    TypeScriptFunction,
    inspect_typescript_sources,
)


_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def verify_api_contract_source(
    files: dict[str, str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """通过 AST 验证 API 导出、公共 client 调用和请求响应类型。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    direct_calls = _direct_http_calls(model, set(files))
    if direct_calls:
        return verification_result(
            "failed",
            "业务 API 模块直接调用 axios/fetch，未通过公共 service。",
            facts={"direct_http_calls": direct_calls},
        )
    endpoints = _dict_items(expected.get("endpoints"))
    declared_paths = {
        str(endpoint.get("path") or "")
        for endpoint in endpoints
        if str(endpoint.get("path") or "")
    }
    facts: list[dict[str, Any]] = []
    errors: list[str] = []
    for endpoint in endpoints:
        endpoint_id = str(endpoint.get("endpoint_id") or "")
        method = str(endpoint.get("method") or "GET").upper()
        path = str(endpoint.get("path") or "")
        matched = _find_endpoint_function(model.functions, method, path)
        if matched is None:
            errors.append(f"未找到实现 {method} {path} 的导出函数（{endpoint_id}）。")
            continue
        function, call = matched
        errors.extend(_parameter_contract_errors(endpoint, function, call))
        if endpoint.get("request_schema") and (
            not function.request_type
            or not shape_matches(endpoint.get("request_schema"), function.request_shape)
        ):
            errors.append(f"{function.export_symbol} 的请求类型不匹配 {endpoint_id}。")
        if endpoint.get("response_schema") and (
            not function.response_type
            or not shape_matches(endpoint.get("response_schema"), function.response_shape)
        ):
            errors.append(f"{function.export_symbol} 的响应类型不匹配 {endpoint_id}。")
        facts.append(
            {
                "endpoint_id": endpoint_id,
                "method": method,
                "path": path,
                "export_symbol": function.export_symbol,
                "request_type": function.request_type,
                "response_type": function.response_type,
            }
        )
    unexpected_paths = [
        call.path
        for function in model.functions
        for call in function.calls
        if _is_public_client_call(call)
        and call.method.casefold() in _HTTP_METHODS
        and call.path
        and not any(_paths_match(call.path, declared) for declared in declared_paths)
    ]
    if unexpected_paths:
        errors.append("业务 API 模块实现了契约外路径：" + "、".join(dict.fromkeys(unexpected_paths)))
    if errors:
        return verification_result("failed", "；".join(errors), facts={"endpoint_exports": facts})
    if not facts and endpoints:
        return verification_result(
            "blocked",
            "AST 未提取到可验证的 TypeScript endpoint 导出。",
            facts={"reason_code": "verifier_unsupported_syntax"},
        )
    return verification_result(
        "passed",
        f"已通过 AST 验证 {len(facts)} 个前端业务 API endpoint。",
        facts={"endpoint_exports": facts},
    )


def verify_page_endpoint_usage_source(
    files: dict[str, str],
    expected: dict[str, Any],
    dependency_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """通过 AST 验证页面可达代码对依赖 API 导出的真实调用。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    reachable_paths = _reachable_source_paths(files, model)
    direct_calls = _direct_http_calls(model, reachable_paths)
    if direct_calls:
        return verification_result(
            "failed",
            "页面或其任务内组件直接调用 axios/fetch。",
            facts={"direct_http_calls": direct_calls},
        )
    endpoint_paths = [
        str(endpoint.get("path") or "")
        for endpoint in _dict_items(expected.get("endpoints"))
        if str(endpoint.get("path") or "")
    ]
    hardcoded = [
        literal
        for path in reachable_paths
        for literal in model.string_literals_by_path.get(path, [])
        if any(_literal_contains_path(literal, endpoint_path) for endpoint_path in endpoint_paths)
    ]
    if hardcoded:
        return verification_result(
            "failed",
            "页面或其任务内组件硬编码业务 API URL。",
            facts={"hardcoded_paths": list(dict.fromkeys(hardcoded))},
        )
    exports = _dependency_exports(dependency_evidence or [])
    required = [str(item) for item in expected.get("required_endpoint_ids") or [] if str(item).strip()]
    if required and not exports:
        return verification_result(
            "blocked",
            "缺少已完成依赖 API 任务的结构化业务验收证据，无法确认页面调用归属。",
            facts={"reason_code": "dependency_evidence_unavailable"},
        )
    missing: list[str] = []
    used: list[dict[str, str]] = []
    for endpoint_id in required:
        symbol = exports.get(endpoint_id, "")
        binding = _called_import_binding(model, reachable_paths, symbol) if symbol else None
        if binding is None:
            missing.append(endpoint_id)
            continue
        used.append(
            {
                "endpoint_id": endpoint_id,
                "export_symbol": symbol,
                "local_symbol": binding,
            }
        )
    if missing:
        return verification_result(
            "failed",
            "页面未实际调用 required endpoint：" + "、".join(missing),
            facts={"endpoint_usage": used},
        )
    return verification_result(
        "passed",
        f"已通过 AST 确认页面调用 {len(used)} 个 required endpoint。",
        facts={"endpoint_usage": used, "reachable_files": sorted(reachable_paths)},
    )


def _inspect_or_block(files: dict[str, str]) -> TypeScriptAstModel | dict[str, Any]:
    """统一解析 TypeScript，并把 grammar 不支持收敛为 blocked。"""

    try:
        return inspect_typescript_sources(files)
    except TypeScriptAstError as exc:
        return verification_result(
            "blocked",
            f"当前 TypeScript AST 无法安全解析目标源码：{exc}。",
            facts={"reason_code": "verifier_unsupported_syntax"},
        )


def _find_endpoint_function(
    functions: list[TypeScriptFunction],
    method: str,
    path: str,
) -> tuple[TypeScriptFunction, TypeScriptCall] | None:
    """在导出函数 AST 内按公共 client、method 和 path 精确匹配 endpoint。"""

    expected_method = method.casefold()
    for function in functions:
        for call in function.calls:
            if (
                _is_public_client_call(call)
                and call.method.casefold() == expected_method
                and _paths_match(call.path, path)
            ):
                return function, call
    return None


def _is_public_client_call(call: TypeScriptCall) -> bool:
    """判断调用对象是否属于项目允许的公共 HTTP client 命名。"""

    name = call.object_name.rsplit(".", 1)[-1]
    return name in {"service", "client", "apiClient", "httpClient"} or name.endswith("Service")


def _paths_match(actual: str, expected: str) -> bool:
    """归一模板占位符后比较实现路径与正式契约路径。"""

    if not actual or not expected:
        return actual == expected
    actual_path = re.sub(
        r"\$\{[^}]*?([A-Za-z_$][\w$]*)\s*\}",
        lambda match: "{" + match.group(1) + "}",
        actual,
    )
    return actual_path == expected


def _parameter_contract_errors(
    endpoint: dict[str, Any],
    function: TypeScriptFunction,
    call: TypeScriptCall,
) -> list[str]:
    """验证参数字段存在于请求类型并传入正确的 HTTP 调用位置。"""

    parameters = _dict_items(endpoint.get("parameters"))
    if not parameters:
        return []
    errors: list[str] = []
    properties = function.request_shape.get("properties", {}) if isinstance(function.request_shape, dict) else {}
    call_arguments = " ".join(call.arguments)
    for parameter in parameters:
        name = str(parameter.get("name") or "")
        location = str(parameter.get("in") or "query").casefold()
        if not name:
            continue
        if name not in properties:
            errors.append(f"{function.export_symbol} 的请求类型缺少参数 {name}（{location}）。")
            continue
        expected_required = bool(parameter.get("required"))
        if expected_required and properties[name].get("required") is not True:
            errors.append(f"{function.export_symbol} 将必填参数 {name} 声明为可选。")
        if location in {"query", "body", "formdata"} and len(call.arguments) < 2:
            errors.append(f"{function.export_symbol} 未将 {location} 参数 {name} 传入 HTTP 调用。")
        if location == "path" and not re.search(rf"\b{re.escape(name)}\b", call_arguments):
            errors.append(f"{function.export_symbol} 未将 path 参数 {name} 放入请求路径。")
    return errors


def _dependency_exports(evidence: list[dict[str, Any]]) -> dict[str, str]:
    """从已通过的依赖证据读取 endpoint 到导出符号映射。"""

    return {
        str(item.get("endpoint_id")): str(item.get("export_symbol") or "")
        for result in evidence
        for item in _dict_items(result.get("facts", {}).get("endpoint_exports"))
        if result.get("status") == "passed" and item.get("endpoint_id")
    }


def _called_import_binding(
    model: TypeScriptAstModel,
    reachable_paths: set[str],
    export_symbol: str,
) -> str | None:
    """确认 API 导出经 import 绑定后在同一可达文件中作为调用目标出现。"""

    for path in reachable_paths:
        calls = set(model.calls_by_path.get(path, []))
        for imported in model.imports_by_path.get(path, []):
            if imported.namespace:
                local_call = f"{imported.local}.{export_symbol}"
                if local_call in calls:
                    return local_call
            elif imported.imported == export_symbol and imported.local in calls:
                return imported.local
    return None


def _direct_http_calls(model: TypeScriptAstModel, paths: set[str]) -> list[str]:
    """提取指定文件中绕过公共 service 的 axios/fetch 调用。"""

    return list(
        dict.fromkeys(
            call
            for path in paths
            for call in model.calls_by_path.get(path, [])
            if call == "fetch" or call == "axios" or call.startswith("axios.")
        )
    )


def _literal_contains_path(literal: str, path: str) -> bool:
    """判断字符串字面量是否直接承载正式业务 API 路径。"""

    return bool(path) and (
        literal == path
        or literal.startswith(f"{path}?")
        or literal.startswith(f"{path}#")
    )


def _reachable_source_paths(files: dict[str, str], model: TypeScriptAstModel) -> set[str]:
    """从页面入口沿 AST import 边建立任务内源码可达集合。"""

    if not files:
        return set()
    entry = next(
        (path for path in files if path.casefold().endswith("/index.tsx")),
        next(iter(files)),
    )
    reachable: set[str] = set()
    pending = [entry]
    while pending and len(reachable) < 40:
        current = pending.pop()
        if current in reachable or current not in files:
            continue
        reachable.add(current)
        for imported in model.imports_by_path.get(current, []):
            target = _resolve_import_path(current, imported.module, files)
            if target and target not in reachable:
                pending.append(target)
    return reachable


def _resolve_import_path(current_path: str, module_name: str, files: dict[str, str]) -> str:
    """按当前文件和有限别名解析任务内 TypeScript import 目标。"""

    module = module_name.replace("\\", "/")
    if module.startswith("."):
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(current_path), module))
    elif module.startswith("@/"):
        candidate = posixpath.normpath(posixpath.join("frontend/src", module[2:]))
    elif module.startswith("src/"):
        candidate = module
    else:
        return ""
    candidates = [
        candidate,
        *(f"{candidate}{extension}" for extension in (".ts", ".tsx", ".js", ".jsx")),
        f"{candidate}/index.tsx",
        f"{candidate}/index.ts",
    ]
    for item in candidates:
        if item in files:
            return item
        matching = [path for path in files if path.casefold() == item.casefold()]
        if len(matching) == 1:
            return matching[0]
    return ""


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """读取结构化事实列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
