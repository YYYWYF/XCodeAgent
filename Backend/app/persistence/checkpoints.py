from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings
from app.workspace.spec_documents import workflow_artifact_root

_CHECKPOINT_EXIT_STACK = AsyncExitStack()
_CHECKPOINT_SAVERS: dict[str, AsyncSqliteSaver] = {}


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
    saver = _CHECKPOINT_SAVERS.get(cache_key)
    if saver is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        saver = await _CHECKPOINT_EXIT_STACK.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(db_path))
        )
        await saver.setup()
        _CHECKPOINT_SAVERS[cache_key] = saver
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
