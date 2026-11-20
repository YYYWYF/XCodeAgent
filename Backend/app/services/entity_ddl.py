"""从当前已确认技术规划生成、修订及确认实体 DDL。"""

import json
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4
from weakref import WeakValueDictionary

from app.agents.entity_ddl import generate_entity_ddl
from app.domain.entity_tables import EntityDdlRecord, EntityTableRequest
from app.services.entity_ddl_validation import validate_entity_ddl
from app.services.entity_definitions import ENTITY_FIELD_NAME_RE, ENTITY_FIELD_TYPES, entity_database_table_name
from app.workspace.endpoint_design_documents import technical_plan_path
from app.workspace.entity_ddl_documents import read_confirmed_ddl, require_ddl_plan_version, write_confirmed_ddl


_STATE_LOCK = Lock()
_ENTITY_LOCKS = WeakValueDictionary()
_DRAFTS: OrderedDict[tuple[Path, str], EntityDdlRecord] = OrderedDict()
_MAX_DRAFTS = 64


class EntityDdlRequiredError(ValueError):
    """当前规划下尚无正式 DDL，调用方可引导用户审阅确认。"""


def _model_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """投影目标表与已确认字段，只把建表所需的业务信息传给模型。"""

    if not isinstance(entity.get("fields"), list) or not entity["fields"]:
        raise ValueError("当前实体尚未生成字段清单，请先完善并确认技术规划。")
    names: set[str] = set()
    fields = []
    for field in entity["fields"]:
        if not isinstance(field, dict):
            raise ValueError("实体字段定义无效，请修订并确认技术规划。")
        name = field.get("name")
        field_type = field.get("type")
        if (not isinstance(name, str) or not ENTITY_FIELD_NAME_RE.fullmatch(name) or name in names
                or not isinstance(field_type, str) or field_type not in ENTITY_FIELD_TYPES
                or not isinstance(field.get("required"), bool)):
            raise ValueError("实体包含无效或重复字段，请修订并确认技术规划。")
        names.add(name)
        projected = {"name": name, "type": field_type, "required": field["required"]}
        for key in ("label", "description"):
            if isinstance(field.get(key), str):
                projected[key] = field[key]
        if field_type == "enum" and isinstance(field.get("enum_values"), list):
            projected["enum_values"] = [value for value in field["enum_values"] if isinstance(value, str)]
        fields.append(projected)
    return {
        "name": entity.get("name") or entity["id"],
        "description": entity.get("description") or "",
        "table_name": entity_database_table_name(entity),
        "fields": fields,
    }


def _read_entity(path: Path, entity_id: str) -> tuple[dict[str, Any], str]:
    """从同一份已确认文件取得实体输入及其版本摘要。"""
    try:
        content = path.read_bytes()
        plan = json.loads(content.decode("utf-8"))
    except (OSError, ValueError):
        raise ValueError("请先完成并确认技术规划，再查看建表 DDL。") from None
    if (not isinstance(plan, dict) or plan.get("artifact_type") != "technical-plan"
            or plan.get("confirmation_status") != "confirmed"):
        raise ValueError("请先确认当前技术规划，再查看建表 DDL。")
    entities = plan.get("entities")
    matches = [item for item in entities if isinstance(item, dict) and item.get("id") == entity_id] \
        if isinstance(entities, list) else []
    if len(matches) != 1:
        raise ValueError("当前技术规划中不存在唯一的目标实体。")
    return _model_entity(matches[0]), sha256(content).hexdigest()


def _draft_for_version(key: tuple[Path, str], plan_digest: str) -> EntityDdlRecord | None:
    """恢复相同版本的内存草稿，释放已过期的规划版本。"""
    with _STATE_LOCK:
        draft = _DRAFTS.get(key)
        if draft is not None and draft.plan_digest == plan_digest:
            _DRAFTS.move_to_end(key)
            return draft
        _DRAFTS.pop(key, None)
    return None


def _stage_draft(key: tuple[Path, str], draft: EntityDdlRecord) -> None:
    """仅在校验成功后替换旧草稿，并限制进程内保留数量。"""
    with _STATE_LOCK:
        _DRAFTS[key] = draft
        _DRAFTS.move_to_end(key)
        if len(_DRAFTS) > _MAX_DRAFTS:
            _DRAFTS.popitem(last=False)


def _view(record: EntityDdlRecord, entity: dict[str, Any], *, confirmed: bool) -> dict[str, Any]:
    """投影前端展示以及确认当前版本所需的身份。"""
    return {
        "entityId": record.entity_id,
        "entityName": entity["name"],
        "tableName": record.table_name,
        "sql": record.sql,
        "draftId": record.draft_id,
        "confirmationStatus": "confirmed" if confirmed else "draft",
    }


def _confirmed_view(record: EntityDdlRecord, entity: dict[str, Any]) -> dict[str, Any]:
    """展示或执行前复核正式记录仍对应已确认实体。"""
    _validate_confirmed_record(record, entity)
    return _view(record, entity, confirmed=True)


def require_confirmed_entity_ddl(
    workspace: Path, entity: dict[str, Any], plan_digest: str,
) -> EntityDdlRecord:
    """使用调用方读取的同一规划版本验明正式 SQL，不调用模型或读取草稿。"""
    model_entity = _model_entity(entity)
    record = read_confirmed_ddl(workspace, entity["id"], plan_digest)
    if record is None:
        raise EntityDdlRequiredError(f"请先在实体「{model_entity['name']}」的「建表 DDL」页签确认并保存当前版本。")
    _validate_confirmed_record(record, model_entity)
    return record


def _validate_confirmed_record(record: EntityDdlRecord, entity: dict[str, Any]) -> None:
    """按正式规划校验已保存 SQL 的表名、字段和约束。"""
    if record.table_name != entity["table_name"]:
        raise ValueError("已保存的建表 DDL 与当前表名不一致。")
    validate_entity_ddl(record.sql, entity)


def run_entity_ddl_action(request: EntityTableRequest) -> dict[str, Any]:
    """对当前实体串行读取、修订或确认，模型输出仅进入内存待确认稿。"""
    workspace = Path(request.workspace_root).expanduser().resolve()
    plan_path = technical_plan_path(workspace).resolve()
    if not plan_path.is_relative_to(workspace):
        raise ValueError("技术规划路径超出当前工作区。")
    key = (workspace, request.entity_id)
    with _STATE_LOCK:
        lock = _ENTITY_LOCKS.setdefault(key, Lock())
    with lock:
        return _apply_ddl_action(request, workspace, plan_path, key)


def _apply_ddl_action(
    request: EntityTableRequest, workspace: Path, plan_path: Path, key: tuple[Path, str],
) -> dict[str, Any]:
    """核对当前规划和草稿版本，并在显式确认后提升为正式记录。"""
    entity, plan_digest = _read_entity(plan_path, request.entity_id)
    confirmed = read_confirmed_ddl(workspace, request.entity_id, plan_digest)
    if confirmed is not None:
        if request.action == "preview_ddl" or (
            request.action == "confirm_ddl" and request.draft_id == confirmed.draft_id
        ):
            return _confirmed_view(confirmed, entity)
        raise ValueError("当前实体已有已确认 DDL，请重新读取。")

    draft = _draft_for_version(key, plan_digest)
    if request.action == "preview_ddl" and draft is not None:
        return _view(draft, entity, confirmed=False)
    if request.action != "preview_ddl" and (draft is None or draft.draft_id != request.draft_id):
        raise ValueError("DDL 草稿已变化或失效，请重新读取后再操作。")
    if request.action == "confirm_ddl" and draft is not None:
        confirmed = write_confirmed_ddl(workspace, plan_path, draft)
        with _STATE_LOCK:
            _DRAFTS.pop(key, None)
        return _confirmed_view(confirmed, entity)

    # 修订基于用户实际审阅的 SQL。模型调用、校验或上游检查失败时保留旧草稿。
    if draft is None:
        sql = generate_entity_ddl(entity)
    else:
        sql = generate_entity_ddl(entity, previous_sql=draft.sql, feedback=(request.feedback or "").strip())
    validate_entity_ddl(sql, entity)
    require_ddl_plan_version(plan_path, plan_digest)
    confirmed = read_confirmed_ddl(workspace, request.entity_id, plan_digest)
    if confirmed is not None:
        if request.action == "preview_ddl":
            return _confirmed_view(confirmed, entity)
        raise ValueError("生成期间当前实体已确认 DDL，请重新读取。")

    revised = EntityDdlRecord(
        entity_id=request.entity_id, table_name=entity["table_name"],
        plan_digest=plan_digest, draft_id=uuid4().hex, sql=sql,
    )
    _stage_draft(key, revised)
    return _view(revised, entity, confirmed=False)
