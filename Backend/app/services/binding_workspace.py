"""保存绑定工作台的中间状态，不改变数据源和 Endpoint 正式产物。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.api_design import DraftFieldMapping, EndpointField, ExternalApiFixedValueDraft
from app.services.data_sources import change_selected_tables, selected_tables


BINDING_STATE_LOCK = RLock()


class BindingTarget(BaseModel):
    """校验单一表或接口的工作台选择，不写入正式产物。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source_type: Literal["database", "external_api"] = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    schema_name: str | None = Field(default=None, alias="schema", max_length=256)
    table: str | None = Field(default=None, max_length=256)
    directory_id: str | None = Field(default=None, alias="directoryId", max_length=128)
    operation_id: str | None = Field(default=None, alias="operationId", max_length=128)


class BindingDraftRequest(BaseModel):
    """校验可含未配置字段的草稿和来源选择。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    draft: dict[str, Any]
    selection: BindingTarget | None = None
    base_revision: str | None = Field(default=None, alias="baseRevision", pattern=r"^[0-9a-f]{32}$")
    technical_plan_hash: str = Field(alias="technicalPlanHash", pattern=r"^[0-9a-f]{64}$")


def _draft_path(workspace: str | Path, name: str) -> Path:
    """返回 drafts 下的绑定草稿路径；仅校验，不创建目录。"""
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("当前工作区不存在或不是目录。")
    artifact_directory = root / WORKSPACE_ARTIFACT_DIR
    if artifact_directory.is_symlink():
        raise ValueError("工作区 .devagentstudio 不允许使用符号链接。")
    if artifact_directory.exists() and not artifact_directory.is_dir():
        raise ValueError("工作区 .devagentstudio 不可用。")
    drafts_directory = artifact_directory / "drafts"
    if drafts_directory.is_symlink():
        raise ValueError("工作区 drafts 目录不允许使用符号链接。")
    if drafts_directory.exists() and not drafts_directory.is_dir():
        raise ValueError("工作区 drafts 路径不可用。")
    directory = drafts_directory / "endpoint-bindings"
    if directory.is_symlink():
        raise ValueError("Endpoint 绑定草稿目录不允许使用符号链接。")
    if directory.exists() and not directory.is_dir():
        raise ValueError("Endpoint 绑定草稿路径不可用。")
    path = directory / name
    if path.is_symlink():
        raise ValueError("绑定工作台文件不允许使用符号链接。")
    return path


def _read(path: Path) -> dict[str, Any]:
    """读取中间状态，损坏时显式失败而不是覆盖。"""
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("绑定工作台状态格式无效。")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    """保存时创建草稿分类目录，并以同目录临时文件原子替换草稿。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directories = (path.parent.parent.parent, path.parent.parent, path.parent)
    if any(directory.is_symlink() for directory in directories) or path.is_symlink():
        raise ValueError("绑定草稿目录和文件不允许使用符号链接。")
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _draft_name(contract: str, endpoint: str) -> str:
    """使用复合身份哈希避免文件名碰撞和路径穿越。"""
    key = hashlib.sha256(json.dumps([contract, endpoint]).encode()).hexdigest()
    return f"draft-{key}.json"


def read_binding_draft(workspace: str | Path, contract: str, endpoint: str) -> dict[str, Any] | None:
    """读取指定接口草稿，保持与正式状态分离。"""
    with BINDING_STATE_LOCK:
        value = _read(_draft_path(workspace, _draft_name(contract, endpoint)))
        return value if value.get("draftFormat") == "endpoint-field-mapping.v6" else None


def _draft_database_query(value: Any) -> dict[str, Any] | None:
    """允许尚未填完的条件叶子暂存，同时限制分组深度和数量。"""

    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"join", "items"} or value.get("join") not in {"and", "or"} or not isinstance(value.get("items"), list):
        raise ValueError("数据库查询草稿结构无效。")
    if len(value["items"]) > 300:
        raise ValueError("查询条件数量超出限制。")
    count = 0
    for item in value["items"]:
        if not isinstance(item, dict) or item.get("kind") not in {"condition", "group"}:
            raise ValueError("数据库查询草稿包含无效项目。")
        if item["kind"] == "condition":
            count += 1
        else:
            if set(item) != {"kind", "join", "items"} or item.get("join") not in {"and", "or"} or not isinstance(item.get("items"), list):
                raise ValueError("数据库查询草稿分组无效。")
            if any(not isinstance(child, dict) or child.get("kind") != "condition" for child in item["items"]):
                raise ValueError("查询条件最多允许一层子组。")
            count += len(item["items"])
    if count > 300:
        raise ValueError("查询条件最多 300 条。")
    return value


def _draft_database_writes(value: Any) -> list[dict[str, Any]]:
    """保存可暂缺目标列或右值的数据库写入行，并限制来源和字段边界。"""

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 3000:
        raise ValueError("数据库写入字段必须是最多 3000 项的列表。")
    writes: list[dict[str, Any]] = []
    for item in value:
        allowed = {"sourceType", "sourceId", "schema", "table", "column", "type", "description", "right"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("数据库写入字段草稿结构无效。")
        if item.get("sourceType") != "database":
            raise ValueError("数据库写入字段只能来自数据库表。")
        source_id = str(item.get("sourceId") or "")
        schema = str(item.get("schema") or "")
        table = str(item.get("table") or "")
        column = str(item.get("column") or "")
        column_type = str(item.get("type") or "unknown")
        if not source_id or not schema or not table or len(source_id) > 128 or len(schema) > 256 or len(table) > 256 or len(column) > 256:
            raise ValueError("数据库写入字段缺少有效的数据表身份。")
        row = {
            "sourceType": "database", "sourceId": source_id, "schema": schema, "table": table,
            "column": column, "type": column_type[:128], "description": str(item.get("description") or "")[:2048],
        }
        right = item.get("right")
        if right is not None:
            if not isinstance(right, dict) or right.get("kind") not in {"endpoint", "fixed"}:
                raise ValueError("数据库写入值来源无效。")
            if right["kind"] == "endpoint":
                if set(right) - {"kind", "endpointField"}:
                    raise ValueError("数据库写入接口参数结构无效。")
                endpoint = right.get("endpointField")
                if endpoint is None:
                    row["right"] = {"kind": "endpoint"}
                else:
                    parsed = EndpointField.model_validate(endpoint)
                    if parsed.side != "request":
                        raise ValueError("数据库写入值只能引用接口请求参数。")
                    row["right"] = {"kind": "endpoint", "endpointField": parsed.model_dump(by_alias=True)}
            else:
                if set(right) - {"kind", "value"}:
                    raise ValueError("数据库写入固定值结构无效。")
                row["right"] = {"kind": "fixed", **({"value": right["value"]} if "value" in right else {})}
        writes.append(row)
    return writes


def _draft_external_api_fixed_values(value: Any) -> list[dict[str, Any]]:
    """保留外部 API 固定值的未完成编辑态，并校验请求字段结构。"""

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 3000:
        raise ValueError("外部 API 固定值必须是最多 3000 项的列表。")
    try:
        entries = TypeAdapter(list[ExternalApiFixedValueDraft]).validate_python(value)
    except ValueError as exc:
        raise ValueError(f"外部 API 固定值草稿结构无效：{exc}") from exc
    return [item.model_dump(by_alias=True, exclude_none=True) for item in entries]


def save_binding_draft(request: BindingDraftRequest) -> dict[str, Any]:
    """验证草稿身份与基础版本，保存不完整映射但不确认。"""
    from app.workspace.endpoint_design_documents import read_endpoint_design, technical_plan_sha256
    from app.services.endpoint_design_detail import _read_current_technical_plan
    from app.services.api_design import find_endpoint

    with BINDING_STATE_LOCK:
        find_endpoint(_read_current_technical_plan(Path(request.workspace_root)), request.api_contract_id, request.endpoint_id)
        current = read_endpoint_design(request.workspace_root, request.api_contract_id, request.endpoint_id, require_current=False)
        revision = (current or {}).get("artifactRevision")
        if revision != request.base_revision or technical_plan_sha256(request.workspace_root) != request.technical_plan_hash:
            raise ValueError("契约或正式映射已变化，请重新加载；当前输入仍保留。")
        draft = request.draft
        if set(draft) - {"apiContractId", "endpointId", "implementationDescription", "databaseOperation", "databaseWrites", "externalApiFixedValues", "databaseQuery", "fieldMappings"}:
            raise ValueError("草稿包含未支持字段。")
        if draft.get("apiContractId") != request.api_contract_id or draft.get("endpointId") != request.endpoint_id:
            raise ValueError("草稿接口身份与请求不一致。")
        mappings = TypeAdapter(list[DraftFieldMapping]).validate_python(draft.get("fieldMappings", []))
        writes = _draft_database_writes(draft.get("databaseWrites", []))
        external_fixed_values = _draft_external_api_fixed_values(draft.get("externalApiFixedValues", []))
        query = _draft_database_query(draft.get("databaseQuery"))
        if len(mappings) > 3000:
            raise ValueError("草稿字段数量超出限制。")
        clean = {
            "apiContractId": request.api_contract_id,
            "endpointId": request.endpoint_id,
            "implementationDescription": str(draft.get("implementationDescription") or "")[:4000],
            "databaseOperation": draft.get("databaseOperation"),
            "databaseWrites": writes,
            "externalApiFixedValues": external_fixed_values,
            "databaseQuery": query,
            "fieldMappings": [item.model_dump(by_alias=True, exclude_none=True) for item in mappings],
        }
        value = {"draftFormat": "endpoint-field-mapping.v6", "draft": clean, "selection": request.selection.model_dump(by_alias=True, exclude_none=True) if request.selection else None,
                 "baseRevision": request.base_revision, "technicalPlanHash": request.technical_plan_hash,
                 "savedAt": datetime.now(UTC).isoformat()}
        _write(_draft_path(request.workspace_root, _draft_name(request.api_contract_id, request.endpoint_id)), value)
        return value


def validate_binding_selection(workspace: str | Path, selection: BindingTarget, draft: dict[str, Any]) -> None:
    """简化旅程确认时验证所有字段只来自选定的已添加表或接口。"""
    from app.services.api_design import load_database_columns, load_external_operation

    # 正式产物通过字段映射保存来源；空映射无法表达绑定，不能返回虚假的确认成功。
    if not draft.get("fieldMappings") and not draft.get("databaseQuery") and not draft.get("databaseWrites") and not draft.get("externalApiFixedValues"):
        raise ValueError("当前接口没有可映射字段，无法确认数据来源绑定；草稿已保留。")
    expected = selection.model_dump(by_alias=True, exclude_none=True)
    if selection.source_type != "database" and draft.get("databaseWrites"):
        raise ValueError("数据库写入字段只能绑定数据库数据表。")
    if selection.source_type != "external_api" and draft.get("externalApiFixedValues"):
        raise ValueError("外部 API 固定值只能绑定外部接口。")
    if selection.source_type == "database":
        if not selection.table or not selection.schema_name or not any(
            item["sourceId"] == selection.source_id and item["schema"] == selection.schema_name and item["table"] == selection.table
            for item in selected_tables(workspace)
        ):
            raise ValueError("所选表不在应用已添加清单中。")
        keys = ("sourceType", "sourceId", "schema", "table")
        metadata = load_database_columns(workspace, selection.source_id, selection.table)
        if metadata.get("schema") != selection.schema_name or not metadata.get("columns"):
            raise ValueError("所选表结构已不可用，请重新读取。")
        available_columns = {str(item.get("name") or ""): item for item in metadata.get("columns", []) if isinstance(item, dict)}
        for write in draft.get("databaseWrites", []):
            if any(write.get(key) != expected.get(key) for key in keys):
                raise ValueError("所有数据库写入字段必须来自当前选定数据表。")
            column = str(write.get("column") or "")
            if column and column not in available_columns:
                raise ValueError(f"数据库写入字段不在当前数据表中：{column}。")
    else:
        if not selection.directory_id or not selection.operation_id:
            raise ValueError("请选择完整的外部接口。")
        keys = ("sourceType", "sourceId", "directoryId", "operationId")
        operation_metadata = load_external_operation(workspace, selection.source_id, selection.directory_id, selection.operation_id)
        available_fields = {
            (str(field.get("section") or ""), str(field.get("path") or "")): field
            for field in operation_metadata.get("fields", []) if isinstance(field, dict)
        }
        for item in draft.get("externalApiFixedValues", []):
            field = item.get("externalField") if isinstance(item, dict) else None
            if not isinstance(field, dict) or any(field.get(key) != expected.get(key) for key in keys):
                raise ValueError("外部 API 固定值必须来自当前选定接口。")
            field_key = (str(field.get("section") or ""), str(field.get("path") or ""))
            actual = available_fields.get(field_key)
            if actual is None or actual.get("section") == "response_body":
                raise ValueError("外部 API 固定值目标已失效或不是请求参数。")
            if str(field.get("type") or "unknown") != str(actual.get("type") or "unknown"):
                raise ValueError(f"外部 API 固定值字段类型已变化：{field_key[1]}。")
    used_source_fields: set[tuple[str, str]] = set()
    from app.services.api_design import database_query_leaves
    for condition in database_query_leaves(draft.get("databaseQuery")):
        if selection.source_type != "database" or any(condition.get(key) != expected.get(key) for key in keys):
            raise ValueError("数据库查询条件必须来自当前选定数据表。")
        if not condition.get("column"):
            raise ValueError("数据库查询条件缺少数据库列。")
    for mapping in draft.get("fieldMappings", []):
        if mapping.get("mappingType") == "unconfigured" and mapping.get("endpointField", {}).get("side") == "request":
            continue
        sources = mapping.get("sourceFields", [])
        if mapping.get("mappingType") != "source_mapping" or mapping.get("processingType") != "direct" or len(sources) != 1:
            raise ValueError("当前绑定旅程只支持直接映射。")
        if any(sources[0].get(key) != expected.get(key) for key in keys):
            raise ValueError("所有字段必须来自当前选定对象。")
        # 外部响应字段可供多个应用出参复用，只有入参继续保持一对一绑定。
        if selection.source_type == "external_api" and sources[0].get("section") != "response_body":
            source_key = (str(sources[0].get("section") or ""), str(sources[0].get("path") or ""))
            if source_key in used_source_fields:
                raise ValueError("同一个外部字段不能绑定多个应用字段。")
            used_source_fields.add(source_key)
    for item in draft.get("externalApiFixedValues", []):
        field = item.get("externalField") if isinstance(item, dict) else None
        if not isinstance(field, dict):
            continue
        source_key = (str(field.get("section") or ""), str(field.get("path") or ""))
        if source_key in used_source_fields:
            raise ValueError("外部请求参数不能同时绑定接口参数和固定值。")
        used_source_fields.add(source_key)
    if selection.source_type == "external_api":
        mapped = {(str(source.get("section") or ""), str(source.get("path") or ""))
                  for mapping in draft.get("fieldMappings", [])
                  for source in mapping.get("sourceFields", [])}
        mapped.update(
            (str((item.get("externalField") or {}).get("section") or ""), str((item.get("externalField") or {}).get("path") or ""))
            for item in draft.get("externalApiFixedValues", [])
            if isinstance(item, dict) and isinstance(item.get("externalField"), dict)
            and item.get("value") is not None and (not isinstance(item.get("value"), str) or item.get("value").strip())
        )
        required = {(str(field.get("section") or ""), str(field.get("path") or ""))
                    for field in operation_metadata.get("fields", [])
                    if field.get("required") and field.get("section") != "response_body"}
        missing = sorted(required - mapped)
        if missing:
            raise ValueError(f"外部接口必填字段尚未映射：{', '.join(f'{section}.{path}' for section, path in missing)}。")


def clear_binding_draft(workspace: str | Path, contract: str, endpoint: str) -> None:
    """正式确认或主动放弃后删除对应草稿，不影响其他接口。"""
    with BINDING_STATE_LOCK:
        path = _draft_path(workspace, _draft_name(contract, endpoint))
        path.unlink(missing_ok=True)
        directory = path.parent
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def source_references(workspace: str | Path, source_id: str, table: str | None = None,
                      directory_id: str | None = None, operation_id: str | None = None) -> list[str]:
    """读取正式映射引用，仅供删除前提示，不修改产物。"""
    result = []
    for path in (Path(workspace) / WORKSPACE_ARTIFACT_DIR / "plans" / "endpoints").glob("*.json"):
        design = _read(path)
        if design.get("schemaVersion") != "endpoint-field-mapping.v6":
            continue
        fields = [source for mapping in design.get("fieldMappings", []) for source in mapping.get("sourceFields", [])]
        fields.extend(item.get("externalField", {}) for item in design.get("externalApiFixedValues", []) if isinstance(item, dict))
        fields.extend(design.get("databaseWrites", []))
        from app.services.api_design import database_query_leaves
        fields.extend(database_query_leaves(design.get("databaseQuery")))
        if any(source.get("sourceId") == source_id
               and (not table or source.get("table") == table)
               and (not directory_id or source.get("directoryId") == directory_id)
               and (not operation_id or source.get("operationId") == operation_id) for source in fields):
            result.append(f'{design.get("apiContractId")}/{design.get("endpointId")}')
    return result
