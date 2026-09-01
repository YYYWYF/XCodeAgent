"""Java/XML 业务 verifier 的共享 AST 匹配辅助函数。"""

from __future__ import annotations

import re
from typing import Any

from app.services.business_acceptance_verifiers.common import verification_result
from app.services.business_acceptance_verifiers.java_ast import (
    JavaAnnotation,
    JavaAstError,
    JavaAstModel,
    JavaMethod,
    JavaType,
    inspect_java_sources,
)


def _inspect_or_block(files: dict[str, str]) -> JavaAstModel | dict[str, Any]:
    """统一解析 Java/XML，并把 grammar 不支持收敛为 blocked。"""

    try:
        return inspect_java_sources(files)
    except JavaAstError as exc:
        return verification_result(
            "blocked",
            f"当前 Java/XML AST 无法安全解析目标源码：{exc}。",
            facts={"reason_code": "verifier_unsupported_syntax"},
        )


def _find_controller_method(
    controllers: list[JavaType], method: str, path: str
) -> tuple[JavaType, JavaMethod] | None:
    """按类型级与方法级 Mapping 组合匹配具体 Controller 方法。"""

    for controller in controllers:
        base_paths = _mapping_paths(controller.annotations) or [""]
        for handler in controller.methods:
            if _mapping_http_method(handler.annotations) != method:
                continue
            method_paths = _mapping_paths(handler.annotations) or [""]
            if any(_join_paths(base, leaf) == path for base in base_paths for leaf in method_paths):
                return controller, handler
    return None


def _mapping_http_method(annotations: list[JavaAnnotation]) -> str:
    """从 Spring Mapping 注解读取 HTTP method。"""

    names = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
    }
    for annotation in annotations:
        if annotation.name in names:
            return names[annotation.name]
        if annotation.name == "RequestMapping":
            match = re.search(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE)", annotation.text)
            if match:
                return match.group(1)
    return ""


def _mapping_paths(annotations: list[JavaAnnotation]) -> list[str]:
    """读取 Spring Mapping 注解中的静态路径。"""

    mapping_names = {"RequestMapping", "GetMapping", "PostMapping", "PutMapping", "PatchMapping", "DeleteMapping"}
    return [path for annotation in annotations if annotation.name in mapping_names for path in annotation.strings]


def _join_paths(base: str, leaf: str) -> str:
    """按 Spring 语义拼接类型级和方法级路径。"""

    parts = [part.strip("/") for part in (base, leaf) if part.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def _external_api_implemented(client: JavaType, method: str, path: str) -> bool:
    """验证 Feign 或项目 HTTP Client 的同方法 method/path 事实。"""

    if _find_controller_method([client], method, path) is not None:
        return True
    for handler in client.methods:
        method_matches = any(
            method.casefold() in call.method.casefold()
            or f"HttpMethod.{method}" in call.arguments
            for call in handler.calls
        )
        path_matches = any(path in call.strings for call in handler.calls)
        client_call = any(
            "client" in call.object_name.casefold()
            or "template" in call.object_name.casefold()
            or "webclient" in call.object_name.casefold()
            for call in handler.calls
        )
        if method_matches and path_matches and client_call:
            return True
    return False


def _operation_present(methods: list[JavaMethod], model: JavaAstModel, operation_kind: str) -> bool:
    """检查 Java 方法或 XML Mapper 操作是否承载指定操作语义。"""

    if any(_method_matches_operation(method.name, operation_kind) for method in methods):
        return True
    xml_tag = {
        "list": "select",
        "query": "select",
        "get": "select",
        "create": "insert",
        "update": "update",
        "delete": "delete",
    }.get(operation_kind, "")
    return bool(xml_tag and model.xml_operation_ids.get(xml_tag))


def _method_matches_operation(name: str, operation_kind: str) -> bool:
    """按方法名前缀把领域操作映射到 Java 分层入口。"""

    prefixes = {
        "list": ("find", "select", "query", "list", "page"),
        "query": ("find", "select", "query", "list", "page"),
        "get": ("find", "get", "select", "query"),
        "create": ("save", "insert", "create"),
        "update": ("save", "update", "modify"),
        "delete": ("delete", "remove"),
    }.get(operation_kind, (operation_kind,))
    return name.casefold().startswith(prefixes)


def _has_annotation(annotations: list[JavaAnnotation], name: str) -> bool:
    """判断声明是否携带指定简单注解名。"""

    return any(annotation.name == name for annotation in annotations)


def _type_has_suffix(java_type: JavaType, *suffixes: str) -> bool:
    """判断 Java 类型名是否属于指定分层角色。"""

    return _name_has_suffix(java_type.name, *suffixes)


def _name_has_suffix(name: str, *suffixes: str) -> bool:
    """忽略泛型和包名前缀判断 Java 类型后缀。"""

    simple = re.sub(r"<.*>", "", str(name or "")).rsplit(".", 1)[-1]
    return simple.endswith(suffixes)


def _java_type_matches(expected: Any, actual: str) -> bool:
    """按跨语言标量类别比较 EntityDesign 类型与 Java 类型。"""

    expected_name = str(expected or "").strip().casefold()
    actual_name = str(actual or "").strip().casefold()
    if not expected_name or not actual_name:
        return True
    if expected_name in {"string", "text", "varchar"}:
        return any(token in actual_name for token in ("string", "char"))
    if expected_name in {"number", "integer", "int", "long", "decimal"}:
        return any(token in actual_name for token in ("int", "long", "double", "float", "decimal", "bigdecimal"))
    if expected_name in {"boolean", "bool"}:
        return "bool" in actual_name
    if expected_name in {"array", "list"}:
        return "list" in actual_name or "[]" in actual_name or "collection" in actual_name
    return True


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """读取结构化对象列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    """读取结构化对象。"""

    return dict(value) if isinstance(value, dict) else {}
