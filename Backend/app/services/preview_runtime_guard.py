
from app.branding import WORKSPACE_ARTIFACT_DIR
"""预览维护与应用任务共享的原子互斥栅栏。"""

import json
from pathlib import Path
from threading import RLock
from typing import Any


maintenance_lock = RLock()
_owners: dict[str, dict[str, Any]] = {}
_pending: dict[tuple[str, str], dict[str, Any]] = {}


def workspace_key(workspace: str | Path) -> str:
    """规范化工作区，使不同入口共享同一占用。"""
    return str(Path(workspace).expanduser().resolve())


def maintenance_owner(workspace: str | Path) -> dict[str, Any] | None:
    """读取当前维护占用的公开身份。"""
    with maintenance_lock:
        key = workspace_key(workspace)
        path = Path(key) / WORKSPACE_ARTIFACT_DIR / 'runtime/preview-maintenance.json'
        owner = _owners.get(key)
        if owner is None and path.is_file():
            owner = json.loads(path.read_text(encoding="utf-8")) or None
        return dict(owner) if owner else None


def require_no_maintenance(workspace: str | Path, thread_id: str = "") -> None:
    """保留调用边界但不再让预览维护阻断普通应用任务。"""
    del workspace, thread_id


def claim_maintenance(workspace: str, thread_id: str, action: str) -> None:
    """调用方持有共享锁时登记独占维护身份。"""
    with maintenance_lock:
        owner = maintenance_owner(workspace)
        if owner and owner["threadId"] != thread_id:
            raise RuntimeError("当前应用已有预览维护任务，请完成或停止后再操作。")
        _owners[workspace_key(workspace)] = {"threadId": thread_id, "action": action}
        path = Path(workspace_key(workspace)) / WORKSPACE_ARTIFACT_DIR / 'runtime/preview-maintenance.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(_owners[workspace_key(workspace)]), encoding="utf-8")
        temporary.replace(path)


def release_maintenance(workspace: str, thread_id: str) -> None:
    """只释放匹配会话的维护占用。"""
    with maintenance_lock:
        owner = maintenance_owner(workspace)
        if owner and owner["threadId"] == thread_id:
            _owners.pop(workspace_key(workspace), None)
            path = Path(workspace_key(workspace)) / WORKSPACE_ARTIFACT_DIR / 'runtime/preview-maintenance.json'
            temporary = path.with_suffix(".tmp")
            temporary.write_text("{}", encoding="utf-8")
            temporary.replace(path)


def record_product_interaction(workspace: str, thread_id: str, data: dict[str, Any]) -> None:
    """投影独立产品对话的待确认占用，确认或拒绝终态后移除。"""
    with maintenance_lock:
        status = data.get("summary", {}).get("status") or data.get("state", {}).get("status") or data.get("status")
        key = (workspace_key(workspace), thread_id)
        if status in {"requires_user_input", "awaiting_user", "awaiting_confirmation"}:
            _pending[key] = {"threadId": thread_id, "message": "当前应用有对话正在等待确认，请完成或明确停止后再操作。"}
        else:
            _pending.pop(key, None)


def pending_product_interaction(workspace: str) -> dict[str, Any] | None:
    """读取独立产品动作尚未解决的交互占用。"""
    with maintenance_lock:
        root = workspace_key(workspace)
        return next((dict(value) for (key, _thread), value in _pending.items() if key == root), None)


def clear_preview_runtime_workspace(workspace: str) -> None:
    """应用目录已移走后清理内存占用，避免复用路径继承旧任务。"""
    with maintenance_lock:
        root = workspace_key(workspace)
        _owners.pop(root, None)
        for key in [key for key in _pending if key[0] == root]:
            _pending.pop(key, None)
