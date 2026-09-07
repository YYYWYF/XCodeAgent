"""Endpoint 动态实体映射的确定性候选、校验与就绪规则。"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from app.domain.api_design import (
    API_DESIGN_SCHEMA_VERSION,
    ApiDesignAction,
    BusinessDescriptionFieldMapping,
    DatabaseSourceField,
    DirectSourceFieldMapping,
    EndpointFieldNode,
    EndpointFieldMappingDesign,
    EntityTemplate,
    EntityTemplateField,
    FieldMapping,
    ExternalSourceField,
    SceneEntity,
    SceneEntityField,
    ThroughEntityFieldMapping,
    UnconfiguredFieldMapping,
)
from app.services.api_schema_refs import normalize_local_schema_ref
from app.services.api_design_schema import resolve_mapping_schema
from app.services.data_sources import (
    DataSourceError,
    public_catalog,
    resolve_direct_database_config,
)
from app.services.frontend_page_tree import project_plan_page_records
from app.tools.mysql_info import mysql_table_info
from app.workspace.endpoint_design_documents import (
    endpoint_design_status,
    read_endpoint_design,
    technical_plan_sha256,
    write_endpoint_design,
)


_FIELD_MAPPING_ADAPTER = TypeAdapter(FieldMapping)


class ApiDesignError(ValueError):
    """表示 API 动态映射目标、来源或关系不符合当前契约。"""


def normalize_api_design_action(value: Any) -> dict[str, Any] | None:
    """把不可信的结构化交互输入校验为当前 API 设计动作。"""

    if not isinstance(value, dict):
        return None
    try:
        return ApiDesignAction.model_validate(value).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
    except ValueError as exc:
        raise ApiDesignError(str(exc)) from exc


def find_endpoint(
    project_plan: dict[str, Any],
    api_contract_id: str,
    endpoint_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按复合标识唯一定位 TechnicalPlan 中的 API Contract 和 Endpoint。"""

    contract = next(
        (
            item
            for item in _dict_items(project_plan.get("api_contracts"))
            if str(item.get("id") or "") == api_contract_id
        ),
        None,
    )
    if contract is None:
        raise ApiDesignError(f"TechnicalPlan 不包含 API Contract：{api_contract_id}。")
    endpoint = next(
        (
            item
            for item in _dict_items(contract.get("endpoints"))
            if str(item.get("id") or "") == endpoint_id
        ),
        None,
    )
    if endpoint is None:
        raise ApiDesignError(f"TechnicalPlan 不包含 Endpoint：{api_contract_id}/{endpoint_id}。")
    return contract, endpoint


def endpoint_api_fields(
    contract: dict[str, Any],
    endpoint: dict[str, Any],
) -> list[dict[str, Any]]:
    """枚举 Path、Query、Header、请求体和响应体中真正可映射的叶子字段。"""

    fields: list[EndpointFieldNode] = []
    for parameter in _dict_items(endpoint.get("parameters")):
        location = str(parameter.get("in") or "query")
        if location not in {"path", "query", "header"}:
            continue
        path = str(parameter.get("name") or "parameter")
        fields.append(
            EndpointFieldNode(
                id=_endpoint_node_id("request", location, path),
                side="request",
                location=location,
                path=path,
                type=_schema_type(parameter.get("schema")),
                required=location == "path" or bool(parameter.get("required")),
                description=str(parameter.get("description") or ""),
            )
        )
    schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
    contract_id = str(contract.get("id") or "")
    request_ref = str(endpoint.get("request_schema_ref") or "").strip()
    response_ref = str(endpoint.get("response_schema_ref") or "").strip()
    if request_ref:
        fields.extend(
            _schema_leaf_fields(
                schemas.get(normalize_local_schema_ref(request_ref, contract_id=contract_id)),
                schemas,
                side="request",
                location="request_body",
                required=True,
            )
        )
    if response_ref:
        fields.extend(
            _schema_leaf_fields(
                schemas.get(normalize_local_schema_ref(response_ref, contract_id=contract_id)),
                schemas,
                side="response",
                location="response_body",
                required=True,
            )
        )
    return [item.model_dump(mode="json", by_alias=True) for item in _unique_endpoint_fields(fields)]


def endpoint_field_nodes(contract: dict[str, Any], endpoint: dict[str, Any]) -> list[dict[str, Any]]:
    """返回与 Endpoint 映射草稿一致的规范 Endpoint Field 节点。"""

    return endpoint_api_fields(contract, endpoint)


def entity_templates(
    project_plan: dict[str, Any],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """把当前 Contract 关联的全局实体投影为只读复制模板。"""

    entity_ids = {str(item).strip() for item in contract.get("entity_ids") or [] if str(item).strip()}
    entities = {
        str(item.get("id") or ""): item
        for item in _dict_items(project_plan.get("entities"))
        if str(item.get("id") or "").strip()
    }
    templates: list[dict[str, Any]] = []
    for entity_id in sorted(entity_ids):
        entity = entities.get(entity_id)
        if entity is None:
            continue
        fields = [
            EntityTemplateField(
                name=str(field.get("name") or field.get("path") or "field"),
                label=str(field.get("label") or field.get("name") or "field"),
                type=str(field.get("type") or "unknown"),
                required=bool(field.get("required")),
                description=str(field.get("description") or ""),
            )
            for field in _dict_items(entity.get("fields"))
            if str(field.get("name") or field.get("path") or "").strip()
        ]
        templates.append(
            EntityTemplate(
                id=entity_id,
                name=str(entity.get("name") or entity_id),
                description=str(entity.get("description") or ""),
                fields=fields,
            ).model_dump(mode="json", by_alias=True)
        )
    return templates


def initial_api_design_payload(
    workspace_root: str | Path,
    project_plan: dict[str, Any],
    api_contract_id: str,
    endpoint_id: str,
) -> dict[str, Any]:
    """构造动态映射工作台所需的契约、模板和有界草稿。

    数据源目录及实时元数据由独立数据源接口读取，不能通过工作流 run
    快照返回，避免把连接目录和元数据混入工作流状态。
    """

    contract, endpoint = find_endpoint(project_plan, api_contract_id, endpoint_id)
    endpoint_nodes = endpoint_field_nodes(contract, endpoint)
    existing = read_endpoint_design(workspace_root, api_contract_id, endpoint_id, require_current=False)
    if isinstance(existing, dict) and isinstance(existing.get("fieldMappings"), list):
        draft = {
            "apiContractId": api_contract_id,
            "endpointId": endpoint_id,
            "implementationDescription": str(existing.get("implementationDescription") or ""),
            "sceneEntities": existing.get("sceneEntities", []),
            "fieldMappings": existing.get("fieldMappings", []),
        }
    else:
        draft = {
            "apiContractId": api_contract_id,
            "endpointId": endpoint_id,
            "implementationDescription": "",
            "sceneEntities": [],
            "fieldMappings": [
                {
                    "endpointField": _endpoint_field_snapshot(field),
                    "mappingType": "unconfigured",
                }
                for field in endpoint_nodes
            ],
        }
    return {
        "endpoint": {**endpoint, "apiContractId": api_contract_id},
        "endpointFields": endpoint_nodes,
        "entityTemplates": entity_templates(project_plan, contract),
        "draft": draft,
        "existingStatus": endpoint_design_status(workspace_root, api_contract_id, endpoint_id),
    }


def load_database_tables(workspace_root: str | Path, source_id: str) -> dict[str, Any]:
    """实时读取直属 MySQL 表清单，不返回任何连接凭据。"""

    payload = _mysql_metadata(workspace_root, source_id)
    return {
        "sourceId": source_id,
        "schema": str(payload.get("database") or ""),
        "tables": [
            {"name": str(item.get("table_name") or ""), "description": str(item.get("comment") or "")}
            for item in _dict_items(payload.get("tables"))
            if str(item.get("table_name") or "").strip()
        ],
    }


def load_database_columns(workspace_root: str | Path, source_id: str, table: str) -> dict[str, Any]:
    """实时读取直属 MySQL 单表字段并转换为前端候选结构。"""

    payload = _mysql_metadata(workspace_root, source_id, table=table)
    schemas = payload.get("schemas") if isinstance(payload.get("schemas"), dict) else {}
    columns = schemas.get(table, [])
    return {
        "sourceId": source_id,
        "schema": str(payload.get("database") or ""),
        # 列查询也回传完整表清单，保证 AG-UI 的下一帧不会让前端级联选择器丢失上一级选项。
        "tables": [
            {"name": str(item.get("table_name") or ""), "description": str(item.get("comment") or "")}
            for item in _dict_items(payload.get("tables"))
            if str(item.get("table_name") or "").strip()
        ],
        "table": table,
        "columns": [
            {
                "name": str(item.get("column_name") or ""),
                "type": str(item.get("column_type") or "unknown"),
                "required": str(item.get("is_nullable") or "YES") == "NO",
                "description": str(item.get("comment") or ""),
            }
            for item in _dict_items(columns)
            if str(item.get("column_name") or "").strip()
        ],
    }


def load_external_operation(
    workspace_root: str | Path,
    source_id: str,
    directory_id: str,
    operation_id: str,
) -> dict[str, Any]:
    """读取外部 API 目录中的最新 Operation Schema，不发起真实网络请求。"""

    catalog = public_catalog(workspace_root, source_id=source_id, operation_id=operation_id)
    source = next(
        (
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in catalog.sources
            if item.id == source_id
        ),
        None,
    )
    if source is None or source.get("type") != "external_api":
        raise ApiDesignError("目标外部 API 数据源不存在。")
    directory = next(
        (item for item in _dict_items(source.get("directories")) if str(item.get("id") or "") == directory_id),
        None,
    )
    if directory is None:
        raise ApiDesignError("目标外部 API 目录不存在。")
    operation = next(
        (item for item in _dict_items(directory.get("operations")) if str(item.get("id") or "") == operation_id),
        None,
    )
    if operation is None:
        raise ApiDesignError("目标外部 API Operation 不存在。")
    safe_operation = _safe_external_operation(operation)
    return {
        "sourceId": source_id,
        "sourceName": str(source.get("name") or source_id),
        "connection": {
            "baseUrl": source.get("baseUrl"),
            "baseUrlConfigKey": source.get("baseUrlConfigKey"),
            "timeoutMs": source.get("timeoutMs"),
            "headers": [{"name": item.get("name")} for item in _dict_items(source.get("headers"))],
        },
        "directoryId": directory_id,
        "directoryName": str(directory.get("name") or directory_id),
        "operation": safe_operation,
        "fields": _external_operation_fields(safe_operation),
    }


def confirm_api_design(
    workspace_root: str | Path,
    project_plan: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    """按当前 TechnicalPlan 和来源重新校验动态映射草稿并写入双文件产物。"""

    parsed = ApiDesignAction.model_validate(action)
    if parsed.action != "confirm":
        raise ApiDesignError("当前动作不是 API 设计确认。")
    contract, endpoint = find_endpoint(project_plan, parsed.api_contract_id, parsed.endpoint_id)
    draft = parsed.draft
    if not isinstance(draft, dict):
        raise ApiDesignError("API 设计草稿必须是对象。")
    if str(draft.get("apiContractId") or parsed.api_contract_id) != parsed.api_contract_id:
        raise ApiDesignError("API 设计草稿的 API Contract 标识与确认目标不一致。")
    if str(draft.get("endpointId") or parsed.endpoint_id) != parsed.endpoint_id:
        raise ApiDesignError("API 设计草稿的 Endpoint 标识与确认目标不一致。")
    endpoint_nodes = endpoint_field_nodes(contract, endpoint)
    normalized = _validate_design(
        workspace_root,
        project_plan,
        contract,
        endpoint,
        endpoint_nodes,
        draft,
    )
    design = EndpointFieldMappingDesign.model_validate(
        {
            "apiContractId": parsed.api_contract_id,
            "endpointId": parsed.endpoint_id,
            "endpointContract": endpoint,
            **normalized,
            "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_plan_sha256(workspace_root)}],
            "artifactRevision": uuid4().hex,
            "confirmedAt": datetime.now(UTC),
        }
    )
    paths = write_endpoint_design(workspace_root, design)
    return {
        "status": "confirmed",
        "apiContractId": parsed.api_contract_id,
        "endpointId": parsed.endpoint_id,
        "design": design.model_dump(mode="json", by_alias=True, exclude_none=True),
        "artifacts": paths,
    }


def api_design_readiness(
    workspace_root: str | Path,
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    api_contract_id: str | None = None,
) -> dict[str, Any]:
    """检查页面或 Endpoint 直接依赖的当前版动态映射是否全部有效。"""

    targets = _target_endpoints(
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=api_contract_id,
    )
    missing: list[dict[str, Any]] = []
    for contract, endpoint in targets:
        contract_id = str(contract.get("id") or "")
        endpoint_id = str(endpoint.get("id") or "")
        status = endpoint_design_status(workspace_root, contract_id, endpoint_id)
        if status["designed"]:
            try:
                design = read_endpoint_design(workspace_root, contract_id, endpoint_id)
                if design is None:
                    raise ApiDesignError("API 设计正式产物无法读取。")
                _validate_persisted_design(project_plan, contract, endpoint, design)
            except (ApiDesignError, ValueError) as exc:
                status = {
                    "status": "stale",
                    "designed": False,
                    "reason": str(exc),
                }
        if not status["designed"]:
            missing.append(
                {
                    "api_contract_id": contract_id,
                    "endpoint_id": endpoint_id,
                    "method": str(endpoint.get("method") or "API"),
                    "path": str(endpoint.get("path") or endpoint_id),
                    "status": status["status"],
                    "reason": status["reason"],
                }
            )
    return {
        "ready": not missing,
        "target_type": target_type,
        "target_id": target_id,
        "api_contract_id": api_contract_id,
        "endpoint_ids": [str(endpoint.get("id") or "") for _, endpoint in targets],
        "missing_api_designs": missing,
    }


def load_confirmed_endpoint_designs(
    workspace_root: str | Path,
    project_plan: dict[str, Any],
    endpoint_ids: list[str],
    *,
    api_contract_id: str | None = None,
) -> list[dict[str, Any]]:
    """为 Build 上下文加载指定 Endpoint 的当前版已确认动态映射。"""

    result: list[dict[str, Any]] = []
    target_ids = set(endpoint_ids)
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        if api_contract_id and contract_id != api_contract_id:
            continue
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if endpoint_id not in target_ids:
                continue
            design = read_endpoint_design(workspace_root, contract_id, endpoint_id)
            if design is None:
                raise ApiDesignError(f"Endpoint {contract_id}/{endpoint_id} 缺少当前版已确认动态映射。")
            _validate_persisted_design(project_plan, contract, endpoint, design)
            result.append(design)
    found = {str(item.get("endpointId") or "") for item in result}
    missing = [endpoint_id for endpoint_id in endpoint_ids if endpoint_id not in found]
    if missing:
        raise ApiDesignError(f"TechnicalPlan 不包含 Endpoint：{', '.join(missing)}。")
    return result


def api_design_source_types(designs: list[dict[str, Any]]) -> list[str]:
    """从自包含字段映射中提取有序去重的数据源类型。"""

    result: list[str] = []
    for design in designs:
        for mapping in _dict_items(design.get("fieldMappings")):
            source = mapping.get("sourceField") if isinstance(mapping.get("sourceField"), dict) else {}
            source_type = str(source.get("sourceType") or "")
            if source_type and source_type not in result:
                result.append(source_type)
    return result


def api_design_entity_ids(designs: list[dict[str, Any]]) -> list[str]:
    """从场景实体中提取业务实体 ID，不读取实体全局绑定。"""

    result: list[str] = []
    for design in designs:
        for entity in _dict_items(design.get("sceneEntities")):
            entity_id = str(entity.get("id") or "")
            if entity_id and entity_id not in result:
                result.append(entity_id)
    return result


def api_design_mapping_flows(designs: list[dict[str, Any]]) -> list[str]:
    """把字段映射转换为可读的数据流表达式供 Prompt、日志和验收使用。"""

    flows: list[str] = []
    for design in designs:
        for mapping in _dict_items(design.get("fieldMappings")):
            endpoint = _endpoint_field_label(mapping.get("endpointField"))
            mapping_type = str(mapping.get("mappingType") or "")
            if mapping_type == "business_description":
                description = str(mapping.get("businessDescription") or "").strip()
                if endpoint and description:
                    flows.append(f"{endpoint} ⇒ 业务说明：{description}")
                continue
            if mapping_type == "unconfigured" or not endpoint:
                continue
            entity = _entity_field_label(mapping.get("entityField"))
            source = _source_field_label(mapping.get("sourceField"))
            middle = [label for label in (entity, source) if label]
            endpoint_field = (
                mapping.get("endpointField")
                if isinstance(mapping.get("endpointField"), dict)
                else {}
            )
            labels = (
                [endpoint, *middle]
                if endpoint_field.get("side") == "request"
                else [*reversed(middle), endpoint]
            )
            if len(labels) > 1:
                flows.append(" → ".join(labels))
    return list(dict.fromkeys(flows))


def api_design_business_descriptions(designs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取 Request/Response 字段的一句话业务说明，供构建、任务规划和验收使用。"""

    descriptions: list[dict[str, Any]] = []
    for design in designs:
        for mapping in _dict_items(design.get("fieldMappings")):
            if mapping.get("mappingType") != "business_description":
                continue
            endpoint = mapping.get("endpointField") if isinstance(mapping.get("endpointField"), dict) else {}
            descriptions.append(
                {
                    "api_contract_id": str(design.get("apiContractId") or ""),
                    "endpoint_id": str(design.get("endpointId") or ""),
                    "path": str(endpoint.get("path") or ""),
                    "side": str(endpoint.get("side") or ""),
                    "location": str(endpoint.get("location") or ""),
                    "type": str(endpoint.get("type") or "unknown"),
                    "required": bool(endpoint.get("required")),
                    "description": str(mapping.get("businessDescription") or ""),
                }
            )
    return descriptions


def _validate_design(
    workspace_root: str | Path,
    project_plan: dict[str, Any],
    contract: dict[str, Any],
    endpoint: dict[str, Any],
    endpoint_nodes: list[dict[str, Any]],
    draft: dict[str, Any],
) -> dict[str, Any]:
    """校验自包含字段映射、来源真实性和必填 Endpoint 字段覆盖。"""

    if any(key in draft for key in ("nodes", "mappings", "fieldBindings")):
        raise ApiDesignError("API 设计草稿必须使用当前 fieldMappings 结构。")
    scene_entities = _parse_entities(draft.get("sceneEntities"))
    field_mappings = _parse_field_mappings(draft.get("fieldMappings"))
    _validate_entities(scene_entities, entity_templates(project_plan, contract))
    _validate_field_mappings(field_mappings, endpoint_nodes, scene_entities)
    implementation_description = _normalize_implementation_description(
        draft.get("implementationDescription")
    )
    snapshots = _validated_source_snapshots(workspace_root, field_mappings)
    normalized = {
        "sceneEntities": [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in scene_entities],
        "fieldMappings": [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in field_mappings],
        "sourceSnapshots": snapshots,
    }
    if implementation_description is not None:
        normalized["implementationDescription"] = implementation_description
    return normalized


def _normalize_implementation_description(value: Any) -> str | None:
    """规范化 Endpoint 级实现描述；空白内容表示未填写。"""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiDesignError("API 实现描述必须是文本。")
    normalized = value.strip()
    if len(normalized) > 4000:
        raise ApiDesignError("API 实现描述不能超过 4000 个字符。")
    return normalized or None


def _parse_entities(value: Any) -> list[SceneEntity]:
    """解析场景实体列表并拒绝旧字段绑定结构。"""

    if not isinstance(value, list):
        raise ApiDesignError("草稿缺少 sceneEntities 列表。")
    try:
        return [SceneEntity.model_validate(item) for item in value]
    except ValueError as exc:
        raise ApiDesignError(f"场景实体结构无效：{exc}") from exc


def _parse_field_mappings(value: Any) -> list[Any]:
    """解析每个 Endpoint 字段的一条自包含映射记录。"""

    if not isinstance(value, list):
        raise ApiDesignError("草稿缺少 fieldMappings 列表。")
    try:
        return [_FIELD_MAPPING_ADAPTER.validate_python(item) for item in value]
    except ValueError as exc:
        raise ApiDesignError(f"字段映射结构无效：{exc}") from exc


def _validate_entities(
    entities: list[SceneEntity],
    templates: list[dict[str, Any]],
) -> None:
    """确保场景实体只能是当前 Contract 的唯一只读模板副本。"""

    entity_ids: set[str] = set()
    template_by_id = {str(item.get("id") or ""): item for item in templates}
    used_template_ids: set[str] = set()
    for entity in entities:
        if entity.id in entity_ids:
            raise ApiDesignError(f"场景实体 ID 重复：{entity.id}。")
        entity_ids.add(entity.id)
        template_id = entity.template_entity_id
        template = template_by_id.get(template_id)
        if template is None:
            raise ApiDesignError(
                f"场景实体 {entity.id} 未引用当前 Contract 的 TechnicalPlan 实体模板：{template_id}。"
            )
        if template_id in used_template_ids:
            raise ApiDesignError(f"TechnicalPlan 实体模板 {template_id} 在当前 Endpoint 中只能复制一次。")
        used_template_ids.add(template_id)
        if entity.name != str(template.get("name") or template_id) or entity.description != str(
            template.get("description") or ""
        ):
            raise ApiDesignError(f"场景实体 {entity.id} 的实体定义已偏离 TechnicalPlan 模板。")
        template_fields = [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or ""),
                "type": str(field.get("type") or "unknown"),
                "required": bool(field.get("required")),
                "description": str(field.get("description") or ""),
            }
            for field in _dict_items(template.get("fields"))
        ]
        actual_fields = [
            {
                "name": field.name,
                "label": field.label,
                "type": field.type,
                "required": field.required,
                "description": field.description,
            }
            for field in entity.fields
        ]
        if actual_fields != template_fields:
            raise ApiDesignError(
                f"场景实体 {entity.id} 的字段只能使用 TechnicalPlan 模板字段，不能新增、删除或修改。"
            )
        field_ids: set[str] = set()
        field_names: set[str] = set()
        for field in entity.fields:
            if field.id in field_ids or field.name in field_names:
                raise ApiDesignError(f"场景实体 {entity.id} 的字段 ID 或名称重复。")
            field_ids.add(field.id)
            field_names.add(field.name)


def _validate_persisted_design(
    project_plan: dict[str, Any],
    contract: dict[str, Any],
    endpoint: dict[str, Any],
    design: dict[str, Any],
) -> None:
    """重新校验已确认产物的当前字段映射，防止手工残缺产物进入 Build。"""

    persisted_endpoint = design.get("endpointContract")
    if not isinstance(persisted_endpoint, dict) or persisted_endpoint != endpoint:
        raise ApiDesignError("已确认产物中的 Endpoint 定义已被修改。")
    scene_entities = _parse_entities(design.get("sceneEntities"))
    field_mappings = _parse_field_mappings(design.get("fieldMappings"))
    _validate_entities(scene_entities, entity_templates(project_plan, contract))
    current_endpoint_nodes = endpoint_field_nodes(contract, endpoint)
    _validate_field_mappings(field_mappings, current_endpoint_nodes, scene_entities)


def _validate_field_mappings(
    mappings: list[Any],
    endpoint_nodes: list[dict[str, Any]],
    entities: list[SceneEntity],
) -> None:
    """校验字段唯一性、Endpoint 快照、实体引用、来源方向和类型兼容性。"""

    expected = {
        _endpoint_field_key(item): _endpoint_field_snapshot(item)
        for item in endpoint_nodes
    }
    actual: dict[tuple[str, str, str], Any] = {}
    entity_fields = {
        (entity.id, field.id): field
        for entity in entities
        for field in entity.fields
    }
    for mapping in mappings:
        endpoint = mapping.endpoint_field
        endpoint_payload = endpoint.model_dump(mode="json", by_alias=True)
        key = _endpoint_field_key(endpoint_payload)
        if key in actual:
            raise ApiDesignError(f"Endpoint 字段映射重复：{key}。")
        expected_endpoint = expected.get(key)
        if expected_endpoint is None:
            raise ApiDesignError(f"字段映射包含 TechnicalPlan 不存在的 Endpoint 字段：{key}。")
        if endpoint_payload != expected_endpoint:
            raise ApiDesignError(f"Endpoint 字段定义被草稿修改：{key}。")
        actual[key] = mapping
        if isinstance(mapping, UnconfiguredFieldMapping):
            if endpoint.required:
                raise ApiDesignError(f"必填 Endpoint 字段尚未配置映射：{endpoint.path}。")
            continue
        if isinstance(mapping, BusinessDescriptionFieldMapping):
            continue
        source_field = _mapping_source_field(mapping)
        if isinstance(mapping, ThroughEntityFieldMapping):
            reference = mapping.entity_field
            entity_field = entity_fields.get((reference.entity_id, reference.field_id))
            if entity_field is None:
                raise ApiDesignError(
                    f"实体字段引用不存在：{reference.entity_id}.{reference.field_id}。"
                )
            if reference.path != entity_field.name or reference.type != entity_field.type:
                raise ApiDesignError(
                    f"实体字段引用与场景实体定义不一致：{reference.entity_id}.{reference.field_id}。"
                )
            if not _types_compatible(endpoint.type, reference.type):
                raise ApiDesignError(f"Endpoint 与实体字段类型不兼容：{endpoint.path} → {reference.path}。")
            if source_field is not None and not _types_compatible(reference.type, source_field.type):
                raise ApiDesignError(
                    f"实体与数据源字段类型不兼容：{reference.path} → {_source_field_label(source_field)}。"
                )
        elif isinstance(mapping, DirectSourceFieldMapping):
            if not _types_compatible(endpoint.type, mapping.source_field.type):
                raise ApiDesignError(
                    f"Endpoint 与数据源字段类型不兼容：{endpoint.path} → {_source_field_label(source_field)}。"
                )
        if source_field is not None:
            _validate_source_for_endpoint(endpoint, source_field)

    expected_keys = set(expected)
    if set(actual) != expected_keys:
        missing = sorted(expected_keys - set(actual))
        extra = sorted(set(actual) - expected_keys)
        raise ApiDesignError(f"Endpoint 字段集合与当前契约不一致，缺少 {missing}，多出 {extra}。")


def _mapping_source_field(mapping: Any) -> DatabaseSourceField | ExternalSourceField | None:
    """读取直接来源或经实体映射中的可选来源字段。"""

    if isinstance(mapping, DirectSourceFieldMapping):
        return mapping.source_field
    if isinstance(mapping, ThroughEntityFieldMapping):
        return mapping.source_field
    return None


def _validate_source_for_endpoint(endpoint: Any, source_field: Any) -> None:
    """按 Endpoint 方向校验外部字段区段和数据库字段用途。"""

    if isinstance(source_field, ExternalSourceField):
        if endpoint.side == "request" and source_field.section == "response_body":
            raise ApiDesignError("Request Endpoint 字段不能映射到外部 API 响应字段。")
        if endpoint.side == "response" and source_field.section != "response_body":
            raise ApiDesignError("Response Endpoint 字段只能映射外部 API 响应字段。")
        return
    if not isinstance(source_field, DatabaseSourceField):
        return
    if endpoint.side == "request" and source_field.usage not in {"filter", "write"}:
        raise ApiDesignError(
            f"请求映射中的数据库字段 {source_field.table}.{source_field.column} 只能使用 filter 或 write。"
        )
    if endpoint.side == "response" and source_field.usage != "read":
        raise ApiDesignError(
            f"响应映射中的数据库字段 {source_field.table}.{source_field.column} 必须使用 read。"
        )


def _validated_source_snapshots(
    workspace_root: str | Path,
    mappings: list[Any],
) -> list[dict[str, Any]]:
    """重新读取字段映射内嵌的来源字段并形成无凭据确认快照。"""

    source_fields = [
        source_field
        for mapping in mappings
        if (source_field := _mapping_source_field(mapping)) is not None
    ]
    database_refs: defaultdict[tuple[str, str], list[DatabaseSourceField]] = defaultdict(list)
    external_refs: defaultdict[tuple[str, str, str], list[ExternalSourceField]] = defaultdict(list)
    for source_field in source_fields:
        if isinstance(source_field, DatabaseSourceField):
            database_refs[(source_field.source_id, source_field.table)].append(source_field)
        else:
            external_refs[
                (source_field.source_id, source_field.directory_id, source_field.operation_id)
            ].append(source_field)
    snapshots: list[dict[str, Any]] = []
    for (source_id, table), refs in sorted(database_refs.items()):
        metadata = load_database_columns(workspace_root, source_id, table)
        actual_schema = str(metadata.get("schema") or metadata.get("database") or "")
        columns = {str(item.get("name") or ""): item for item in _dict_items(metadata.get("columns"))}
        for ref in refs:
            if actual_schema and ref.schema_name != actual_schema:
                raise ApiDesignError(f"数据库字段 Schema 与实时元数据不一致：{ref.schema_name}.{ref.table}。")
            actual = columns.get(ref.column)
            if actual is None:
                raise ApiDesignError(f"数据库表 {table} 不包含确认字段：{ref.column}。")
            if not _types_compatible(ref.type, str(actual.get("type") or "unknown")):
                raise ApiDesignError(f"数据库字段类型与草稿不一致：{table}.{ref.column}。")
        snapshots.append(
            {
                "sourceType": "database",
                "sourceId": source_id,
                "name": source_id,
                "details": _project_database_snapshot(metadata, refs),
            }
        )
    for (source_id, directory_id, operation_id), refs in sorted(external_refs.items()):
        operation = load_external_operation(workspace_root, source_id, directory_id, operation_id)
        available = {
            (str(item.get("section") or ""), str(item.get("path") or "")): item
            for item in _dict_items(operation.get("fields"))
        }
        for ref in refs:
            actual = available.get((ref.section, ref.path))
            if actual is None:
                raise ApiDesignError(f"外部 Operation 不包含确认字段：{ref.section}.{ref.path}。")
            if not _types_compatible(ref.type, str(actual.get("type") or "unknown")):
                raise ApiDesignError(f"外部 Operation 字段类型与草稿不一致：{ref.section}.{ref.path}。")
        connection = operation.get("connection") if isinstance(operation.get("connection"), dict) else {}
        safe_connection = {
            "baseUrl": connection.get("baseUrl"),
            "baseUrlConfigKey": connection.get("baseUrlConfigKey"),
            "timeoutMs": connection.get("timeoutMs"),
            "headers": [
                {"name": item.get("name")}
                for item in _dict_items(connection.get("headers"))
                if str(item.get("name") or "").strip()
            ],
        }
        snapshots.append(
            {
                "sourceType": "external_api",
                "sourceId": source_id,
                "name": str(operation.get("sourceName") or source_id),
                "details": _project_external_snapshot(operation, refs, safe_connection),
            }
        )
    return snapshots


def _project_database_snapshot(
    metadata: dict[str, Any],
    refs: list[DatabaseSourceField],
) -> dict[str, Any]:
    """仅保留实际参与映射的数据库列，同时保留表和 Schema 身份元数据。"""

    selected_columns = {ref.column for ref in refs}
    projected = dict(metadata)
    if isinstance(metadata.get("columns"), list):
        projected["columns"] = [
            item
            for item in _dict_items(metadata.get("columns"))
            if str(item.get("name") or item.get("column_name") or "") in selected_columns
        ]
    schemas = metadata.get("schemas")
    if isinstance(schemas, dict):
        projected["schemas"] = {
            table: [
                item
                for item in _dict_items(columns)
                if str(item.get("name") or item.get("column_name") or "") in selected_columns
            ]
            for table, columns in schemas.items()
            if isinstance(columns, list)
            and table in {ref.table for ref in refs}
        }
    tables = metadata.get("tables")
    if isinstance(tables, list):
        projected["tables"] = [
            item
            for item in _dict_items(tables)
            if str(item.get("table_name") or item.get("name") or "") in {ref.table for ref in refs}
        ]
    return projected


def _project_external_snapshot(
    operation: dict[str, Any],
    refs: list[ExternalSourceField],
    safe_connection: dict[str, Any],
) -> dict[str, Any]:
    """保留外部 Operation 身份和实际映射字段，裁剪未使用的参数与 Schema。"""

    selected = {(ref.section, ref.path) for ref in refs}
    projected = {
        "connection": safe_connection,
        **{
            key: value
            for key, value in operation.items()
            if key not in {"fields", "connection", "operation"}
        },
        "fields": [
            field
            for field in _dict_items(operation.get("fields"))
            if (str(field.get("section") or ""), str(field.get("path") or "")) in selected
        ],
    }
    raw_operation = operation.get("operation")
    if not isinstance(raw_operation, dict):
        return projected
    nested = dict(raw_operation)
    nested["pathParameters"] = [
        item
        for item in _dict_items(raw_operation.get("pathParameters"))
        if ("path", str(item.get("name") or "")) in selected
    ]
    nested["queryParameters"] = [
        item
        for item in _dict_items(raw_operation.get("queryParameters"))
        if ("query", str(item.get("name") or "")) in selected
    ]
    nested["headers"] = [
        {"name": item.get("name")}
        for item in _dict_items(raw_operation.get("headers"))
        if ("header", str(item.get("name") or "")) in selected
    ]
    nested["requestStructure"] = _project_json_structure(
        raw_operation.get("requestStructure"), "request_body", selected
    )
    nested["responseStructure"] = _project_json_structure(
        raw_operation.get("responseStructure"), "response_body", selected
    )
    projected["operation"] = nested
    return projected


def _project_json_structure(
    schema: Any,
    section: str,
    selected: set[tuple[str, str]],
    prefix: str = "",
) -> Any:
    """递归裁剪 JSON Schema，仅保留被映射的叶子路径。"""

    if not isinstance(schema, dict):
        return schema
    schema_types = schema.get("type") if isinstance(schema.get("type"), list) else [schema.get("type")]
    if "array" in schema_types:
        return {
            **schema,
            "items": _project_json_structure(schema.get("items"), section, selected, f"{prefix}[]"),
        }
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    projected_properties: dict[str, Any] = {}
    for name, child in properties.items():
        child_path = f"{prefix}.{name}" if prefix else str(name)
        if (section, child_path) not in selected and not any(
            selected_path.startswith(f"{child_path}.")
            or selected_path.startswith(f"{child_path}[]")
            for selected_section, selected_path in selected
            if selected_section == section
        ):
            continue
        projected_properties[name] = _project_json_structure(child, section, selected, child_path)
    projected = {**schema, "properties": projected_properties}
    if isinstance(schema.get("required"), list):
        projected["required"] = [name for name in schema["required"] if name in projected_properties]
    return projected


def _safe_external_operation(operation: dict[str, Any]) -> dict[str, Any]:
    """仅保留外部 Operation 的结构元数据，移除 Header 值与样例数据。"""

    safe = {
        key: value
        for key, value in operation.items()
        if key not in {"requestSample", "responseSample", "headers"}
    }
    safe["headers"] = [
        {"name": item.get("name")}
        for item in _dict_items(operation.get("headers"))
        if str(item.get("name") or "").strip()
    ]
    return safe


def _mysql_metadata(workspace_root: str | Path, source_id: str, *, table: str | None = None) -> dict[str, Any]:
    """解析内部凭据并调用既有只读 MySQL 元数据工具。"""

    try:
        config = resolve_direct_database_config(workspace_root, source_id)
        raw = mysql_table_info(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            table_name=table,
        )
        payload = json.loads(raw)
    except (DataSourceError, json.JSONDecodeError) as exc:
        raise ApiDesignError(str(exc)) from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ApiDesignError(str(payload.get("error") or "数据库元数据读取失败。"))
    if payload.get("database_exists") is False:
        raise ApiDesignError("直属 MySQL 的目标 Schema 不存在。")
    return payload


def _external_operation_fields(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """枚举外部 Operation 的 Path、Query、Header 与请求响应叶子字段。"""

    result: list[dict[str, Any]] = []
    for source_key, section in (("pathParameters", "path"), ("queryParameters", "query")):
        for parameter in _dict_items(operation.get(source_key)):
            result.append(
                {
                    "section": section,
                    "path": str(parameter.get("name") or ""),
                    "type": str(parameter.get("type") or "unknown"),
                    "required": bool(parameter.get("required")),
                    "description": str(parameter.get("description") or ""),
                }
            )
    for header in _dict_items(operation.get("headers")):
        result.append(
            {
                "section": "header",
                "path": str(header.get("name") or ""),
                "type": "string",
                "required": False,
                "description": "外部 Operation 非敏感请求 Header",
            }
        )
    for source_key, section in (("requestStructure", "request_body"), ("responseStructure", "response_body")):
        result.extend(_json_structure_leaf_fields(operation.get(source_key), section=section))
    return [item for item in result if item["path"]]


def _schema_leaf_fields(
    schema: Any,
    schemas: dict[str, Any],
    *,
    side: str,
    location: str,
    prefix: str = "",
    required: bool = True,
    visited_refs: frozenset[str] = frozenset(),
) -> list[EndpointFieldNode]:
    """递归展开本地 Schema，只返回叶子字段并正确传播 required。"""

    if not isinstance(schema, dict):
        return []
    schema = resolve_mapping_schema(schema, schemas, visited_refs)
    if not schema:
        return []
    types = schema.get("type") if isinstance(schema.get("type"), list) else [schema.get("type")]
    if "array" in types:
        return _schema_leaf_fields(
            schema.get("items"),
            schemas,
            side=side,
            location=location,
            prefix=f"{prefix}[]",
            required=required,
            visited_refs=visited_refs,
        )
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if properties:
        required_names = {str(item) for item in schema.get("required") or [] if str(item).strip()}
        result: list[EndpointFieldNode] = []
        for name, child in properties.items():
            child_path = f"{prefix}.{name}" if prefix else str(name)
            result.extend(
                _schema_leaf_fields(
                    child,
                    schemas,
                    side=side,
                    location=location,
                    prefix=child_path,
                    required=required and str(name) in required_names,
                    visited_refs=visited_refs,
                )
            )
        return result
    if not prefix:
        return []
    return [
        EndpointFieldNode(
            id=_endpoint_node_id(side, location, prefix),
            side=side,
            location=location,
            path=prefix,
            type=_schema_type(schema),
            required=required,
            description=str(schema.get("description") or ""),
        )
    ]


def _json_structure_leaf_fields(schema: Any, *, section: str, prefix: str = "") -> list[dict[str, Any]]:
    """递归展开外部 API 标准化 JSON 结构，只返回叶子字段。"""

    if not isinstance(schema, dict):
        return []
    types = schema.get("type") if isinstance(schema.get("type"), list) else [schema.get("type")]
    if "array" in types:
        return _json_structure_leaf_fields(schema.get("items"), section=section, prefix=f"{prefix}[]")
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if properties:
        result: list[dict[str, Any]] = []
        for name, child in properties.items():
            child_path = f"{prefix}.{name}" if prefix else str(name)
            result.extend(_json_structure_leaf_fields(child, section=section, prefix=child_path))
        return result
    if not prefix:
        return []
    return [
        {
            "section": section,
            "path": prefix,
            "type": _schema_type(schema),
            "required": False,
            "description": str(schema.get("description") or ""),
        }
    ]


def _target_endpoints(
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    api_contract_id: str | None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """解析页面或单 Endpoint 对应的唯一 TechnicalPlan 接口集合。"""

    if target_type == "endpoint":
        return [find_endpoint(project_plan, str(api_contract_id or ""), target_id)]
    if target_type != "page":
        raise ApiDesignError(f"不支持的开发目标类型：{target_type}。")
    page = next(
        (
            item
            for item in project_plan_page_records(project_plan)
            if str(item.get("pageId") or item.get("id") or "") == target_id
        ),
        None,
    )
    if page is None:
        raise ApiDesignError(f"TechnicalPlan 不包含页面：{target_id}。")
    implementation = next(
        (
            item
            for item in _dict_items(project_plan.get("page_implementation_contracts"))
            if str(item.get("pageId") or "") == target_id
        ),
        None,
    )
    if implementation is not None:
        endpoint_ids = {str(item).strip() for item in implementation.get("requiredEndpointIds") or [] if str(item).strip()}
    else:
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        endpoint_ids = {
            str(item.get("endpoint_id") or "")
            for item in _dict_items(references.get("endpoint_dependencies"))
            if str(item.get("endpoint_id") or "").strip()
        }
    matches = [
        (contract, endpoint)
        for contract in _dict_items(project_plan.get("api_contracts"))
        for endpoint in _dict_items(contract.get("endpoints"))
        if str(endpoint.get("id") or "") in endpoint_ids
    ]
    found = {str(endpoint.get("id") or "") for _, endpoint in matches}
    missing = sorted(endpoint_ids - found)
    if missing:
        raise ApiDesignError(f"页面引用了不存在的 Endpoint：{', '.join(missing)}。")
    return matches


def _endpoint_node_id(side: str, location: str, path: str) -> str:
    """为契约字段生成稳定、不可篡改的节点 ID。"""

    return f"endpoint:{side}:{location}:{path}"


def _endpoint_field_key(value: dict[str, Any]) -> tuple[str, str, str]:
    """生成 Endpoint 字段的语义唯一键。"""

    return (str(value.get("side") or ""), str(value.get("location") or ""), str(value.get("path") or ""))


def _unique_endpoint_fields(fields: list[EndpointFieldNode]) -> list[EndpointFieldNode]:
    """按字段身份保留首次出现项，消除 Schema 展开重复叶子。"""

    result: list[EndpointFieldNode] = []
    seen: set[tuple[str, str, str]] = set()
    for field in fields:
        key = (field.side, field.location, field.path)
        if key not in seen:
            seen.add(key)
            result.append(field)
    return result


def _schema_type(schema: Any) -> str:
    """读取 JSON Schema 类型，并把联合类型转换为稳定文本。"""

    if not isinstance(schema, dict):
        return "unknown"
    value = schema.get("type")
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    return str(value or "unknown")


def _endpoint_field_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """移除工作台节点身份，仅保留正式映射需要的 Endpoint 字段事实。"""

    return {
        "side": str(value.get("side") or ""),
        "location": str(value.get("location") or ""),
        "path": str(value.get("path") or ""),
        "type": str(value.get("type") or "unknown"),
        "required": bool(value.get("required")),
        "description": str(value.get("description") or ""),
    }


def _endpoint_field_label(value: Any) -> str:
    """把内嵌 Endpoint 字段转换为可读标签。"""

    if hasattr(value, "side"):
        return f"{value.side}.{value.location}.{value.path}"
    if isinstance(value, dict):
        return f"{value.get('side')}.{value.get('location')}.{value.get('path')}"
    return ""


def _entity_field_label(value: Any) -> str:
    """把内嵌实体字段引用转换为可读标签。"""

    if hasattr(value, "entity_id"):
        return f"{value.entity_id}.{value.path}"
    if isinstance(value, dict):
        return f"{value.get('entityId')}.{value.get('path')}"
    return ""


def _source_field_label(value: Any) -> str:
    """把内嵌数据源字段转换为可读标签。"""

    if isinstance(value, DatabaseSourceField):
        return f"{value.source_id}.{value.table}.{value.column}"
    if isinstance(value, ExternalSourceField):
        return f"{value.source_id}.{value.operation_id}.{value.section}.{value.path}"
    if isinstance(value, dict) and value.get("sourceType") == "database":
        return f"{value.get('sourceId')}.{value.get('table')}.{value.get('column')}"
    if isinstance(value, dict) and value.get("sourceType") == "external_api":
        return f"{value.get('sourceId')}.{value.get('operationId')}.{value.get('section')}.{value.get('path')}"
    return ""


def _types_compatible(left: str, right: str) -> bool:
    """按 API、实体、SQL 和外部 Schema 的常用类型族判断兼容性。"""

    left_family = _type_family(left)
    right_family = _type_family(right)
    return "unknown" in {left_family, right_family} or left_family == right_family


def _type_family(value: str) -> str:
    """把 TypeScript、JSON Schema 和 MySQL 类型归并到基础类型族。"""

    normalized = str(value or "").strip().lower().split("(", 1)[0]
    if any(token in normalized for token in ("int", "decimal", "numeric", "float", "double", "number")):
        return "number"
    if any(token in normalized for token in ("bool", "bit")):
        return "boolean"
    if any(token in normalized for token in ("array", "list", "[]")):
        return "array"
    if any(token in normalized for token in ("object", "map", "record", "json")):
        return "object"
    if any(token in normalized for token in ("char", "text", "string", "date", "time", "uuid", "enum")):
        return "string"
    return normalized or "unknown"


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤列表中的非对象输入。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

