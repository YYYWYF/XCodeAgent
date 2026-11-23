"""冻结开发阶段相对本迭代起始基线的变更类型，供入口、禁用与重做判定。

计划审阅标识只描述用户当时看到的比较结果，不能作为开发资格事实。
确认最终 TechnicalPlan 时独立比较跨迭代冻结的基线，忽略 added/modified/unchanged
展示标记；页面同时考虑产品定义和依赖接口的变化。开发完成记录与快照仍分开保存。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.product_plan_carryover import read_product_plan_carryover
from app.services.repository_branch import read_workspace_branch_name
from app.services.technical_plan_carryover import (
    drop_generated_fields, read_technical_plan_carryover,
)
from app.services.api_schema_refs import normalize_local_schema_ref

logger = logging.getLogger("uvicorn.error")

CHANGE_SNAPSHOT_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "development-change-snapshot.json"

_SCHEMA_VERSION = 1

# 与 `technical_plan_merger` 一致的展示标记字段名。
CHANGE_STATUS_FIELD = "_change_status"


def change_snapshot_path(workspace: str | Path) -> Path:
    """返回工作区本轮变更快照的文件路径。"""

    return Path(workspace).expanduser().resolve() / CHANGE_SNAPSHOT_RELATIVE_PATH


def read_change_snapshot(workspace: str | Path) -> dict[str, Any] | None:
    """读取本轮变更快照；缺失或损坏返回 None（调用方按"无变更信息"处理）。"""

    try:
        payload = json.loads(change_snapshot_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("pages"), dict) and not isinstance(payload.get("endpoints"), dict):
        return None
    return payload


def _status(record: Any) -> str:
    """读取单个产物记录上的变更类型，未知一律按未变更处理。"""

    value = str((record or {}).get(CHANGE_STATUS_FIELD) or "").strip() if isinstance(record, dict) else ""
    return value if value in {"added", "modified", "unchanged", "deleted"} else "unchanged"


def _reset_previous_snapshot_redevelopment(workspace: str | Path) -> None:
    """清空上一变更快照对应的已重做记录，防止旧证据抵消新的修改或删除。

    已重做记录只有“目标键”，没有绑定快照内容。新 TechnicalPlan 确认后，如果不作废旧
    记录，一个之前开发完成的接口随后被标记 deleted，门禁仍会把它当作已处理的未变更
    接口，在页面删除后提前进入测试。清理与新快照在同一工作区锁内完成，失败时不继续
    写入新快照，避免形成“新快照 + 旧已重做证据”的不安全组合。
    """

    from app.services.application_lifecycle import (
        _application_lifecycle_lock,
        application_lifecycle_path,
        load_application_lifecycle,
        write_application_lifecycle,
    )
    from app.services.development_artifacts import _REDEVELOPED_EXTENSION

    root = Path(workspace).expanduser().resolve()
    try:
        with _application_lifecycle_lock(application_lifecycle_path(root)):
            state = load_application_lifecycle(root)
            if state is None:
                return
            extensions = state.extensions or {}
            if _REDEVELOPED_EXTENSION not in extensions:
                return
            updated = state.model_copy(update={
                "extensions": {
                    key: value for key, value in extensions.items()
                    if key != _REDEVELOPED_EXTENSION
                },
                "revision": state.revision + 1,
                "updated_at": datetime.now(UTC),
            })
            write_application_lifecycle(root, updated, expected_revision=state.revision)
    except (OSError, ValueError) as exc:
        logger.exception("development_change_snapshot_redeveloped_reset_failed")
        raise ValueError("无法重置上一技术规划的已开发记录。") from exc


def _records(value: Any, field: str, key: str) -> dict[str, dict[str, Any]]:
    """校验当前规划目录的稳定身份，拒绝坏数据造成目标静默退出快照。"""

    records = value.get(field, []) if isinstance(value, dict) else []
    if not isinstance(records, list):
        raise ValueError(f"开发变更比较要求 {field} 为数组。")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        identity = record.get(key) if isinstance(record, dict) else None
        if not isinstance(identity, str) or not identity.strip() or identity in result:
            raise ValueError(f"开发变更比较要求 {field} 具有唯一的 {key}。")
        result[identity] = record
    return result


def _content(value: Any) -> Any:
    """去掉审阅标记和运行派生内容，并规范集合顺序，只比较正式业务内容。"""

    if isinstance(value, dict):
        return {key: _content(child) for key, child in sorted(value.items())
                if key not in {CHANGE_STATUS_FIELD, "confirmation_status", "detail_design", "entity_detail_plans", "page_implementation_contracts"}}
    if isinstance(value, list):
        children = [_content(child) for child in value]
        # 身份列表及纯值集合重排不改变开发要求。
        return sorted(children, key=lambda child: json.dumps(child, sort_keys=True, ensure_ascii=False))
    return value


def _changed(current: dict[str, Any] | None, previous: dict[str, Any] | None) -> str:
    """按最终业务内容计算增删改，只有 deleted 是正式删除事实而非审阅标记。"""

    if current is None or _status(current) == "deleted":
        return "deleted"
    if previous is None or _status(previous) == "deleted":
        return "added"
    return "unchanged" if _content(current) == _content(previous) else "modified"


def _endpoint_content(contract: dict[str, Any], endpoint: dict[str, Any]) -> Any:
    """展开接口实际引用的请求、响应类型，间接及循环引用都参与独立开发比较。"""

    schemas = contract.get("schemas") or {}
    referenced: dict[str, Any] = {}
    contract_id = str(contract.get("id") or "")

    def visit(value: Any) -> None:
        """沿本契约类型引用展开，已访问名称终止循环。"""

        if isinstance(value, dict):
            if value.get("$ref"):
                add(normalize_local_schema_ref(value["$ref"], contract_id=contract_id))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    def add(name: str) -> None:
        """收集类型定义；悬空引用明确失败而非伪造未变更。"""

        if name in referenced:
            return
        if name not in schemas:
            raise ValueError(f"开发比较遇到缺失类型：{contract_id}.{name}。")
        referenced[name] = schemas[name]
        visit(schemas[name])

    for key in ("request_schema_ref", "response_schema_ref"):
        if endpoint.get(key):
            add(normalize_local_schema_ref(endpoint[key], contract_id=contract_id))
    shell = {key: value for key, value in contract.items() if key not in {"endpoints", "schemas"}}
    return _content({"contract": shell, "endpoint": endpoint, "schemas": referenced})


def _endpoint_status(
    contract: dict[str, Any], endpoint: dict[str, Any], previous: dict[str, Any] | None,
    old_endpoint: dict[str, Any] | None,
) -> str:
    """按接口外壳、正文和依赖类型比较开发状态，忽略全部非删除审阅标识。"""

    if _status(contract) == "deleted" or _status(endpoint) == "deleted":
        return "deleted"
    if previous is None or old_endpoint is None or _status(old_endpoint) == "deleted":
        return "added"
    return "unchanged" if _endpoint_content(contract, endpoint) == _endpoint_content(previous, old_endpoint) else "modified"


def build_development_change_snapshot(
    workspace: str | Path, *, technical_plan: dict[str, Any], product_plan: dict[str, Any],
) -> dict[str, Any]:
    """按冻结的上迭代基线独立计算完整开发快照，不消费计划审阅 diff。"""

    baseline = drop_generated_fields(read_technical_plan_carryover(workspace) or {})
    current = drop_generated_fields(technical_plan)
    base_entities = _records(baseline, "entities", "id")
    entities = {
        key: _changed(record, base_entities.get(key))
        for key, record in _records(current, "entities", "id").items()
    }
    for key in base_entities:
        entities.setdefault(key, "deleted")
    endpoints: dict[str, dict[str, str]] = {}
    base_contracts = _records(baseline, "api_contracts", "id")
    for key, contract in _records(current, "api_contracts", "id").items():
        previous = base_contracts.get(key)
        old_endpoints = _records(previous or {}, "endpoints", "id")
        endpoints[key] = {
            endpoint_id: _endpoint_status(contract, endpoint, previous, old_endpoints.get(endpoint_id))
            for endpoint_id, endpoint in _records(contract, "endpoints", "id").items()
        }
        for endpoint_id in old_endpoints:
            endpoints[key].setdefault(endpoint_id, "deleted")
    for key, contract in base_contracts.items():
        if key not in endpoints:
            endpoints[key] = {endpoint_id: "deleted" for endpoint_id in _records(contract, "endpoints", "id")}
    technical_pages = _records(current, "pages", "pageId")
    base_pages = _records(baseline, "pages", "pageId")
    carried = read_product_plan_carryover(workspace)
    pages: dict[str, str] = {}
    for key, page in _records(product_plan, "pages", "pageId").items():
        old_product = (carried.get(key) or {}).get("development_page")
        previous = base_pages.get(key)
        status = _changed(page, old_product)
        if _status(technical_pages.get(key)) == "deleted":
            status = "deleted"
        elif status == "unchanged" and _changed(technical_pages.get(key), previous) != "unchanged":
            status = "modified"
        # 产品与技术引用未变，但依赖接口的响应、参数或删除发生变化时页面仍须重做。
        references = (technical_pages.get(key) or previous or {}).get("references") or {}
        dependencies = {item.get("endpoint_id") for item in references.get("endpoint_dependencies", []) if isinstance(item, dict)}
        for binding in references.get("action_implementations", []):
            if not isinstance(binding, dict):
                continue
            dependencies.add(binding.get("endpointId"))
            dependencies.update(item.get("endpointId") for item in binding.get("stepBindings", []) if isinstance(item, dict))
        if status == "unchanged" and any(
            endpoint_id in dependencies and change != "unchanged"
            for changes in endpoints.values() for endpoint_id, change in changes.items()
        ):
            status = "modified"
        pages[key] = status
    for key in set(carried) | set(base_pages):
        pages.setdefault(key, "deleted")
    return {"pages": pages, "entities": entities, "endpoints": endpoints}


def write_change_snapshot(
    workspace: str | Path, *, technical_plan: dict[str, Any], product_plan: dict[str, Any],
) -> dict[str, Any]:
    """独立比较最终规划与迭代基线，并原子冻结开发入口使用的变更事实。"""

    root = Path(workspace).expanduser().resolve()
    changes = build_development_change_snapshot(root, technical_plan=technical_plan, product_plan=product_plan)
    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": read_workspace_branch_name(root),
        "writtenAt": datetime.now(UTC).isoformat(),
        **changes,
    }
    path = change_snapshot_path(root)
    previous = read_change_snapshot(root)
    if previous is None or any(
        previous.get(field) != payload.get(field)
        for field in ("pages", "entities", "endpoints")
    ):
        _reset_previous_snapshot_redevelopment(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".development-change-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        Path(temporary).unlink(missing_ok=True)
        logger.exception("development_change_snapshot_persist_failed")
        raise ValueError("开发变更快照写入失败，未发布开发就绪状态。") from exc
    return payload


def clear_change_snapshot(workspace: str | Path) -> None:
    """删除旧快照；新迭代开始或确认失败时调用，避免下一轮沿用上一轮的结论。"""

    try:
        change_snapshot_path(workspace).unlink(missing_ok=True)
    except OSError:
        logger.exception("development_change_snapshot_clear_failed")
