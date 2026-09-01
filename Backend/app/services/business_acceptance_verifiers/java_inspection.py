"""基于 Java/XML AST 的后端分层业务契约检查。"""

from __future__ import annotations

import re
from typing import Any

from app.services.business_acceptance_verifiers.common import verification_result
from app.services.business_acceptance_verifiers.java_inspection_support import (
    _dict_items,
    _dict_value,
    _external_api_implemented,
    _find_controller_method,
    _has_annotation,
    _inspect_or_block,
    _method_matches_operation,
    _name_has_suffix,
    _operation_present,
    _type_has_suffix,
)


def _java_name_candidates(value: Any) -> set[str]:
    """把外部参数名转换为 Java 中常见的原名与 lowerCamelCase 候选。"""

    text = str(value or "").strip()
    if not text:
        return set()
    parts = [part for part in re.split(r"[^A-Za-z0-9_$]+", text) if part]
    camel = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
    return {text, camel}


def _contract_name_present(model: Any, value: Any) -> bool:
    """判断请求参数名是否真实出现在 Java 标识符或字符串字面量中。"""

    candidates = _java_name_candidates(value)
    return bool(candidates & (model.identifiers | model.literals))


def _configuration_key_present(model: Any, value: Any) -> bool:
    """识别配置键原文及 Spring ${配置键} 占位符字面量。"""

    key = str(value or "").strip()
    if not key:
        return False
    return key in model.identifiers or any(
        literal == key or f"${{{key}}}" in literal
        for literal in model.literals
    )


def _source_path_segments(value: Any) -> set[str]:
    """提取响应路径中的可执行 Java 标识符，并过滤根数组产生的空段。"""

    return {
        cleaned
        for segment in str(value or "").split(".")
        if (cleaned := segment.replace("[]", "").strip())
    }


def verify_repository_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """通过 AST 验证 Repository/Mapper 类型、操作方法和列绑定。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    repositories = [item for item in model.types if _type_has_suffix(item, "Repository", "Mapper")]
    if not repositories and "mapper" not in model.xml_tags:
        return verification_result("failed", "未找到 Repository 或 Mapper 类型。")
    methods = [method for item in repositories for method in item.methods]
    available = (
        model.identifiers
        | model.literals
        | {field.name for item in repositories for field in item.fields}
        | {identifier for method in methods for identifier in method.identifiers}
    )
    errors: list[str] = []
    operations = _dict_items(expected.get("operations"))
    for operation in operations:
        endpoint_id = str(operation.get("endpoint_id") or "")
        for field in _dict_value(operation.get("selector")).get("fields") or []:
            if str(field) not in available:
                errors.append(f"Repository 缺少 selector 字段 {field}（{endpoint_id}）。")
        operation_kind = str(operation.get("operation_kind") or "").casefold()
        if operation_kind and not _operation_present(methods, model, operation_kind):
            errors.append(f"Repository 缺少 {operation_kind} 操作方法（{endpoint_id}）。")
    for entity in _dict_items(expected.get("entities")):
        for binding in _dict_items(entity.get("database_bindings")):
            column = str(binding.get("table_column") or "")
            if column and column not in available:
                errors.append(f"Repository 缺少表列引用 {column}。")
    if errors:
        return verification_result("failed", "；".join(errors), facts={"operation_count": len(operations)})
    return verification_result(
        "passed",
        "已通过 AST 验证 Repository/Mapper 方法和数据库绑定。",
        facts={"operation_count": len(operations)},
    )


def verify_application_service_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """通过 AST 验证 ApplicationService 的 Repository 委托和事务入口。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    services = [item for item in model.types if _type_has_suffix(item, "Service", "ServiceImpl")]
    if not services:
        return verification_result("failed", "未找到 ApplicationService 类型。")
    operations = _dict_items(expected.get("operations"))
    errors: list[str] = []
    for service in services:
        repository_fields = {
            field.name for field in service.fields if _name_has_suffix(field.type_name, "Repository", "Mapper")
        }
        if operations and not repository_fields:
            errors.append(f"ApplicationService {service.name} 未声明 Repository/Mapper 依赖。")
            continue
        for operation in operations:
            operation_kind = str(operation.get("operation_kind") or "").casefold()
            candidates = [method for method in service.methods if _method_matches_operation(method.name, operation_kind)]
            if not candidates:
                errors.append(f"ApplicationService 缺少 {operation_kind} 操作入口（{operation.get('endpoint_id')}）。")
                continue
            method = candidates[0]
            if operation.get("transaction_required") is True and not _has_annotation(method.annotations, "Transactional"):
                errors.append(f"操作 {operation.get('endpoint_id')} 缺少 @Transactional。")
            for field in _dict_value(operation.get("selector")).get("fields") or []:
                if str(field) not in method.identifiers:
                    errors.append(f"操作 {operation.get('endpoint_id')} 缺少 selector 字段 {field}。")
            if repository_fields and not any(call.object_name in repository_fields for call in method.calls):
                errors.append(f"操作 {operation.get('endpoint_id')} 未调用 Repository/Mapper。")
    if errors:
        return verification_result("failed", "；".join(errors), facts={"operation_count": len(operations)})
    return verification_result(
        "passed",
        "已通过 AST 验证 ApplicationService 的 Repository 委托和操作语义。",
        facts={"operation_count": len(operations)},
    )


def verify_endpoint_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """通过 AST 验证 Controller Mapping、DTO、返回值和 Service 委托。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    controllers = [item for item in model.types if _type_has_suffix(item, "Controller", "Resource", "Endpoint", "Handler")]
    if not controllers:
        return verification_result("failed", "未找到 Controller/Resource 类型。")
    errors: list[str] = []
    checked: list[dict[str, str]] = []
    for endpoint in _dict_items(expected.get("endpoints")):
        method = str(endpoint.get("method") or "GET").upper()
        path = str(endpoint.get("path") or "")
        matched = _find_controller_method(controllers, method, path)
        if matched is None:
            errors.append(f"Controller 缺少 {method} {path} 的 Spring Mapping。")
            continue
        controller, handler = matched
        request_ref = str(endpoint.get("request_schema_ref") or "")
        if request_ref and not any(parameter.annotations for parameter in handler.parameters):
            errors.append(f"Controller 缺少请求 DTO/参数绑定（{endpoint.get('endpoint_id')}）。")
        response_name = str(endpoint.get("response_schema_ref") or "").rsplit("/", 1)[-1]
        if response_name and response_name not in handler.return_type and "ResponseEntity" not in handler.return_type:
            errors.append(f"Controller 缺少响应 DTO（{endpoint.get('endpoint_id')}）。")
        service_fields = {
            field.name for field in controller.fields if _name_has_suffix(field.type_name, "Service", "ServiceImpl")
        }
        repository_fields = [
            field for field in controller.fields if _name_has_suffix(field.type_name, "Repository", "Mapper")
        ]
        if not service_fields or not any(call.object_name in service_fields for call in handler.calls):
            errors.append(f"Controller 未委托 ApplicationService（{endpoint.get('endpoint_id')}）。")
        if repository_fields:
            errors.append("Controller 直接访问 Repository/Mapper，越过 ApplicationService。")
        checked.append({"endpoint_id": str(endpoint.get("endpoint_id") or ""), "method": method, "path": path})
    if errors:
        return verification_result("failed", "；".join(errors), facts={"endpoints": checked})
    return verification_result(
        "passed",
        "已通过 AST 验证 Controller method/path、DTO 和 Service 委托。",
        facts={"endpoints": checked},
    )


def verify_external_client_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """通过 AST 验证外部 API Client 的连接配置、method/path 和调用事实。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    clients = [item for item in model.types if _type_has_suffix(item, "Client", "Gateway", "Adapter")]
    errors: list[str] = []
    for api in _dict_items(expected.get("external_apis")):
        info = _dict_value(api.get("api_info"))
        method = str(info.get("method") or "GET").upper()
        path = str(info.get("path") or "")
        matched = any(_external_api_implemented(client, method, path) for client in clients)
        if not matched:
            errors.append(f"外部 API Client 缺少 {method} {path} 的 HTTP 调用声明。")
        config_key = str(info.get("base_url_config_key") or "").strip()
        if config_key and not _configuration_key_present(model, config_key):
            errors.append(f"外部 API Client 未读取 Base URL 配置键 {config_key}。")
        for parameter in _dict_items(info.get("parameters")):
            parameter_name = str(parameter.get("name") or "").strip()
            if parameter_name and not _contract_name_present(model, parameter_name):
                errors.append(f"外部 API Client 未实现请求参数 {parameter_name}。")
    if any(_type_has_suffix(item, "Repository", "Mapper") for item in model.types):
        errors.append("外部 API Client 不得引入持久化 Repository/Mapper。")
    if errors:
        return verification_result("failed", "；".join(errors))
    return verification_result(
        "passed",
        "已通过 AST 验证外部 API Client method/path，且未引入持久化层。",
        facts={"api_count": len(_dict_items(expected.get("external_apis")))},
    )


def verify_external_mapping_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """通过 AST 验证外部 source_field 到 entity_field 的同方法映射。"""

    model = _inspect_or_block(files)
    if isinstance(model, dict):
        return model
    mapping_methods = [
        method
        for item in model.types
        if _type_has_suffix(item, "Mapper", "Converter", "Assembler", "Adapter")
        for method in item.methods
    ]
    errors: list[str] = []
    mappings = 0
    entity_payload_apis = 0
    for api in _dict_items(expected.get("external_apis")):
        response_handling = _dict_value(api.get("response_handling"))
        if response_handling.get("entity_payload") is not True:
            continue
        entity_payload_apis += 1
        payload_path = str(response_handling.get("payload_path") or "").strip()
        payload_segments = _source_path_segments(payload_path)
        if payload_segments and not any(
            payload_segments.issubset(method.identifiers)
            for method in mapping_methods
        ):
            errors.append(f"缺少外部 API 实体载荷路径解析：{payload_path}。")
        for mapping in _dict_items(api.get("field_mappings")):
            source_field = str(mapping.get("source_field") or "")
            entity_field = str(mapping.get("entity_field") or "")
            if not source_field or not entity_field:
                return verification_result("blocked", "正式外部 API field_mappings 缺少 source_field 或 entity_field。")
            required = _source_path_segments(source_field)
            required.add(entity_field)
            if not any(required.issubset(method.identifiers) for method in mapping_methods):
                errors.append(f"缺少外部字段到实体字段的同方法映射 {source_field} -> {entity_field}。")
            mappings += 1
    if errors:
        return verification_result("failed", "；".join(errors), facts={"mapping_count": mappings})
    if entity_payload_apis == 0:
        return verification_result("passed", "上游操作均为非实体响应，无需字段映射。", facts={"mapping_count": 0})
    if not mappings:
        return verification_result("blocked", "正式输入没有可执行的外部 API 字段映射。")
    return verification_result("passed", f"已通过 AST 验证 {mappings} 条外部 API 字段映射。", facts={"mapping_count": mappings})
