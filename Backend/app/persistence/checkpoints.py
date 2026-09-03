from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings
from app.workspace.spec_documents import workflow_artifact_root

_CHECKPOINT_EXIT_STACK = AsyncExitStack()
_CHECKPOINT_SAVERS: dict[str, AsyncSqliteSaver] = {}
_CHECKPOINT_IDENTITIES: dict[str, tuple[int, int]] = {}
_CHECKPOINT_LOCK = asyncio.Lock()


def _checkpoint_file_identity(path: Path) -> tuple[int, int] | None:
    """读取 checkpoint 文件的设备与 inode，用于识别同路径文件被移动或替换。"""

    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino


def workflow_checkpoint_db_path(
    *,
    workspace: str | None = None,
    project_id: str | None = None,
) -> Path:
    """Resolve the SQLite checkpoint database for a workflow workspace."""

    settings = Settings.from_env()
    if settings.checkpoint_db_path:
        return Path(settings.checkpoint_db_path).expanduser()
    return workflow_artifact_root(
        {"workspace": workspace, "project_id": project_id}
    ) / "checkpoints" / "checkpoints.sqlite"


async def workflow_checkpointer(
    *,
    workspace: str | None = None,
    project_id: str | None = None,
) -> AsyncSqliteSaver:
    """Return a SQLite checkpointer scoped to the workflow workspace."""

    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    async with _CHECKPOINT_LOCK:
        saver = _CHECKPOINT_SAVERS.get(cache_key)
        current_identity = _checkpoint_file_identity(db_path)
        if saver is not None and _CHECKPOINT_IDENTITIES.get(cache_key) == current_identity:
            return saver
        if saver is not None:
            # 项目删除会把整个 .xcodeagent 移入废纸篓；旧连接仍可写旧 inode，必须主动关闭。
            await saver.conn.close()
            _CHECKPOINT_SAVERS.pop(cache_key, None)
            _CHECKPOINT_IDENTITIES.pop(cache_key, None)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        saver = await _CHECKPOINT_EXIT_STACK.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(db_path))
        )
        await saver.setup()
        _CHECKPOINT_SAVERS[cache_key] = saver
        identity = _checkpoint_file_identity(db_path)
        if identity is None:
            raise RuntimeError(f"Checkpoint 数据库创建失败：{db_path}")
        _CHECKPOINT_IDENTITIES[cache_key] = identity
        return saver


async def cleanup_workflow_checkpoints(
    *,
    workspace: str | None = None,
    project_id: str | None = None,
    retention_days: int | None = None,
) -> int:
    """Remove old completed-thread checkpoints while preserving recovery points."""

    saver = await workflow_checkpointer(workspace=workspace, project_id=project_id)
    retention = (
        Settings.from_env().checkpoint_retention_days
        if retention_days is None
        else retention_days
    )
    cutoff = datetime.now(UTC) - timedelta(days=max(retention, 1))
    latest_by_thread: dict[tuple[str, str], Any] = {}
    checkpoints_by_thread: dict[tuple[str, str], list[Any]] = {}

    async for item in saver.alist(None):
        configurable = item.config.get("configurable", {})
        thread_key = (
            str(configurable.get("thread_id") or ""),
            str(configurable.get("checkpoint_ns") or ""),
        )
        if not thread_key[0]:
            continue
        checkpoints_by_thread.setdefault(thread_key, []).append(item)
        latest_by_thread.setdefault(thread_key, item)

    deletions: list[tuple[str, str, str]] = []
    for thread_key, items in checkpoints_by_thread.items():
        latest = latest_by_thread[thread_key]
        if _checkpoint_requires_user_input(latest):
            continue
        for item in items:
            configurable = item.config.get("configurable", {})
            checkpoint_id = str(configurable.get("checkpoint_id") or "")
            if item is latest or not checkpoint_id:
                continue
            if _checkpoint_timestamp(item) < cutoff:
                deletions.append((thread_key[0], thread_key[1], checkpoint_id))

    if not deletions:
        return 0

    async with saver.lock:
        await saver.conn.executemany(
            """
            DELETE FROM writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            """,
            deletions,
        )
        await saver.conn.executemany(
            """
            DELETE FROM checkpoints
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            """,
            deletions,
        )
        await saver.conn.commit()
    return len(deletions)


async def close_workflow_checkpointer() -> None:
    """Close SQLite connections held by process-wide checkpointers."""

    await _CHECKPOINT_EXIT_STACK.aclose()
    _CHECKPOINT_SAVERS.clear()
    _CHECKPOINT_IDENTITIES.clear()


async def delete_workflow_checkpoints_for_workspace(
    *,
    workspace: str,
    project_id: str | None = None,
    thread_ids: set[str] | None = None,
) -> dict[str, Any]:
    """按工作区正文、metadata 或已知线程删除目标应用的全部 checkpoint。"""

    normalized_workspace = os.path.normcase(
        str(Path(workspace).expanduser().resolve(strict=False))
    )
    saver = await workflow_checkpointer(workspace=workspace, project_id=project_id)
    thread_keys: set[tuple[str, str]] = set()
    async with saver.lock:
        cursor = await saver.conn.execute(
            "SELECT thread_id, checkpoint_ns, type, checkpoint, metadata FROM checkpoints"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        known_thread_ids = {str(thread_id) for thread_id in (thread_ids or set())}
        for thread_id, checkpoint_ns, type_tag, checkpoint_blob, raw_metadata in rows:
            metadata = _checkpoint_metadata(raw_metadata)
            checkpoint = _deserialize_checkpoint(saver, type_tag, checkpoint_blob)
            channel_values = checkpoint.get("channel_values")
            channel_values = channel_values if isinstance(channel_values, dict) else {}
            candidate_workspace = str(
                metadata.get("workspace")
                or channel_values.get("workspace")
                or ""
            ).strip()
            candidate = (
                os.path.normcase(
                    str(Path(candidate_workspace).expanduser().resolve(strict=False))
                )
                if candidate_workspace
                else ""
            )
            if candidate == normalized_workspace or str(thread_id) in known_thread_ids:
                thread_keys.add((str(thread_id), str(checkpoint_ns or "")))
        if thread_keys:
            await saver.conn.executemany(
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ?",
                sorted(thread_keys),
            )
            await saver.conn.executemany(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ?",
                sorted(thread_keys),
            )
            await saver.conn.commit()
    return {
        "databasePath": str(
            workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
        ),
        "deletedThreadCount": len(thread_keys),
    }


async def close_workflow_checkpointer_for_workspace(
    *,
    workspace: str,
    project_id: str | None = None,
) -> bool:
    """关闭工作区本地 checkpoint 连接；共享数据库只删行而不影响其他应用。"""

    settings = Settings.from_env()
    if settings.checkpoint_db_path:
        return False
    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    async with _CHECKPOINT_LOCK:
        saver = _CHECKPOINT_SAVERS.pop(cache_key, None)
        _CHECKPOINT_IDENTITIES.pop(cache_key, None)
        if saver is None:
            return False
        await saver.conn.close()
        return True


def _checkpoint_metadata(raw_metadata: Any) -> dict[str, Any]:
    """把 SQLite 中的 JSON metadata 安全转换为字典。"""

    if isinstance(raw_metadata, bytes):
        try:
            raw_metadata = raw_metadata.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not isinstance(raw_metadata, str):
        return {}
    try:
        value = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _deserialize_checkpoint(
    saver: AsyncSqliteSaver,
    type_tag: Any,
    checkpoint_blob: Any,
) -> dict[str, Any]:
    """安全反序列化 checkpoint 正文，仅用于识别其 workspace channel。"""

    if not isinstance(type_tag, str) or not isinstance(
        checkpoint_blob,
        (bytes, bytearray, memoryview),
    ):
        return {}
    try:
        value = saver.serde.loads_typed((type_tag, bytes(checkpoint_blob)))
    except Exception:  # noqa: BLE001 - 损坏行不能阻断其余应用级清理
        return {}
    return value if isinstance(value, dict) else {}


def _checkpoint_timestamp(item: Any) -> datetime:
    ts = item.checkpoint.get("ts") if isinstance(item.checkpoint, dict) else None
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _checkpoint_requires_user_input(item: Any) -> bool:
    values = (
        item.checkpoint.get("channel_values", {})
        if isinstance(item.checkpoint, dict)
        else {}
    )
    if not isinstance(values, dict):
        return False
    clarification = values.get("clarification")
    return values.get("status") == "requires_user_input" or (
        isinstance(clarification, dict)
        and clarification.get("status") == "requires_user_input"
    )
