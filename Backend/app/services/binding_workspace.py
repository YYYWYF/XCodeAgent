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

from app.domain.api_design import DraftFieldMapping
from app.persistence.data_sources import data_sources_directory
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


def _state_path(workspace: str | Path, name: str) -> Path:
    """限定中间文件位置，并拒绝符号链接。"""
    # 数据源持久化会清理其目录中的非目录文件，中间状态必须放在同级独立目录。
    directory = data_sources_directory(workspace).parent / "binding-workspace"
    if directory.is_symlink():
        raise ValueError("绑定工作台目录不允许使用符号链接。")
    directory.mkdir(exist_ok=True)
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
    """用同目录临时文件原子替换中间状态。"""
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
        return _read(_state_path(workspace, _draft_name(contract, endpoint))) or None


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
        if set(draft) - {"apiContractId", "endpointId", "implementationDescription", "fieldMappings"}:
            raise ValueError("草稿包含未支持字段。")
        if draft.get("apiContractId") != request.api_contract_id or draft.get("endpointId") != request.endpoint_id:
            raise ValueError("草稿接口身份与请求不一致。")
        mappings = TypeAdapter(list[DraftFieldMapping]).validate_python(draft.get("fieldMappings", []))
        if len(mappings) > 3000:
            raise ValueError("草稿字段数量超出限制。")
        clean = {
            "apiContractId": request.api_contract_id,
            "endpointId": request.endpoint_id,
            "implementationDescription": str(draft.get("implementationDescription") or "")[:4000],
            "fieldMappings": [item.model_dump(by_alias=True, exclude_none=True) for item in mappings],
        }
        value = {"draft": clean, "selection": request.selection.model_dump(by_alias=True, exclude_none=True) if request.selection else None,
                 "baseRevision": request.base_revision, "technicalPlanHash": request.technical_plan_hash,
                 "savedAt": datetime.now(UTC).isoformat()}
        _write(_state_path(request.workspace_root, _draft_name(request.api_contract_id, request.endpoint_id)), value)
        return value


def validate_binding_selection(workspace: str | Path, selection: BindingTarget, draft: dict[str, Any]) -> None:
    """简化旅程确认时验证所有字段只来自选定的已添加表或接口。"""
    from app.services.api_design import load_database_columns, load_external_operation

    # 正式产物通过字段映射保存来源；空映射无法表达绑定，不能返回虚假的确认成功。
    if not draft.get("fieldMappings"):
        raise ValueError("当前接口没有可映射字段，无法确认数据来源绑定；草稿已保留。")
    expected = selection.model_dump(by_alias=True, exclude_none=True)
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
    else:
        if not selection.directory_id or not selection.operation_id:
            raise ValueError("请选择完整的外部接口。")
        keys = ("sourceType", "sourceId", "directoryId", "operationId")
        operation_metadata = load_external_operation(workspace, selection.source_id, selection.directory_id, selection.operation_id)
    used_source_fields: set[tuple[str, str]] = set()
    for mapping in draft.get("fieldMappings", []):
        sources = mapping.get("sourceFields", [])
        if mapping.get("mappingType") != "source_mapping" or mapping.get("processingType") != "direct" or len(sources) != 1:
            raise ValueError("当前绑定旅程只支持直接映射。")
        if any(sources[0].get(key) != expected.get(key) for key in keys):
            raise ValueError("所有字段必须来自当前选定对象。")
        if selection.source_type == "external_api":
            source_key = (str(sources[0].get("section") or ""), str(sources[0].get("path") or ""))
            if source_key in used_source_fields:
                raise ValueError("同一个外部字段不能绑定多个应用字段。")
            used_source_fields.add(source_key)
    if selection.source_type == "external_api":
        mapped = {(str(source.get("section") or ""), str(source.get("path") or ""))
                  for mapping in draft.get("fieldMappings", [])
                  for source in mapping.get("sourceFields", [])}
        required = {(str(field.get("section") or ""), str(field.get("path") or ""))
                    for field in operation_metadata.get("fields", [])
                    if field.get("required") and field.get("section") != "response_body"}
        missing = sorted(required - mapped)
        if missing:
            raise ValueError(f"外部接口必填字段尚未映射：{', '.join(f'{section}.{path}' for section, path in missing)}。")


def clear_binding_draft(workspace: str | Path, contract: str, endpoint: str) -> None:
    """正式确认成功后清空对应草稿，不影响其他接口。"""
    with BINDING_STATE_LOCK:
        _write(_state_path(workspace, _draft_name(contract, endpoint)), {})


def source_references(workspace: str | Path, source_id: str, table: str | None = None,
                      directory_id: str | None = None, operation_id: str | None = None) -> list[str]:
    """读取正式映射引用，仅供删除前提示，不修改产物。"""
    result = []
    for path in (Path(workspace) / ".xcodeagent/plans/endpoints").glob("*.json"):
        design = _read(path)
        fields = [source for mapping in design.get("fieldMappings", []) for source in mapping.get("sourceFields", [])]
        if any(source.get("sourceId") == source_id
               and (not table or source.get("table") == table)
               and (not directory_id or source.get("directoryId") == directory_id)
               and (not operation_id or source.get("operationId") == operation_id) for source in fields):
            result.append(f'{design.get("apiContractId")}/{design.get("endpointId")}')
    return result
