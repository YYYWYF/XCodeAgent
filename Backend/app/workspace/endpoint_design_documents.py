"""Endpoint API 设计正式产物的当前版读写服务。"""

from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.api_design import API_DESIGN_SCHEMA_VERSION, EndpointApiDesign
from app.services.artifact_invalidation import canonical_sha256
from app.services.api_design_mapping_rules import mapping_business_description


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

    directory = Path(workspace_root).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "plans" / "endpoints"
    stem = endpoint_design_stem(api_contract_id, endpoint_id)
    return directory / f"{stem}.json", directory / f"{stem}.md"


def technical_plan_path(workspace_root: str | Path) -> Path:
    """返回当前工作区 TechnicalPlan JSON 的规范路径。"""

    return Path(workspace_root).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "plans" / "technical-plan.json"


def technical_plan_sha256(workspace_root: str | Path) -> str:
    """计算当前 TechnicalPlan 文件哈希，作为 API 设计直接上游指纹。"""

    path = technical_plan_path(workspace_root)
    if not path.is_file():
        raise ValueError("缺少已确认的 technical-plan.json。")
    return canonical_sha256(path)


def endpoint_upstream_content(workspace_root: str | Path, api_contract_id: str, endpoint_id: str) -> dict[str, Any]:
    """取出某个 Endpoint 真正依赖的上游内容，用于内容级失效判断。

    为什么不能只看整份 TechnicalPlan 的哈希：计划阶段每一轮都会让模型整份重新生成，
    即使后端契约一字未变，措辞和字段顺序也会变，文件哈希必然不同。按哈希判断会让
    所有既有字段映射在每一轮迭代里全部作废，用户必须把已确认的映射重新配一遍。

    真正决定映射是否仍然成立的是这个 Endpoint 的**契约语义**：
    - 该 Endpoint 自身的定义（方法、路径、参数、请求 / 响应 schema 引用）；
    - 所属分组的配置（base_path、authentication、entity_ids、分组名）；
    - 分组内被它引用的类型定义（schemas）；
    - 它绑定的实体定义（字段一列一列比对，字段变了映射就必须重配）。
    """

    path = technical_plan_path(workspace_root)
    if not path.is_file():
        raise ValueError("缺少已确认的 technical-plan.json。")
    from app.workspace.plan_documents import load_project_plan_json

    plan = load_project_plan_json(path, hydrate_detail_designs=True)
    contracts = [item for item in plan.get("api_contracts") or [] if isinstance(item, dict)]
    contract = next((item for item in contracts if item.get("id") == api_contract_id), None)
    if contract is None:
        return {}
    endpoint = next(
        (item for item in contract.get("endpoints") or []
         if isinstance(item, dict) and item.get("id") == endpoint_id),
        None,
    )
    if endpoint is None:
        return {}
    schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
    referenced = _referenced_schema_names(endpoint, schemas)
    entities = [
        entity for entity in plan.get("entities") or []
        if isinstance(entity, dict) and str(entity.get("id") or "") in {
            str(item) for item in contract.get("entity_ids") or []
        }
    ]
    return {
        "contract": {
            key: contract.get(key)
            for key in ("id", "name", "base_path", "authentication", "entity_ids")
        },
        "endpoint": endpoint,
        "schemas": {name: schemas.get(name) for name in referenced},
        "entities": entities,
    }


def endpoint_upstream_sha256(workspace_root: str | Path, api_contract_id: str, endpoint_id: str) -> str:
    """计算 Endpoint 依赖内容的规范哈希；读取失败返回空串（视为无法判断）。"""

    try:
        content = endpoint_upstream_content(workspace_root, api_contract_id, endpoint_id)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ""
    if not content:
        return ""
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _referenced_schema_names(endpoint: dict[str, Any], schemas: dict[str, Any]) -> list[str]:
    """收集该 Endpoint 通过请求 / 响应引用可达的类型名（含嵌套引用）。"""

    found: list[str] = []
    pending = [
        str(endpoint.get(key) or "").split("/")[-1]
        for key in ("request_schema_ref", "response_schema_ref")
        if endpoint.get(key)
    ]
    while pending:
        name = pending.pop()
        if not name or name in found or name not in schemas:
            continue
        found.append(name)
        for ref in re.findall(r'"\$ref"\s*:\s*"([^"]+)"', json.dumps(schemas[name], ensure_ascii=False)):
            pending.append(ref.split("/")[-1])
    return sorted(found)


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
    """读取并严格校验当前版双文件 API 设计及字段映射结构。"""

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
            if not expected:
                return None
            if expected == technical_plan_sha256(workspace_root):
                return design.model_dump(mode="json", by_alias=True, exclude_none=True)
        except ValueError:
            return None
        # 整份计划哈希不同不代表映射失效：计划阶段每轮都会整份重生成，措辞与字段顺序
        # 的变化也会改变文件哈希。只有该 Endpoint 真正依赖的契约语义变了，映射才需要重配。
        current = endpoint_upstream_content(workspace_root, api_contract_id, endpoint_id)
        if not current:
            return None
        design_payload = design.model_dump(mode="json", by_alias=True, exclude_none=True)
        if not _endpoint_content_matches(design_payload, current):
            return None
    return design.model_dump(mode="json", by_alias=True, exclude_none=True)


def _endpoint_content_matches(design: dict[str, Any], current: dict[str, Any]) -> bool:
    """判断已确认设计所依据的接口定义是否仍与当前技术规划一致。

    设计里存了确认当时的 `endpointContract`，因此可以逐字段比对：接口自身定义变了
    （方法、路径、参数、schema 引用、错误码），或者它引用的类型 / 绑定实体变了，
    映射就必须重新配置；只是技术规划整体被重写（措辞、顺序）则继续沿用。
    """

    previous = design.get("endpointContract")
    if not isinstance(previous, dict) or not previous:
        return False
    if previous != current.get("endpoint"):
        return False
    # 绑定的数据源快照也要能对上当前实体字段，否则映射指向的列可能已经不存在。
    entities = {str(item.get("id") or ""): item for item in current.get("entities") or []}
    for snapshot in design.get("sourceSnapshots") or []:
        if not isinstance(snapshot, dict) or snapshot.get("sourceType") != "database":
            continue
        details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
        table = str(details.get("table") or "")
        entity = next(
            (
                item for item in entities.values()
                if str(item.get("table_name") or "") == table or str(item.get("id") or "") == table
            ),
            None,
        )
        if entity is None:
            continue
        expected_fields = {
            str(field.get("name") or "")
            for field in entity.get("fields") or []
            if isinstance(field, dict) and str(field.get("name") or "").strip()
        }
        used_fields = {
            str(column.get("name") or column.get("column_name") or "")
            for column in details.get("columns") or []
            if isinstance(column, dict)
        }
        if used_fields and not used_fields <= expected_fields:
            return False
    return True


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
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if raw.get("schemaVersion") != API_DESIGN_SCHEMA_VERSION:
            return {"status": "stale", "designed": False, "reason": "API 设计需重新配置。"}
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return {"status": "stale", "designed": False, "reason": "API 设计产物无法读取。"}
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
        "reason": "API 设计格式无效、双文件不完整或 TechnicalPlan 已变化。",
    }


def render_endpoint_design_markdown(design: dict[str, Any]) -> str:
    """把字段映射、数据库写入、外部 API 固定值和查询配置渲染为 Markdown 正式产物。"""

    endpoint = design.get("endpointContract") if isinstance(design.get("endpointContract"), dict) else {}
    lines = [
        f"# Endpoint 字段映射：{endpoint.get('method') or 'API'} {endpoint.get('path') or design.get('endpointId')}",
        "",
        f"- API Contract：`{design.get('apiContractId') or ''}`",
        f"- Endpoint：`{design.get('endpointId') or ''}`",
        f"- 数据库操作：`{design.get('databaseOperation') or '无数据库映射'}`",
        f"- 状态：已确认",
        f"<!-- devagentstudio-artifact-revision: {design.get('artifactRevision') or ''} -->",
        "",
    ]
    if (design.get("sourceBinding") or {}).get("sourceType") == "static":
        # 静态模式不展示映射说明，完整内容是正式配置而非示例。
        lines.extend(["## 静态数据", "", "```json", json.dumps(design.get("staticData"), ensure_ascii=False, indent=2), "```", ""])
    else:
        lines.extend(["## 接口映射说明", ""])
        lines.extend(_implementation_description_lines(design) or ["- 未补充 接口映射说明。"])
    lines.extend(["", "## Request 映射", ""])
    lines.extend(_mapping_lines(design, side="request") or ["- 无 Request 映射。"])
    lines.extend(["", "## 外部 API 固定参数", ""])
    lines.extend(_external_api_binding_lines(design) or ["- 无外部 API 固定参数。"])
    lines.extend(["", "## 数据库写入字段", ""])
    lines.extend(_database_write_lines(design) or ["- 无数据库写入字段。"])
    lines.extend(["", "## Response 映射", ""])
    lines.extend(_mapping_lines(design, side="response") or ["- 无 Response 映射。"])
    lines.extend(["", "## 数据库查询条件", ""])
    lines.extend(_condition_lines(design) or ["- 无数据库查询条件。"])
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
        if mapping.get("mappingType") == "value_mapping":
            from app.services.api_design_values import value_rule_summary
            lines.append(f"- {_escape_markdown(endpoint_label)} ← {_escape_markdown(value_rule_summary(mapping.get('right')))}")
            continue
        if mapping.get("mappingType") == "business_description":
            lines.append(
                f"- {_escape_markdown(endpoint_label)}："
                f"{_escape_markdown(str(mapping.get('businessDescription') or ''))}"
            )
            continue
        middle = [" + ".join(_source_field_label(item) for item in mapping.get("sourceFields", []))]
        middle.append({"direct": "直接映射", "single_field_description": "单字段业务处理", "multi_field_description": "多字段业务处理"}.get(mapping.get("processingType"), ""))
        middle = [label for label in middle if label]
        labels = [endpoint_label, *middle] if side == "request" else [*reversed(middle), endpoint_label]
        if len(labels) > 1:
            lines.append(f"- {_escape_markdown(' → '.join(labels))}")
    return lines


def _condition_lines(design: dict[str, Any]) -> list[str]:
    """按顶层和子组的 AND/OR 关系渲染当前查询树。"""

    query = design.get("databaseQuery")
    if not isinstance(query, dict) or not query.get("items"):
        return []
    from app.services.api_design import _database_query_flow
    return [f"- {_escape_markdown(_database_query_flow(query))}"]


def _database_write_lines(design: dict[str, Any]) -> list[str]:
    """按目标列展示已确认的数据库写入来源和值。"""

    lines: list[str] = []
    for write in _dict_items(design.get("databaseWrites")):
        right = write.get("right") if isinstance(write.get("right"), dict) else {}
        target = ".".join(
            str(write.get(key) or "")
            for key in ("schema", "table", "column")
            if str(write.get(key) or "")
        )
        if right.get("kind") == "endpoint":
            endpoint_field = right.get("endpointField") if isinstance(right.get("endpointField"), dict) else {}
            value = f"接口参数 {_endpoint_field_label(endpoint_field)}"
        elif right.get("kind") == "fixed":
            value = f"固定值 {right.get('value')!r}"
        else:
            from app.services.api_design_values import value_rule_summary
            value = value_rule_summary(right)
        if target:
            lines.append(f"- {_escape_markdown(target)} ← {_escape_markdown(value)}")
    return lines


def _external_api_binding_lines(design: dict[str, Any]) -> list[str]:
    """展示已确认的外部 API 请求参数固定值。"""

    lines: list[str] = []
    for item in _dict_items(design.get("externalApiBindings")):
        external_field = item.get("externalField") if isinstance(item.get("externalField"), dict) else {}
        target = _source_field_label(external_field)
        if target:
            from app.services.api_design_values import value_rule_summary
            lines.append(f"- {_escape_markdown(target)} ← {_escape_markdown(value_rule_summary(item.get('right')))}")
    return lines


def _business_description_lines(design: dict[str, Any]) -> list[str]:
    """按字段展示 Request 和 Response 的多行业务处理内容。"""

    lines: list[str] = []
    for mapping in _dict_items(design.get("fieldMappings")):
        if not mapping.get("businessDescription"):
            continue
        endpoint = mapping.get("endpointField") if isinstance(mapping.get("endpointField"), dict) else {}
        lines.extend(
            [
                f"### {_escape_markdown(_endpoint_field_label(endpoint))}",
                f"- 业务说明：{_escape_markdown(mapping_business_description(mapping))}",
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


def _source_field_label(value: Any) -> str:
    """把内嵌来源字段转换为简洁标签。"""

    if not isinstance(value, dict):
        return ""
    if value.get("sourceType") == "static":
        return f"静态数据.{value.get('path')}"
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

    match = re.search(r"devagentstudio-artifact-revision:\s*([0-9a-f]{32})", markdown)
    return match.group(1) if match else ""


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤列表中的非对象输入。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
