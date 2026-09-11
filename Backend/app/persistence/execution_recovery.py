"""Durable Execution Recovery 的独立 SQLite 持久化实现。"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryPoint,
    RecoveryPointKind,
)


RECOVERY_DATABASE_RELATIVE_PATH = Path(
    ".xcodeagent/recovery/execution-recovery.sqlite"
)
RECOVERY_SCHEMA_VERSION = "1"


def execution_recovery_db_path(workspace: str | Path) -> Path:
    """返回指定工作区的独立恢复数据库路径。"""

    return Path(workspace).expanduser().resolve() / RECOVERY_DATABASE_RELATIVE_PATH


def _utc_iso(value: datetime) -> str:
    """将时间统一序列化为带时区的 ISO-8601 文本。"""

    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    """将数据库中的时间文本恢复为带时区的 datetime。"""

    parsed = datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed
    )


def _dedupe_key(point: RecoveryPoint) -> str:
    """按现场事实生成稳定幂等键，不把时间和随机记录 ID 纳入身份。"""

    payload = {
        "runId": point.run_id,
        "checkpointId": point.checkpoint_id,
        "checkpointNs": point.checkpoint_ns,
        "completedNode": point.completed_node,
        "nextNodes": point.next_nodes,
        "kind": point.kind.value,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@asynccontextmanager
async def _connection(workspace: str | Path) -> AsyncIterator[aiosqlite.Connection]:
    """打开短生命周期连接并设置恢复库所需的 SQLite 参数。"""

    path = execution_recovery_db_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path)
    try:
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        yield connection
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise
    finally:
        await connection.close()


async def initialize_execution_recovery_store(workspace: str | Path) -> None:
    """创建恢复库表、索引和当前 schema version。"""

    async with _connection(workspace) as connection:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS recovery_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS execution_records (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                workspace TEXT NOT NULL,
                project_id TEXT,
                execution_kind TEXT NOT NULL,
                workflow_scope TEXT,
                first_node TEXT NOT NULL,
                current_node TEXT,
                status TEXT NOT NULL,
                last_recovery_point_id TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_execution_records_thread
                ON execution_records(thread_id);
            CREATE INDEX IF NOT EXISTS idx_execution_records_status
                ON execution_records(status);
            CREATE INDEX IF NOT EXISTS idx_execution_records_updated
                ON execution_records(updated_at);

            CREATE TABLE IF NOT EXISTS recovery_points (
                recovery_point_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                checkpoint_id TEXT,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                graph_node TEXT,
                completed_node TEXT,
                next_nodes_json TEXT NOT NULL,
                phase TEXT,
                state_status TEXT,
                lifecycle_revision INTEGER,
                workspace_revision TEXT,
                workspace_snapshot_hash TEXT,
                replay_safety TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_recovery_points_run
                ON recovery_points(run_id);
            CREATE INDEX IF NOT EXISTS idx_recovery_points_thread
                ON recovery_points(thread_id);
            CREATE INDEX IF NOT EXISTS idx_recovery_points_checkpoint
                ON recovery_points(checkpoint_id);
            CREATE INDEX IF NOT EXISTS idx_recovery_points_captured
                ON recovery_points(captured_at);
            """
        )
        await connection.execute(
            """
            INSERT INTO recovery_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (RECOVERY_SCHEMA_VERSION,),
        )


async def insert_execution(
    record: DurableExecutionRecord,
) -> DurableExecutionRecord:
    """幂等插入一次执行记录，并保留已存在执行的运行现场。"""

    await initialize_execution_recovery_store(record.workspace)
    async with _connection(record.workspace) as connection:
        await connection.execute(
            """
            INSERT INTO execution_records(
                run_id, thread_id, workspace, project_id, execution_kind,
                workflow_scope, first_node, current_node, status,
                last_recovery_point_id, started_at, updated_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO NOTHING
            """,
            (
                record.run_id,
                record.thread_id,
                record.workspace,
                record.project_id,
                record.execution_kind,
                record.workflow_scope,
                record.first_node,
                record.current_node,
                record.status.value,
                record.last_recovery_point_id,
                _utc_iso(record.started_at),
                _utc_iso(record.updated_at),
                _utc_iso(record.ended_at) if record.ended_at else None,
            ),
        )
        row = await _fetch_execution_row(connection, record.run_id)
        if row is None:
            raise RuntimeError(f"无法读取刚写入的执行记录：{record.run_id}")
        return _execution_from_row(row)


async def update_execution_node(
    *,
    workspace: str | Path,
    run_id: str,
    node_name: str,
) -> None:
    """更新执行当前节点，不改变业务生命周期状态。"""

    await initialize_execution_recovery_store(workspace)
    now = _utc_iso(datetime.now(timezone.utc))
    async with _connection(workspace) as connection:
        await connection.execute(
            """
            UPDATE execution_records
            SET current_node = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (node_name, now, run_id),
        )


async def update_execution_status(
    *,
    workspace: str | Path,
    run_id: str,
    status: DurableExecutionStatus,
    ended: bool = False,
) -> None:
    """更新恢复记录状态，并仅在明确结束时写入 ended_at。"""

    await initialize_execution_recovery_store(workspace)
    now = _utc_iso(datetime.now(timezone.utc))
    async with _connection(workspace) as connection:
        await connection.execute(
            """
            UPDATE execution_records
            SET status = ?, updated_at = ?, ended_at =
                CASE WHEN ? THEN COALESCE(ended_at, ?) ELSE ended_at END
            WHERE run_id = ?
            """,
            (status.value, now, int(ended), now if ended else None, run_id),
        )


async def insert_recovery_point(
    *,
    workspace: str | Path,
    point: RecoveryPoint,
) -> RecoveryPoint:
    """在同一事务中幂等写入现场并更新执行记录的 latest 指针。"""

    await initialize_execution_recovery_store(workspace)
    dedupe_key = _dedupe_key(point)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            INSERT INTO recovery_points(
                recovery_point_id, run_id, thread_id, kind, checkpoint_id,
                checkpoint_ns, graph_node, completed_node, next_nodes_json,
                phase, state_status, lifecycle_revision, workspace_revision,
                workspace_snapshot_hash, replay_safety, dedupe_key, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            (
                point.recovery_point_id,
                point.run_id,
                point.thread_id,
                point.kind.value,
                point.checkpoint_id,
                point.checkpoint_ns,
                point.graph_node,
                point.completed_node,
                json.dumps(point.next_nodes, ensure_ascii=False),
                point.phase,
                point.state_status,
                point.lifecycle_revision,
                point.workspace_revision,
                point.workspace_snapshot_hash,
                point.replay_safety,
                dedupe_key,
                _utc_iso(point.captured_at),
            ),
        )
        inserted = cursor.rowcount == 1
        row = await _fetch_recovery_point_row_by_dedupe(connection, dedupe_key)
        if row is None:
            raise RuntimeError(f"无法读取刚写入的恢复现场：{point.recovery_point_id}")
        persisted = _recovery_point_from_row(row)
        if inserted:
            await connection.execute(
                """
                UPDATE execution_records
                SET last_recovery_point_id = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (persisted.recovery_point_id, _utc_iso(persisted.captured_at), point.run_id),
            )
        return persisted


async def get_execution(
    workspace: str | Path,
    run_id: str,
) -> DurableExecutionRecord | None:
    """读取指定执行记录，缺失时返回 None。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        row = await _fetch_execution_row(connection, run_id)
        return _execution_from_row(row) if row is not None else None


async def get_latest_recovery_point(
    workspace: str | Path,
    run_id: str,
) -> RecoveryPoint | None:
    """按捕获时间读取指定执行的最新恢复现场。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            SELECT recovery_point_id, run_id, thread_id, kind, checkpoint_id,
                   checkpoint_ns, graph_node, completed_node, next_nodes_json,
                   phase, state_status, lifecycle_revision, workspace_revision,
                   workspace_snapshot_hash, replay_safety, dedupe_key, captured_at
            FROM recovery_points
            WHERE run_id = ?
            ORDER BY captured_at DESC, recovery_point_id DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return _recovery_point_from_row(row) if row is not None else None


async def list_recovery_points(
    workspace: str | Path,
    run_id: str,
) -> list[RecoveryPoint]:
    """按捕获时间升序读取一次执行的完整现场历史。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            SELECT recovery_point_id, run_id, thread_id, kind, checkpoint_id,
                   checkpoint_ns, graph_node, completed_node, next_nodes_json,
                   phase, state_status, lifecycle_revision, workspace_revision,
                   workspace_snapshot_hash, replay_safety, dedupe_key, captured_at
            FROM recovery_points
            WHERE run_id = ?
            ORDER BY captured_at ASC, recovery_point_id ASC
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        return [_recovery_point_from_row(row) for row in rows]


async def _fetch_execution_row(
    connection: aiosqlite.Connection,
    run_id: str,
) -> tuple[object, ...] | None:
    """读取执行表的一行原始数据。"""

    cursor = await connection.execute(
        """
        SELECT run_id, thread_id, workspace, project_id, execution_kind,
               workflow_scope, first_node, current_node, status,
               last_recovery_point_id, started_at, updated_at, ended_at
        FROM execution_records
        WHERE run_id = ?
        """,
        (run_id,),
    )
    return await cursor.fetchone()


async def _fetch_recovery_point_row_by_dedupe(
    connection: aiosqlite.Connection,
    dedupe_key: str,
) -> tuple[object, ...] | None:
    """按幂等键读取持久化后的现场行。"""

    cursor = await connection.execute(
        """
        SELECT recovery_point_id, run_id, thread_id, kind, checkpoint_id,
               checkpoint_ns, graph_node, completed_node, next_nodes_json,
               phase, state_status, lifecycle_revision, workspace_revision,
               workspace_snapshot_hash, replay_safety, dedupe_key, captured_at
        FROM recovery_points
        WHERE dedupe_key = ?
        """,
        (dedupe_key,),
    )
    return await cursor.fetchone()


def _execution_from_row(row: tuple[object, ...]) -> DurableExecutionRecord:
    """将执行表行恢复为严格的领域模型。"""

    return DurableExecutionRecord(
        run_id=str(row[0]),
        thread_id=str(row[1]),
        workspace=str(row[2]),
        project_id=str(row[3]) if row[3] is not None else None,
        execution_kind=str(row[4]),
        workflow_scope=str(row[5]) if row[5] is not None else None,
        first_node=str(row[6]),
        current_node=str(row[7]) if row[7] is not None else None,
        status=DurableExecutionStatus(str(row[8])),
        last_recovery_point_id=(
            str(row[9]) if row[9] is not None else None
        ),
        started_at=_parse_datetime(str(row[10])),
        updated_at=_parse_datetime(str(row[11])),
        ended_at=_parse_datetime(str(row[12])) if row[12] is not None else None,
    )


def _recovery_point_from_row(row: tuple[object, ...]) -> RecoveryPoint:
    """将恢复现场表行恢复为严格的领域模型。"""

    next_nodes = json.loads(str(row[8]))
    if not isinstance(next_nodes, list):
        raise ValueError("recovery_points.next_nodes_json 必须是数组。")
    return RecoveryPoint(
        recovery_point_id=str(row[0]),
        run_id=str(row[1]),
        thread_id=str(row[2]),
        kind=RecoveryPointKind(str(row[3])),
        checkpoint_id=str(row[4]) if row[4] is not None else None,
        checkpoint_ns=str(row[5] or ""),
        graph_node=str(row[6]) if row[6] is not None else None,
        completed_node=str(row[7]) if row[7] is not None else None,
        next_nodes=[str(value) for value in next_nodes],
        phase=str(row[9]) if row[9] is not None else None,
        state_status=str(row[10]) if row[10] is not None else None,
        lifecycle_revision=int(row[11]) if row[11] is not None else None,
        workspace_revision=str(row[12]) if row[12] is not None else None,
        workspace_snapshot_hash=str(row[13]) if row[13] is not None else None,
        replay_safety=str(row[14]),
        captured_at=_parse_datetime(str(row[16])),
    )
