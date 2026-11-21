"""把本轮各产物的变更类型落成快照，供开发阶段打标、禁用与重做判定。

**为什么需要它**：技术规划的变更类型（新增 / 修改 / 未变更 / 删除）是"本轮相对上一版
基线"的视图，按契约不能写进正式产物（`plans/technical-plan.json` 要进 git，也不能
被下游校验当成业务字段）。可开发阶段——工作台的"今天想从哪里开始"入口、测试门禁、
构建跳过逻辑——恰恰需要这个事实，否则用户会看到一个本轮已经删除的页面仍然可以点进去
开发，已经修改过的接口也不会被重做。

因此本模块在 TechnicalPlan **确认**那一刻，把最终变更类型冻结成一份独立快照
（`.devagentstudio/development-change-snapshot.json`），而不是让下游各自重新比对基线：

- 快照只在确认时由计划阶段写一次，之后是只读事实，不参与任何生成决策；
- 基线来源与计划阶段合并时完全一致（`technical_plan_carryover`），保证"界面看到的
  标记"与"构建跳过的判断"是同一份结论，不会各算各的。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.repository_branch import read_workspace_branch_name

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


def write_change_snapshot(
    workspace: str | Path,
    *,
    technical_plan: dict[str, Any],
) -> dict[str, Any]:
    """从已确认 TechnicalPlan 提取变更类型并写盘，返回写入的载荷。

    `technical_plan` 必须是确认界面使用的**展示版**（带 `_change_status`）。正式版没有
    标记，用它只会写出一份全 "unchanged" 的快照，所以这里对无标记的输入直接报错，
    避免把"没有变更信息"误当成"什么都没变"。
    """

    root = Path(workspace).expanduser().resolve()
    # 没有逐项标记的输入（例如已经剥离过标记的正式版）只会写出全 "unchanged"，
    # 那种快照比没有更危险：下游会据此认为"本轮什么都没变"并跳过重做。
    # 因此这里显式拒绝，让调用方留下"无变更信息"的空缺，由门禁拦住。
    records = [
        record
        for field in ("pages", "entities", "api_contracts")
        for record in (technical_plan.get(field) or [])
        if isinstance(record, dict)
    ]
    if not any(CHANGE_STATUS_FIELD in record for record in records):
        raise ValueError("本轮变更快照缺少逐项变更标记，拒绝写出全未变更的快照。")
    pages: dict[str, str] = {}
    for page in technical_plan.get("pages") or []:
        if isinstance(page, dict) and str(page.get("pageId") or "").strip():
            pages[str(page["pageId"]).strip()] = _status(page)
    entities: dict[str, str] = {}
    for entity in technical_plan.get("entities") or []:
        if isinstance(entity, dict) and str(entity.get("id") or "").strip():
            entities[str(entity["id"]).strip()] = _status(entity)
    endpoints: dict[str, dict[str, str]] = {}
    for contract in technical_plan.get("api_contracts") or []:
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "").strip()
        contract_status = _status(contract)
        for endpoint in contract.get("endpoints") or []:
            endpoint_id = str((endpoint or {}).get("id") or "").strip() if isinstance(endpoint, dict) else ""
            if not contract_id or not endpoint_id:
                continue
            # 接口自身的标记优先；缺失时继承分组结论。"删除"必须显式落在接口上，
            # 不能被分组状态覆盖掉，否则已删页面的专属接口会被当成需要开发。
            status = _status(endpoint) if CHANGE_STATUS_FIELD in endpoint else contract_status
            endpoints.setdefault(contract_id, {})[endpoint_id] = status

    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": read_workspace_branch_name(root),
        "writtenAt": datetime.now(UTC).isoformat(),
        "pages": pages,
        "entities": entities,
        "endpoints": endpoints,
    }
    path = change_snapshot_path(root)
    previous = read_change_snapshot(root)
    if previous is None or any(
        previous.get(field) != payload.get(field)
        for field in ("pages", "entities", "endpoints")
    ):
        _reset_previous_snapshot_redevelopment(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("development_change_snapshot_persist_failed")
    return payload


def clear_change_snapshot(workspace: str | Path) -> None:
    """删除旧快照；新迭代开始或确认失败时调用，避免下一轮沿用上一轮的结论。"""

    try:
        change_snapshot_path(workspace).unlink(missing_ok=True)
    except OSError:
        logger.exception("development_change_snapshot_clear_failed")
