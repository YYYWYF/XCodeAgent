"""Endpoint API 设计正式产物的当前版读写服务。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.api_design import API_DESIGN_SCHEMA_VERSION, EndpointApiDesign
from app.services.artifact_invalidation import canonical_sha256


def endpoint_design_stem(api_contract_id: str, endpoint_id: str) -> str:
    """把契约与接口标识转换为稳定且安全的文件名。"""

    normalized = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        f"{api_contract_id}--{endpoint_id}",
    ).strip("-_")
    return f"endpoint--{normalized or 'unknown'}"


def endpoint_design_paths(
    workspace_root: str | Path,
    api_contract_id: str,
    endpoint_id: str,
) -> tuple[Path, Path]:
    """返回当前 Endpoint 设计 JSON 与 Markdown 的规范路径。"""

    directory = Path(workspace_root).expanduser().resolve() / ".xcodeagent" / "plans" / "endpoints"
    stem = endpoint_design_stem(api_contract_id, endpoint_id)
    return directory / f"{stem}.json", directory / f"{stem}.md"


def technical_plan_path(workspace_root: str | Path) -> Path:
    """返回当前工作区 TechnicalPlan JSON 的规范路径。"""

    return Path(workspace_root).expanduser().resolve() / ".xcodeagent" / "plans" / "technical-plan.json"


def technical_plan_sha256(workspace_root: str | Path) -> str:
    """计算当前 TechnicalPlan 文件哈希，作为 API 设计直接上游指纹。"""

    path = technical_plan_path(workspace_root)
    if not path.is_file():
        raise ValueError("缺少已确认的 technical-plan.json。")
    return canonical_sha256(path)


def write_endpoint_design(
    workspace_root: str | Path,
    design: EndpointApiDesign,
) -> dict[str, str]:
    """原子写入当前版 API 设计 JSON 和 Markdown，并返回产物路径与哈希。"""

    json_path, markdown_path = endpoint_design_paths(
        workspace_root,
        design.api_contract_id,
        design.endpoint_id,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = design.model_dump(mode="json", by_alias=True, exclude_none=True)
    json_content = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    markdown_content = render_endpoint_design_markdown(payload)
    previous_json = json_path.read_bytes() if json_path.is_file() else None
    previous_markdown = markdown_path.read_bytes() if markdown_path.is_file() else None
    temporary_paths: list[Path] = []
    try:
        # 先分别准备两份临时文件，确保任一内容写入失败时正式文件仍保持旧版本。
        temporary_markdown = _prepare_text_file(markdown_path, markdown_content)
        temporary_paths.append(temporary_markdown)
        temporary_json = _prepare_text_file(json_path, json_content)
        temporary_paths.append(temporary_json)
        # 两份临时文件均准备完成后再协调替换；第二次替换失败由下方回滚旧文件。
        _publish_temporary_file(temporary_markdown, markdown_path)
        _publish_temporary_file(temporary_json, json_path)
    except Exception:
        # 任一文件替换失败都恢复上一版，避免破坏已有的正式设计。
        _restore_file(json_path, previous_json)
        _restore_file(markdown_path, previous_markdown)
        raise
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "sha256": hashlib.sha256(json_content.encode("utf-8")).hexdigest(),
    }


def read_endpoint_design(
    workspace_root: str | Path,
    api_contract_id: str,
    endpoint_id: str,
    *,
    require_current: bool = True,
) -> dict[str, Any] | None:
    """读取并严格校验当前版双文件 API 设计及只读模板副本结构。"""

    json_path, markdown_path = endpoint_design_paths(
        workspace_root,
        api_contract_id,
        endpoint_id,
    )
    if not json_path.is_file() or not markdown_path.is_file():
        return None
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")
        if not markdown.strip():
            return None
        design = EndpointApiDesign.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if (
        design.api_contract_id != api_contract_id
        or design.endpoint_id != endpoint_id
        or design.schema_version != API_DESIGN_SCHEMA_VERSION
    ):
        return None
    if _markdown_artifact_revision(markdown) != design.artifact_revision:
        return None
    if require_current:
        expected = next(
            (
                item.sha256
                for item in design.based_on
                if item.artifact_key == "technical-plan"
            ),
            "",
        )
        try:
            if not expected or expected != technical_plan_sha256(workspace_root):
                return None
        except ValueError:
            return None
    return design.model_dump(mode="json", by_alias=True, exclude_none=True)


def endpoint_design_status(
    workspace_root: str | Path,
    api_contract_id: str,
    endpoint_id: str,
) -> dict[str, Any]:
    """返回工作台使用的待设计、已设计或需重新设计状态。"""

    json_path, markdown_path = endpoint_design_paths(
        workspace_root,
        api_contract_id,
        endpoint_id,
    )
    if not json_path.is_file() and not markdown_path.is_file():
        return {"status": "pending", "designed": False, "reason": "缺少当前版 API 设计产物。"}
    if not json_path.is_file() or not markdown_path.is_file():
        return {"status": "stale", "designed": False, "reason": "Endpoint API 设计双文件不完整。"}
    current = read_endpoint_design(
        workspace_root,
        api_contract_id,
        endpoint_id,
        require_current=True,
    )
    if current is not None:
        return {"status": "confirmed", "designed": True, "reason": ""}
    return {
        "status": "stale",
        "designed": False,
        "reason": "API 设计格式无效、双文件不完整、场景实体模板失效或 TechnicalPlan 已变化。",
    }


def render_endpoint_design_markdown(design: dict[str, Any]) -> str:
    """把自包含字段映射渲染为用户可读 Markdown 正式产物。"""

    endpoint = design.get("endpointContract") if isinstance(design.get("endpointContract"), dict) else {}
    lines = [
        f"# Endpoint 字段映射：{endpoint.get('method') or 'API'} {endpoint.get('path') or design.get('endpointId')}",
        "",
        f"- API Contract：`{design.get('apiContractId') or ''}`",
        f"- Endpoint：`{design.get('endpointId') or ''}`",
        f"- 状态：已确认",
        f"<!-- xcodeagent-artifact-revision: {design.get('artifactRevision') or ''} -->",
        "",
        "## API 实现描述",
        "",
    ]
    lines.extend(_implementation_description_lines(design) or ["- 未补充 API 实现描述。"])
    lines.extend([
        "",
        "## 场景实体",
        "",
    ])
    for entity in _dict_items(design.get("sceneEntities")):
        entity_name = str(entity.get("name") or entity.get("id") or "")
        template = f"（模板：{entity.get('templateEntityId')}）" if entity.get("templateEntityId") else ""
        lines.append(f"### {entity_name} `{entity.get('id')}` {template}".rstrip())
        fields = _dict_items(entity.get("fields"))
        lines.extend(
            [f"- `{field.get('name')}`：{field.get('type') or 'unknown'}{('：' + str(field.get('description'))) if field.get('description') else ''}" for field in fields]
            or ["- 尚未添加字段。"]
        )
    if not _dict_items(design.get("sceneEntities")):
        lines.append("- 当前 Endpoint 未创建场景实体。")
    lines.extend(["", "## Request 映射", ""])
    lines.extend(_mapping_lines(design, side="request") or ["- 无 Request 映射。"])
    lines.extend(["", "## Response 映射", ""])
    lines.extend(_mapping_lines(design, side="response") or ["- 无 Response 映射。"])
    lines.extend(["", "## 业务说明", ""])
    lines.extend(_business_description_lines(design) or ["- 无补充业务说明。"])
    lines.extend(["", "## 确认时数据来源", ""])
    snapshots = _dict_items(design.get("sourceSnapshots"))
    lines.extend(_source_snapshot_lines(snapshots) or ["- 未选择数据来源。"])
    return "\n".join(lines).rstrip() + "\n"


def _implementation_description_lines(design: dict[str, Any]) -> list[str]:
    """把 Endpoint 级实现描述渲染为安全的 Markdown 引用块。"""

    description = design.get("implementationDescription")
    if not isinstance(description, str) or not description.strip():
        return []
    return [
        f"> {_escape_markdown(line)}" if line else ">"
        for line in description.strip().splitlines()
    ]


def _mapping_lines(design: dict[str, Any], *, side: str) -> list[str]:
    """按 Request 或 Response 侧生成可读的字段映射表达式。"""

    lines: list[str] = []
    for mapping in _dict_items(design.get("fieldMappings")):
        endpoint = mapping.get("endpointField") if isinstance(mapping.get("endpointField"), dict) else {}
        if endpoint.get("side") != side or mapping.get("mappingType") == "unconfigured":
            continue
        endpoint_label = _endpoint_field_label(endpoint)
        if mapping.get("mappingType") == "business_description":
            lines.append(
                f"- {_escape_markdown(endpoint_label)}："
                f"{_escape_markdown(str(mapping.get('businessDescription') or ''))}"
            )
            continue
        middle = [
            label
            for label in (
                _entity_field_label(mapping.get("entityField")),
                _source_field_label(mapping.get("sourceField")),
            )
            if label
        ]
        labels = [endpoint_label, *middle] if side == "request" else [*reversed(middle), endpoint_label]
        if len(labels) > 1:
            lines.append(f"- {_escape_markdown(' → '.join(labels))}")
    return lines


def _business_description_lines(design: dict[str, Any]) -> list[str]:
    """按字段展示 Request 和 Response 的一句话业务说明。"""

    lines: list[str] = []
    for mapping in _dict_items(design.get("fieldMappings")):
        if mapping.get("mappingType") != "business_description":
            continue
        endpoint = mapping.get("endpointField") if isinstance(mapping.get("endpointField"), dict) else {}
        lines.extend(
            [
                f"### {_escape_markdown(_endpoint_field_label(endpoint))}",
                f"- 业务说明：{_escape_markdown(str(mapping.get('businessDescription') or ''))}",
            ]
        )
    return lines


def _source_snapshot_lines(snapshots: list[dict[str, Any]]) -> list[str]:
    """把脱敏来源快照渲染为表、字段和 Operation 级可读信息。"""

    lines: list[str] = []
    for snapshot in snapshots:
        source_id = str(snapshot.get("sourceId") or "")
        source_name = str(snapshot.get("name") or source_id)
        source_type = str(snapshot.get("sourceType") or "")
        details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
        lines.append(f"### {_escape_markdown(source_name)} `{_escape_markdown(source_id)}`（{source_type}）")
        if source_type == "database":
            database_fields: list[str] = []
            for column in _dict_items(details.get("columns")):
                name = str(column.get("name") or column.get("column_name") or "")
                table = str(details.get("table") or "")
                if name:
                    database_fields.append(f"{table}.{name}" if table else name)
            schemas = details.get("schemas") if isinstance(details.get("schemas"), dict) else {}
            for table, columns in schemas.items():
                for column in _dict_items(columns):
                    name = str(column.get("name") or column.get("column_name") or "")
                    if name:
                        database_fields.append(f"{table}.{name}")
            fields = list(dict.fromkeys(database_fields))
            lines.append(
                f"- 映射字段：{_escape_markdown('、'.join(fields))}"
                if fields
                else "- 映射字段：无"
            )
            continue
        operation = details.get("operation") if isinstance(details.get("operation"), dict) else {}
        operation_id = str(operation.get("operationId") or operation.get("id") or details.get("operationId") or "")
        method = str(operation.get("method") or "")
        path = str(operation.get("path") or "")
        lines.append(f"- Operation：`{_escape_markdown(operation_id)}` {method} {_escape_markdown(path)}".rstrip())
        fields = [
            f"{item.get('section')}.{item.get('path')}"
            for item in _dict_items(details.get("fields"))
            if str(item.get("section") or "") and str(item.get("path") or "")
        ]
        lines.append(
            f"- 映射字段：{_escape_markdown('、'.join(fields))}"
            if fields
            else "- 映射字段：无"
        )
    return lines


def _endpoint_field_label(value: dict[str, Any]) -> str:
    """把内嵌 Endpoint 字段转换为简洁标签。"""

    return f"{value.get('side')}.{value.get('location')}.{value.get('path')}"


def _entity_field_label(value: Any) -> str:
    """把内嵌实体字段引用转换为简洁标签。"""

    return f"{value.get('entityId')}.{value.get('path')}" if isinstance(value, dict) else ""


def _source_field_label(value: Any) -> str:
    """把内嵌来源字段转换为简洁标签。"""

    if not isinstance(value, dict):
        return ""
    if value.get("sourceType") == "database":
        return f"{value.get('sourceId')}.{value.get('table')}.{value.get('column')}"
    return f"{value.get('sourceId')}.{value.get('operationId')}.{value.get('section')}.{value.get('path')}"


def _escape_markdown(value: str) -> str:
    """转义 Markdown 表格中会破坏列结构的字符。"""

    return value.replace("|", "\\|").replace("\n", "<br>")


def _prepare_text_file(path: Path, content: str) -> Path:
    """在目标目录中写入唯一 UTF-8 临时文件但不发布目标路径。"""

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _publish_temporary_file(temporary: Path, path: Path) -> None:
    """将已准备完成的临时文件原子替换到正式路径。"""

    temporary.replace(path)


def _write_text_atomically(path: Path, content: str) -> None:
    """写入并原子替换单个 UTF-8 文本文件，供独立调用方复用。"""

    temporary = _prepare_text_file(path, content)
    try:
        _publish_temporary_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_file(path: Path, content: bytes | None) -> None:
    """恢复双文件写入前的单文件内容，缺失文件则保持缺失。"""

    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.restore")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _markdown_artifact_revision(markdown: str) -> str:
    """读取 Markdown 中的内部产物修订标记。"""

    match = re.search(r"xcodeagent-artifact-revision:\s*([0-9a-f]{32})", markdown)
    return match.group(1) if match else ""


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤列表中的非对象输入。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
