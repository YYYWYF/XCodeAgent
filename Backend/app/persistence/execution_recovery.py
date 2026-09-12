"""Durable Execution Recovery 的独立 SQLite 持久化实现。"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionRunConflictError,
    DurableExecutionStatus,
    ExecutionLease,
    ExecutionLeaseStatus,
    RecoveryAttempt,
    RecoveryAttemptAlreadyClaimedError,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryPlan,
    RecoveryStrategy,
)


RECOVERY_DATABASE_RELATIVE_PATH = Path(
    ".xcodeagent/recovery/execution-recovery.sqlite"
)
RECOVERY_SCHEMA_VERSION = "3"


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


@asynccontextmanager
async def _connection_after_initialize(
    workspace: str | Path,
) -> AsyncIterator[aiosqlite.Connection]:
    """初始化当前恢复库后返回一个可执行普通事务的短连接。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        yield connection


async def initialize_execution_recovery_store(workspace: str | Path) -> None:
    """创建恢复库表，并把现有 v2 store 原子补齐到 v3。"""

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

            CREATE TABLE IF NOT EXISTS execution_leases (
                run_id TEXT PRIMARY KEY,
                owner_backend_instance_id TEXT NOT NULL,
                owner_pid INTEGER NOT NULL,
                status TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT,
                FOREIGN KEY(run_id)
                    REFERENCES execution_records(run_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_execution_leases_status
                ON execution_leases(status);
            CREATE INDEX IF NOT EXISTS idx_execution_leases_expires
                ON execution_leases(expires_at);
            CREATE INDEX IF NOT EXISTS idx_execution_leases_owner
                ON execution_leases(owner_backend_instance_id);

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

            CREATE TABLE IF NOT EXISTS recovery_attempts (
                new_run_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                source_recovery_point_id TEXT NOT NULL,
                source_checkpoint_id TEXT NOT NULL,
                source_checkpoint_ns TEXT NOT NULL DEFAULT '',
                replay_checkpoint_id TEXT,
                replay_checkpoint_ns TEXT NOT NULL DEFAULT '',
                strategy TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                handed_off_at TEXT,
                started_at TEXT,
                failed_at TEXT,
                failure_code TEXT,
                FOREIGN KEY(source_run_id)
                    REFERENCES execution_records(run_id),
                FOREIGN KEY(new_run_id)
                    REFERENCES execution_records(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_recovery_attempts_source
                ON recovery_attempts(source_run_id);
            CREATE INDEX IF NOT EXISTS idx_recovery_attempts_status
                ON recovery_attempts(status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_attempts_active_source
                ON recovery_attempts(source_run_id)
                WHERE status IN ('preparing', 'handed_off', 'finalizing', 'started');
            """
        )
        index_cursor = await connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_recovery_attempts_active_source'
            """
        )
        index_row = await index_cursor.fetchone()
        index_sql = str(index_row[0] or "").lower() if index_row else ""
        if "finalizing" not in index_sql:
            await connection.execute(
                "DROP INDEX IF EXISTS idx_recovery_attempts_active_source"
            )
            await connection.execute(
                """
                CREATE UNIQUE INDEX idx_recovery_attempts_active_source
                ON recovery_attempts(source_run_id)
                WHERE status IN ('preparing', 'handed_off', 'finalizing', 'started')
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


async def claim_native_recovery_attempt(
    *,
    source: DurableExecutionRecord,
    plan: RecoveryPlan,
    new_run_id: str,
    owner_backend_instance_id: str,
    owner_pid: int,
    lease_ttl_seconds: float,
    created_at: datetime | None = None,
) -> tuple[DurableExecutionRecord, ExecutionLease, RecoveryAttempt]:
    """在一个 SQLite 写事务中 claim source、创建 child Execution、Lease 和 Attempt。"""

    from app.domain.execution_recovery import RecoveryExecutionError

    if plan.source_run_id != source.run_id:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_MISMATCH",
            "RecoveryPlan 与 source execution 不属于同一条运行记录。",
        )
    if plan.decision.value != "ready_native" or plan.strategy is not RecoveryStrategy.NATIVE_CHECKPOINT:
        raise RecoveryExecutionError(
            "RECOVERY_NOT_READY_NATIVE",
            "当前 RecoveryPlan 未被 Native Recovery policy 明确允许。",
        )
    if not plan.recovery_point_id or not plan.checkpoint_id or len(plan.next_nodes) != 1:
        raise RecoveryExecutionError(
            "RECOVERY_PLAN_INCOMPLETE",
            "Native RecoveryPlan 缺少唯一 checkpoint 或 next node。",
        )
    if source.status is not DurableExecutionStatus.INTERRUPTED:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_NOT_INTERRUPTED",
            "source execution 当前状态不允许创建 Native Recovery attempt。",
        )
    now = created_at or datetime.now(timezone.utc)
    lease = ExecutionLease(
        run_id=new_run_id,
        owner_backend_instance_id=owner_backend_instance_id,
        owner_pid=owner_pid,
        status=ExecutionLeaseStatus.ACTIVE,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=lease_ttl_seconds),
    )
    record = DurableExecutionRecord(
        run_id=new_run_id,
        thread_id=source.thread_id,
        workspace=source.workspace,
        project_id=source.project_id,
        execution_kind=source.execution_kind,
        workflow_scope=source.workflow_scope,
        first_node=plan.next_nodes[0],
        current_node=plan.next_nodes[0],
        status=DurableExecutionStatus.RUNNING,
        started_at=now,
        updated_at=now,
    )
    attempt = RecoveryAttempt(
        source_run_id=source.run_id,
        new_run_id=new_run_id,
        thread_id=source.thread_id,
        source_recovery_point_id=plan.recovery_point_id,
        source_checkpoint_id=plan.checkpoint_id,
        source_checkpoint_ns=plan.checkpoint_ns,
        strategy=plan.strategy,
        status=RecoveryAttemptStatus.PREPARING,
        created_at=now,
    )
    await initialize_execution_recovery_store(source.workspace)
    async with _connection(source.workspace) as connection:
        await connection.execute("BEGIN IMMEDIATE")
        source_row = await _fetch_execution_row(connection, source.run_id)
        if source_row is None:
            raise RecoveryExecutionError(
                "SOURCE_EXECUTION_NOT_FOUND",
                "source execution 不存在。",
            )
        if str(source_row[8]) != source.status.value:
            raise RecoveryExecutionError(
                "RECOVERY_SOURCE_CHANGED",
                "source execution 在 claim 前已经发生状态变化。",
            )
        existing_row = await _fetch_execution_row(connection, new_run_id)
        if existing_row is not None:
            raise _run_id_conflict_from_row(existing_row)
        try:
            await connection.execute(
                """
                INSERT INTO execution_records(
                    run_id, thread_id, workspace, project_id, execution_kind,
                    workflow_scope, first_node, current_node, status,
                    last_recovery_point_id, started_at, updated_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    None,
                ),
            )
            await connection.execute(
                """
                INSERT INTO execution_leases(
                    run_id, owner_backend_instance_id, owner_pid, status,
                    acquired_at, heartbeat_at, expires_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease.run_id,
                    lease.owner_backend_instance_id,
                    lease.owner_pid,
                    lease.status.value,
                    _utc_iso(lease.acquired_at),
                    _utc_iso(lease.heartbeat_at),
                    _utc_iso(lease.expires_at),
                    None,
                ),
            )
            await connection.execute(
                """
                INSERT INTO recovery_attempts(
                    new_run_id, source_run_id, thread_id,
                    source_recovery_point_id, source_checkpoint_id,
                    source_checkpoint_ns, replay_checkpoint_id,
                    replay_checkpoint_ns, strategy, status, created_at,
                    handed_off_at, started_at, failed_at, failure_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.new_run_id,
                    attempt.source_run_id,
                    attempt.thread_id,
                    attempt.source_recovery_point_id,
                    attempt.source_checkpoint_id,
                    attempt.source_checkpoint_ns,
                    None,
                    attempt.replay_checkpoint_ns,
                    attempt.strategy.value,
                    attempt.status.value,
                    _utc_iso(attempt.created_at),
                    None,
                    None,
                    None,
                    None,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            active = await _fetch_active_attempt_row(connection, source.run_id)
            if active is not None:
                raise RecoveryAttemptAlreadyClaimedError(
                    source_run_id=source.run_id,
                    active_run_id=str(active[0]),
                ) from None
            existing_row = await _fetch_execution_row(connection, new_run_id)
            if existing_row is not None:
                raise _run_id_conflict_from_row(existing_row) from None
            raise exc
        return record, lease, attempt


async def get_recovery_attempt(
    workspace: str | Path,
    new_run_id: str,
) -> RecoveryAttempt | None:
    """读取一次恢复 child 对应的 lineage 记录。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        row = await _fetch_recovery_attempt_row(connection, new_run_id)
        return _recovery_attempt_from_row(row) if row is not None else None


async def list_recovery_attempts_from_source(
    workspace: str | Path,
    source_run_id: str,
) -> list[RecoveryAttempt]:
    """按创建时间读取 source execution 的全部恢复分支。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            SELECT new_run_id, source_run_id, thread_id,
                   source_recovery_point_id, source_checkpoint_id,
                   source_checkpoint_ns, replay_checkpoint_id,
                   replay_checkpoint_ns, strategy, status, created_at,
                   handed_off_at, started_at, failed_at, failure_code
            FROM recovery_attempts
            WHERE source_run_id = ?
            ORDER BY created_at ASC, new_run_id ASC
            """,
            (source_run_id,),
        )
        rows = await cursor.fetchall()
    return [_recovery_attempt_from_row(row) for row in rows]


async def update_recovery_attempt(
    *,
    workspace: str | Path,
    new_run_id: str,
    status: RecoveryAttemptStatus,
    replay_checkpoint_id: str | None = None,
    replay_checkpoint_ns: str | None = None,
    failure_code: str | None = None,
    updated_at: datetime | None = None,
) -> RecoveryAttempt | None:
    """以持久化状态机约束更新 RecoveryAttempt，并返回最新 lineage。"""

    now = updated_at or datetime.now(timezone.utc)
    async with _connection_after_initialize(workspace) as connection:
        await connection.execute("BEGIN IMMEDIATE")
        current = await _fetch_recovery_attempt_row(connection, new_run_id)
        if current is None:
            return None
        current_attempt = _recovery_attempt_from_row(current)
        if current_attempt.status is status:
            return current_attempt
        allowed_transitions = {
            RecoveryAttemptStatus.PREPARING: {
                RecoveryAttemptStatus.HANDED_OFF,
                RecoveryAttemptStatus.FAILED_PRESTART,
            },
            RecoveryAttemptStatus.HANDED_OFF: {
                RecoveryAttemptStatus.FINALIZING,
            },
            RecoveryAttemptStatus.FINALIZING: {
                RecoveryAttemptStatus.STARTED,
                RecoveryAttemptStatus.FINALIZATION_FAILED,
            },
            RecoveryAttemptStatus.STARTED: set(),
            RecoveryAttemptStatus.FAILED_PRESTART: set(),
            RecoveryAttemptStatus.FINALIZATION_FAILED: set(),
        }
        if status not in allowed_transitions[current_attempt.status]:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_INVALID_TRANSITION",
                "RecoveryAttempt 状态不能倒退或跳过 finalization claim。",
            )
        updates = {
            "status": status.value,
            "replay_checkpoint_id": (
                replay_checkpoint_id
                if replay_checkpoint_id is not None
                else current_attempt.replay_checkpoint_id
            ),
            "replay_checkpoint_ns": (
                replay_checkpoint_ns
                if replay_checkpoint_ns is not None
                else current_attempt.replay_checkpoint_ns
            ),
            "handed_off_at": (
                _utc_iso(now)
                if status
                in {
                    RecoveryAttemptStatus.HANDED_OFF,
                    RecoveryAttemptStatus.FINALIZING,
                    RecoveryAttemptStatus.STARTED,
                    RecoveryAttemptStatus.FINALIZATION_FAILED,
                }
                else current_attempt.handed_off_at
            ),
            "started_at": (
                _utc_iso(now)
                if status is RecoveryAttemptStatus.STARTED
                else current_attempt.started_at
            ),
            "failed_at": (
                _utc_iso(now)
                if status
                in {
                    RecoveryAttemptStatus.FAILED_PRESTART,
                    RecoveryAttemptStatus.FINALIZATION_FAILED,
                }
                else current_attempt.failed_at
            ),
            "failure_code": failure_code or current_attempt.failure_code,
        }
        await connection.execute(
            """
            UPDATE recovery_attempts
            SET status = ?, replay_checkpoint_id = ?, replay_checkpoint_ns = ?,
                handed_off_at = ?, started_at = ?, failed_at = ?, failure_code = ?
            WHERE new_run_id = ?
            """,
            (
                updates["status"], updates["replay_checkpoint_id"],
                updates["replay_checkpoint_ns"], updates["handed_off_at"],
                updates["started_at"], updates["failed_at"],
                updates["failure_code"], new_run_id,
            ),
        )
        row = await _fetch_recovery_attempt_row(connection, new_run_id)
        return _recovery_attempt_from_row(row) if row is not None else None


async def claim_recovery_finalization(
    *,
    workspace: str | Path,
    new_run_id: str,
    new_owner_backend_instance_id: str,
    new_owner_pid: int,
    lease_ttl_seconds: float,
    claim_at: datetime | None = None,
) -> tuple[RecoveryAttempt, ExecutionLease]:
    """在同一 SQLite 写事务中独占 finalization 权并接管 child lease。"""

    moment = claim_at or datetime.now(timezone.utc)
    expires_at = moment + timedelta(seconds=lease_ttl_seconds)
    async with _connection_after_initialize(workspace) as connection:
        await connection.execute("BEGIN IMMEDIATE")
        attempt_row = await _fetch_recovery_attempt_row(connection, new_run_id)
        if attempt_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_NOT_FOUND",
                "RecoveryAttempt 不存在。",
            )
        attempt = _recovery_attempt_from_row(attempt_row)
        if attempt.status is RecoveryAttemptStatus.STARTED:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_ALREADY_STARTED",
                "RecoveryAttempt 已经进入 Graph replay，不能再次 finalization。",
            )
        if attempt.status is RecoveryAttemptStatus.FINALIZATION_FAILED:
            raise RecoveryExecutionError(
                "RECOVERY_FINALIZATION_FAILED",
                "RecoveryAttempt 的 finalization 已失败，不能回到 source 重放。",
            )
        if attempt.status not in {
            RecoveryAttemptStatus.HANDED_OFF,
            RecoveryAttemptStatus.FINALIZING,
        }:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_STATUS_MISMATCH",
                "只有 HANDED_OFF 或可接管的 FINALIZING attempt 才能 finalization。",
            )

        execution_row = await _fetch_execution_row(connection, new_run_id)
        if execution_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_EXECUTION_NOT_FOUND",
                "finalization 的 child execution 不存在。",
            )
        if str(execution_row[8]) != DurableExecutionStatus.RUNNING.value:
            raise RecoveryExecutionError(
                "RECOVERY_EXECUTION_NOT_RUNNING",
                "只有 RUNNING child execution 才能 finalization。",
            )
        lease_row = await _fetch_execution_lease_row(connection, new_run_id)
        if lease_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_LEASE_MISSING",
                "finalization 的 child lease 不存在。",
            )
        lease = _execution_lease_from_row(lease_row)
        if (
            attempt.status is RecoveryAttemptStatus.FINALIZING
            and lease.status is ExecutionLeaseStatus.ACTIVE
            and lease.expires_at > moment
        ):
            raise RecoveryExecutionError(
                "RECOVERY_FINALIZATION_ALREADY_CLAIMED",
                "RecoveryAttempt 的 finalization 已由其他 Backend 独占。",
            )

        if attempt.status is RecoveryAttemptStatus.HANDED_OFF:
            status_cursor = await connection.execute(
                """
                UPDATE recovery_attempts
                SET status = ?, handed_off_at = COALESCE(handed_off_at, ?)
                WHERE new_run_id = ? AND status = ?
                """,
                (
                    RecoveryAttemptStatus.FINALIZING.value,
                    _utc_iso(moment),
                    new_run_id,
                    RecoveryAttemptStatus.HANDED_OFF.value,
                ),
            )
            if status_cursor.rowcount != 1:
                raise RecoveryExecutionError(
                    "RECOVERY_FINALIZATION_ALREADY_CLAIMED",
                    "RecoveryAttempt 的 finalization claim 已被其他请求获得。",
                )
        cursor = await connection.execute(
            """
            UPDATE execution_leases
            SET owner_backend_instance_id = ?, owner_pid = ?, status = ?,
                heartbeat_at = ?, expires_at = ?, released_at = NULL
            WHERE run_id = ?
            """,
            (
                new_owner_backend_instance_id,
                new_owner_pid,
                ExecutionLeaseStatus.ACTIVE.value,
                _utc_iso(moment),
                _utc_iso(expires_at),
                new_run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RecoveryExecutionError(
                "RECOVERY_LEASE_MISSING",
                "finalization 的 child lease 无法接管。",
            )
        updated_attempt_row = await _fetch_recovery_attempt_row(connection, new_run_id)
        updated_lease_row = await _fetch_execution_lease_row(connection, new_run_id)
        if updated_attempt_row is None or updated_lease_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_FINALIZATION_CLAIM_FAILED",
                "finalization claim 写入后无法读取完整 owner。",
            )
        return (
            _recovery_attempt_from_row(updated_attempt_row),
            _execution_lease_from_row(updated_lease_row),
        )


async def fail_recovery_attempt_prestart(
    *,
    workspace: str | Path,
    new_run_id: str,
    failure_code: str,
    failed_at: datetime | None = None,
) -> RecoveryAttempt | None:
    """把尚未 handoff 的 child 收口为 FAILED_PRESTART 并释放其 lease。"""

    attempt = await get_recovery_attempt(workspace, new_run_id)
    if attempt is None:
        return None
    if attempt.status is not RecoveryAttemptStatus.PREPARING:
        return attempt
    moment = failed_at or datetime.now(timezone.utc)
    execution = await get_execution(workspace, new_run_id)
    lease = await get_execution_lease(workspace, new_run_id)
    if execution is not None and lease is not None:
        await finish_execution_and_release_lease(
            workspace=workspace,
            run_id=new_run_id,
            status=DurableExecutionStatus.FAILED,
            owner_backend_instance_id=lease.owner_backend_instance_id,
            ended_at=moment,
        )
    return await update_recovery_attempt(
        workspace=workspace,
        new_run_id=new_run_id,
        status=RecoveryAttemptStatus.FAILED_PRESTART,
        failure_code=failure_code,
        updated_at=moment,
    )


async def insert_execution_with_lease(
    *,
    record: DurableExecutionRecord,
    lease: ExecutionLease,
) -> DurableExecutionRecord:
    """在一个 SQLite 事务中原子创建 ExecutionRecord 和 ACTIVE lease。"""

    if record.run_id != lease.run_id:
        raise ValueError("ExecutionRecord 与 ExecutionLease 的 runId 必须一致。")
    if record.status is not DurableExecutionStatus.RUNNING:
        raise ValueError("只能为 RUNNING Execution 创建 lease。")
    if lease.status is not ExecutionLeaseStatus.ACTIVE:
        raise ValueError("新建 Execution 的 lease 必须为 ACTIVE。")
    await initialize_execution_recovery_store(record.workspace)
    async with _connection(record.workspace) as connection:
        # 用写事务把冲突读取和两张表的创建锁在一起，避免 preflight 后的并发窗口。
        await connection.execute("BEGIN IMMEDIATE")
        existing_row = await _fetch_execution_row(connection, record.run_id)
        if existing_row is not None:
            raise _run_id_conflict_from_row(existing_row)
        try:
            await connection.execute(
                """
                INSERT INTO execution_records(
                    run_id, thread_id, workspace, project_id, execution_kind,
                    workflow_scope, first_node, current_node, status,
                    last_recovery_point_id, started_at, updated_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        except aiosqlite.IntegrityError:
            # 处理其他连接已经先提交的同一 runId，并把冲突统一提升为结构化异常。
            existing_row = await _fetch_execution_row(connection, record.run_id)
            if existing_row is not None:
                raise _run_id_conflict_from_row(existing_row) from None
            raise
        await connection.execute(
            """
            INSERT INTO execution_leases(
                run_id, owner_backend_instance_id, owner_pid, status,
                acquired_at, heartbeat_at, expires_at, released_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lease.run_id,
                lease.owner_backend_instance_id,
                lease.owner_pid,
                lease.status.value,
                _utc_iso(lease.acquired_at),
                _utc_iso(lease.heartbeat_at),
                _utc_iso(lease.expires_at),
                _utc_iso(lease.released_at) if lease.released_at else None,
            ),
        )
        row = await _fetch_execution_row(connection, record.run_id)
        if row is None:
            raise RuntimeError(f"无法读取刚写入的执行记录：{record.run_id}")
        return _execution_from_row(row)


def _run_id_conflict_from_row(
    row: tuple[object, ...],
) -> DurableExecutionRunConflictError:
    """把已存在的 execution 行转换为稳定的 runId 冲突异常。"""

    return DurableExecutionRunConflictError(
        run_id=str(row[0]),
        existing_status=str(row[8]),
        existing_thread_id=str(row[1]),
    )


async def get_execution_lease(
    workspace: str | Path,
    run_id: str,
) -> ExecutionLease | None:
    """读取指定执行的持久化租约。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        row = await _fetch_execution_lease_row(connection, run_id)
        return _execution_lease_from_row(row) if row is not None else None


async def takeover_pre_runtime_recovery_lease(
    *,
    workspace: str | Path,
    new_run_id: str,
    expected_attempt_status: set[RecoveryAttemptStatus],
    new_owner_backend_instance_id: str,
    new_owner_pid: int,
    lease_ttl_seconds: float,
    takeover_at: datetime | None = None,
) -> ExecutionLease:
    """在 Graph 启动前原子接管 PREPARING/HANDED_OFF child 的执行租约。"""

    moment = takeover_at or datetime.now(timezone.utc)
    expires_at = moment + timedelta(seconds=lease_ttl_seconds)
    expected_values = {status.value for status in expected_attempt_status}
    async with _connection_after_initialize(workspace) as connection:
        await connection.execute("BEGIN IMMEDIATE")
        attempt_row = await _fetch_recovery_attempt_row(connection, new_run_id)
        if attempt_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_NOT_FOUND",
                "pre-runtime recovery attempt 不存在。",
            )
        attempt_status = RecoveryAttemptStatus(str(attempt_row[9]))
        if attempt_status is RecoveryAttemptStatus.STARTED:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_ALREADY_STARTED",
                "RecoveryAttempt 已经进入 Graph replay，不能进行 pre-runtime takeover。",
            )
        if attempt_status.value not in expected_values:
            raise RecoveryExecutionError(
                "RECOVERY_ATTEMPT_STATUS_MISMATCH",
                "RecoveryAttempt 当前状态不允许进行 pre-runtime takeover。",
            )
        execution_row = await _fetch_execution_row(connection, new_run_id)
        if execution_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_EXECUTION_NOT_FOUND",
                "pre-runtime recovery 的 child execution 不存在。",
            )
        if str(execution_row[8]) != DurableExecutionStatus.RUNNING.value:
            raise RecoveryExecutionError(
                "RECOVERY_EXECUTION_NOT_RUNNING",
                "只有仍处于 RUNNING 的 pre-runtime child 才能被接管。",
            )
        lease_row = await _fetch_execution_lease_row(connection, new_run_id)
        if lease_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_LEASE_MISSING",
                "pre-runtime recovery 缺少 child lease。",
            )
        cursor = await connection.execute(
            """
            UPDATE execution_leases
            SET owner_backend_instance_id = ?, owner_pid = ?, status = ?,
                heartbeat_at = ?, expires_at = ?, released_at = NULL
            WHERE run_id = ?
            """,
            (
                new_owner_backend_instance_id,
                new_owner_pid,
                ExecutionLeaseStatus.ACTIVE.value,
                _utc_iso(moment),
                _utc_iso(expires_at),
                new_run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RecoveryExecutionError(
                "RECOVERY_LEASE_MISSING",
                "pre-runtime recovery 的 child lease 无法接管。",
            )
        updated_lease_row = await _fetch_execution_lease_row(connection, new_run_id)
        if updated_lease_row is None:
            raise RecoveryExecutionError(
                "RECOVERY_LEASE_MISSING",
                "pre-runtime recovery 的 child lease 无法读取。",
            )
        return _execution_lease_from_row(updated_lease_row)


async def renew_execution_lease(
    *,
    workspace: str | Path,
    run_id: str,
    owner_backend_instance_id: str,
    heartbeat_at: datetime,
    expires_at: datetime,
) -> bool:
    """仅为仍处于 RUNNING/ACTIVE 且属于当前 Backend 的执行续租。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            UPDATE execution_leases
            SET heartbeat_at = ?, expires_at = ?
            WHERE run_id = ?
              AND owner_backend_instance_id = ?
              AND status = ?
              AND EXISTS (
                  SELECT 1 FROM execution_records
                  WHERE execution_records.run_id = execution_leases.run_id
                    AND execution_records.status = ?
              )
            """,
            (
                _utc_iso(heartbeat_at),
                _utc_iso(expires_at),
                run_id,
                owner_backend_instance_id,
                ExecutionLeaseStatus.ACTIVE.value,
                DurableExecutionStatus.RUNNING.value,
            ),
        )
        return cursor.rowcount == 1


async def finish_execution_and_release_lease(
    *,
    workspace: str | Path,
    run_id: str,
    status: DurableExecutionStatus,
    owner_backend_instance_id: str,
    ended_at: datetime,
) -> bool:
    """原子写入终态并释放当前 Backend 持有的 ACTIVE lease。"""

    await initialize_execution_recovery_store(workspace)
    ended_at_text = _utc_iso(ended_at)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            UPDATE execution_records
            SET status = ?, updated_at = ?, ended_at = COALESCE(ended_at, ?)
            WHERE run_id = ?
              AND status = ?
            """,
            (
                status.value,
                ended_at_text,
                ended_at_text,
                run_id,
                DurableExecutionStatus.RUNNING.value,
            ),
        )
        if cursor.rowcount != 1:
            return False
        await connection.execute(
            """
            UPDATE execution_leases
            SET status = ?, released_at = COALESCE(released_at, ?)
            WHERE run_id = ?
              AND owner_backend_instance_id = ?
              AND status = ?
            """,
            (
                ExecutionLeaseStatus.RELEASED.value,
                ended_at_text,
                run_id,
                owner_backend_instance_id,
                ExecutionLeaseStatus.ACTIVE.value,
            ),
        )
        return True


async def list_running_executions_with_leases(
    workspace: str | Path,
) -> list[tuple[DurableExecutionRecord, ExecutionLease | None]]:
    """读取工作区全部 RUNNING 执行及其可选 lease，供恢复扫描器判断。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            SELECT
                e.run_id, e.thread_id, e.workspace, e.project_id,
                e.execution_kind, e.workflow_scope, e.first_node,
                e.current_node, e.status, e.last_recovery_point_id,
                e.started_at, e.updated_at, e.ended_at,
                l.run_id, l.owner_backend_instance_id, l.owner_pid,
                l.status, l.acquired_at, l.heartbeat_at, l.expires_at,
                l.released_at
            FROM execution_records AS e
            LEFT JOIN execution_leases AS l ON l.run_id = e.run_id
            WHERE e.status = ?
            ORDER BY e.started_at ASC, e.run_id ASC
            """,
            (DurableExecutionStatus.RUNNING.value,),
        )
        rows = await cursor.fetchall()
    return [
        (
            _execution_from_row(row[:13]),
            _execution_lease_from_row(row[13:]) if row[13] is not None else None,
        )
        for row in rows
    ]


async def mark_execution_interrupted(
    *,
    workspace: str | Path,
    run_id: str,
    interrupted_at: datetime,
) -> DurableExecutionRecord | None:
    """以 RUNNING 条件保护地将执行标记为 INTERRUPTED 并使 lease 过期。"""

    await initialize_execution_recovery_store(workspace)
    interrupted_at_text = _utc_iso(interrupted_at)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            UPDATE execution_records
            SET status = ?, updated_at = ?, ended_at = COALESCE(ended_at, ?)
            WHERE run_id = ?
              AND status = ?
            """,
            (
                DurableExecutionStatus.INTERRUPTED.value,
                interrupted_at_text,
                interrupted_at_text,
                run_id,
                DurableExecutionStatus.RUNNING.value,
            ),
        )
        if cursor.rowcount != 1:
            return None
        await connection.execute(
            """
            UPDATE execution_leases
            SET status = ?
            WHERE run_id = ?
              AND status = ?
            """,
            (
                ExecutionLeaseStatus.EXPIRED.value,
                run_id,
                ExecutionLeaseStatus.ACTIVE.value,
            ),
        )
        row = await _fetch_execution_row(connection, run_id)
        return _execution_from_row(row) if row is not None else None


async def reconcile_orphaned_executions(
    *,
    workspace: str | Path,
    current_backend_instance_id: str,
    locally_active_run_ids: set[str],
    now: datetime,
    lease_ttl_seconds: float = 45.0,
) -> list[DurableExecutionRecord]:
    """按 lease 所有者、当前运行表和 TTL 修正孤儿执行，并返回新中断项。"""

    running = await _list_running_executions_with_leases_and_attempts(workspace)
    interrupted: list[DurableExecutionRecord] = []
    for record, lease, attempt_status in running:
        pre_runtime_recovery = (
            attempt_status in {
                RecoveryAttemptStatus.PREPARING,
                RecoveryAttemptStatus.HANDED_OFF,
                RecoveryAttemptStatus.FINALIZING,
            }
            and lease is not None
            and lease.status in {
                ExecutionLeaseStatus.ACTIVE,
                ExecutionLeaseStatus.EXPIRED,
            }
        )
        if pre_runtime_recovery:
            # PREPARING/HANDED_OFF/FINALIZING 是 P0.3B 的 durable pre-runtime transaction，
            # Graph 尚未 STARTED；必须留给 lineage reconciliation 做 takeover/fork，
            # 不能被普通 orphan scanner 提前改成 INTERRUPTED。
            continue
        should_interrupt = (
            lease is None
            or lease.status is not ExecutionLeaseStatus.ACTIVE
            or lease.owner_backend_instance_id != current_backend_instance_id
            or (
                record.run_id not in locally_active_run_ids
                and lease.expires_at <= now
            )
        )
        if should_interrupt:
            marked = await mark_execution_interrupted(
                workspace=workspace,
                run_id=record.run_id,
                interrupted_at=now,
            )
            if marked is not None:
                interrupted.append(marked)
            continue
        if record.run_id in locally_active_run_ids and lease is not None:
            await renew_execution_lease(
                workspace=workspace,
                run_id=record.run_id,
                owner_backend_instance_id=current_backend_instance_id,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=lease_ttl_seconds),
            )
    return interrupted


async def _list_running_executions_with_leases_and_attempts(
    workspace: str | Path,
) -> list[
    tuple[DurableExecutionRecord, ExecutionLease | None, RecoveryAttemptStatus | None]
]:
    """读取 RUNNING execution、lease 及其 lineage 阶段供 scanner 做有限豁免。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            SELECT
                e.run_id, e.thread_id, e.workspace, e.project_id,
                e.execution_kind, e.workflow_scope, e.first_node,
                e.current_node, e.status, e.last_recovery_point_id,
                e.started_at, e.updated_at, e.ended_at,
                l.run_id, l.owner_backend_instance_id, l.owner_pid,
                l.status, l.acquired_at, l.heartbeat_at, l.expires_at,
                l.released_at, a.status
            FROM execution_records AS e
            LEFT JOIN execution_leases AS l ON l.run_id = e.run_id
            LEFT JOIN recovery_attempts AS a ON a.new_run_id = e.run_id
            WHERE e.status = ?
            ORDER BY e.started_at ASC, e.run_id ASC
            """,
            (DurableExecutionStatus.RUNNING.value,),
        )
        rows = await cursor.fetchall()
    return [
        (
            _execution_from_row(row[:13]),
            _execution_lease_from_row(row[13:21]) if row[13] is not None else None,
            RecoveryAttemptStatus(str(row[21])) if row[21] is not None else None,
        )
        for row in rows
    ]


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


async def get_recovery_point(
    workspace: str | Path,
    recovery_point_id: str,
) -> RecoveryPoint | None:
    """按稳定 recoveryPointId 读取单个恢复现场。"""

    await initialize_execution_recovery_store(workspace)
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            """
            SELECT recovery_point_id, run_id, thread_id, kind, checkpoint_id,
                   checkpoint_ns, graph_node, completed_node, next_nodes_json,
                   phase, state_status, lifecycle_revision, workspace_revision,
                   workspace_snapshot_hash, replay_safety, dedupe_key, captured_at
            FROM recovery_points
            WHERE recovery_point_id = ?
            """,
            (recovery_point_id,),
        )
        row = await cursor.fetchone()
        return _recovery_point_from_row(row) if row is not None else None


async def list_recovery_points(
    workspace: str | Path,
    run_id: str,
    *,
    newest_first: bool = False,
) -> list[RecoveryPoint]:
    """按捕获时间稳定读取一次执行的完整现场历史。"""

    await initialize_execution_recovery_store(workspace)
    order = "DESC" if newest_first else "ASC"
    async with _connection(workspace) as connection:
        cursor = await connection.execute(
            f"""
            SELECT recovery_point_id, run_id, thread_id, kind, checkpoint_id,
                   checkpoint_ns, graph_node, completed_node, next_nodes_json,
                   phase, state_status, lifecycle_revision, workspace_revision,
                   workspace_snapshot_hash, replay_safety, dedupe_key, captured_at
            FROM recovery_points
            WHERE run_id = ?
            ORDER BY captured_at {order}, recovery_point_id {order}
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


async def _fetch_active_attempt_row(
    connection: aiosqlite.Connection,
    source_run_id: str,
) -> tuple[object, ...] | None:
    """读取 source 当前仍占用唯一恢复分支的 child 行。"""

    cursor = await connection.execute(
        """
        SELECT new_run_id, source_run_id, thread_id,
               source_recovery_point_id, source_checkpoint_id,
               source_checkpoint_ns, replay_checkpoint_id,
               replay_checkpoint_ns, strategy, status, created_at,
               handed_off_at, started_at, failed_at, failure_code
        FROM recovery_attempts
        WHERE source_run_id = ?
          AND status IN ('preparing', 'handed_off', 'started')
        ORDER BY created_at ASC, new_run_id ASC
        LIMIT 1
        """,
        (source_run_id,),
    )
    return await cursor.fetchone()


async def _fetch_recovery_attempt_row(
    connection: aiosqlite.Connection,
    new_run_id: str,
) -> tuple[object, ...] | None:
    """按 child runId 读取恢复 lineage 原始行。"""

    cursor = await connection.execute(
        """
        SELECT new_run_id, source_run_id, thread_id,
               source_recovery_point_id, source_checkpoint_id,
               source_checkpoint_ns, replay_checkpoint_id,
               replay_checkpoint_ns, strategy, status, created_at,
               handed_off_at, started_at, failed_at, failure_code
        FROM recovery_attempts
        WHERE new_run_id = ?
        """,
        (new_run_id,),
    )
    return await cursor.fetchone()


async def _fetch_execution_lease_row(
    connection: aiosqlite.Connection,
    run_id: str,
) -> tuple[object, ...] | None:
    """读取执行租约的一行原始数据。"""

    cursor = await connection.execute(
        """
        SELECT run_id, owner_backend_instance_id, owner_pid, status,
               acquired_at, heartbeat_at, expires_at, released_at
        FROM execution_leases
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


def _execution_lease_from_row(row: tuple[object, ...]) -> ExecutionLease:
    """将租约表行恢复为严格的领域模型。"""

    return ExecutionLease(
        run_id=str(row[0]),
        owner_backend_instance_id=str(row[1]),
        owner_pid=int(row[2]),
        status=ExecutionLeaseStatus(str(row[3])),
        acquired_at=_parse_datetime(str(row[4])),
        heartbeat_at=_parse_datetime(str(row[5])),
        expires_at=_parse_datetime(str(row[6])),
        released_at=_parse_datetime(str(row[7])) if row[7] is not None else None,
    )


def _recovery_attempt_from_row(row: tuple[object, ...]) -> RecoveryAttempt:
    """将 recovery_attempts 行恢复为严格的 lineage 模型。"""

    return RecoveryAttempt(
        new_run_id=str(row[0]),
        source_run_id=str(row[1]),
        thread_id=str(row[2]),
        source_recovery_point_id=str(row[3]),
        source_checkpoint_id=str(row[4]),
        source_checkpoint_ns=str(row[5] or ""),
        replay_checkpoint_id=str(row[6]) if row[6] is not None else None,
        replay_checkpoint_ns=str(row[7] or ""),
        strategy=RecoveryStrategy(str(row[8])),
        status=RecoveryAttemptStatus(str(row[9])),
        created_at=_parse_datetime(str(row[10])),
        handed_off_at=_parse_datetime(str(row[11])) if row[11] is not None else None,
        started_at=_parse_datetime(str(row[12])) if row[12] is not None else None,
        failed_at=_parse_datetime(str(row[13])) if row[13] is not None else None,
        failure_code=str(row[14]) if row[14] is not None else None,
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
