"""工作区内已确认的实体 DDL，以技术规划版本绑定原子保存。"""

from hashlib import sha256
from pathlib import Path

from portalocker import Lock
from portalocker.exceptions import LockException

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.entity_tables import EntityDdlRecord
from app.workspace.json_documents import write_json_atomic


def _contained_path(workspace: Path, *parts: str) -> Path:
    """限定正式记录及写入锁位于当前工作区。"""
    path = workspace.joinpath(WORKSPACE_ARTIFACT_DIR, *parts)
    if not path.resolve().is_relative_to(workspace):
        raise ValueError("DDL 保存路径超出当前工作区。")
    return path


def require_ddl_plan_version(plan_path: Path, expected_digest: str) -> None:
    """在发布草稿或写入确认结果前复核当前已确认技术规划文件。"""
    try:
        current = sha256(plan_path.read_bytes()).hexdigest()
    except OSError:
        current = None
    if current != expected_digest:
        raise ValueError("技术规划已变化，请重新读取当前版本的建表 DDL。")


def _record_path(workspace: Path, entity_id: str) -> Path:
    """通过实体身份摘要定位唯一正式 JSON 文件。"""
    name = sha256(entity_id.encode("utf-8")).hexdigest()
    return _contained_path(workspace, "plans", "entity-ddl", f"{name}.json")


def read_confirmed_ddl(workspace: Path, entity_id: str, plan_digest: str) -> EntityDdlRecord | None:
    """只返回绑定当前已确认规划的正式 DDL，损坏记录显示读取错误。"""
    path = _record_path(workspace, entity_id)
    try:
        record = EntityDdlRecord.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError("已保存的建表 DDL 无法读取。") from exc
    if record.entity_id != entity_id:
        raise ValueError("已保存的建表 DDL 不属于当前实体。")
    return record if record.plan_digest == plan_digest else None


def write_confirmed_ddl(workspace: Path, plan_path: Path, draft: EntityDdlRecord) -> EntityDdlRecord:
    """短事务内拒绝覆盖同一规划版本的其他确认，允许同一草稿幂等重试。"""
    lock_path = _contained_path(workspace, "runtime", "entity-ddl.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with Lock(str(lock_path), mode="a", timeout=2):
            require_ddl_plan_version(plan_path, draft.plan_digest)
            current = read_confirmed_ddl(workspace, draft.entity_id, draft.plan_digest)
            if current is not None:
                if current.draft_id != draft.draft_id:
                    raise ValueError("当前实体已有已确认 DDL，请重新读取。")
                return current
            write_json_atomic(_record_path(workspace, draft.entity_id), draft.model_dump())
    except (OSError, LockException) as exc:
        raise ValueError("建表 DDL 保存失败，请重试。") from exc
    return draft
