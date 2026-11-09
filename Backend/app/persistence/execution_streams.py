"""在现有工作区恢复数据库中保存提交身份和紧凑输出，不复制业务状态机。"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import aiosqlite

from app.persistence.execution_recovery import execution_recovery_db_path
from app.domain.execution_recovery import RecoveryExecutionError


@asynccontextmanager
async def stream_store(workspace: str):
    """复用工作区 SQLite，并通过原子事务协调提交去重。"""
    path = execution_recovery_db_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""CREATE TABLE IF NOT EXISTS execution_streams (
            request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, session_id TEXT,
            endpoint TEXT NOT NULL, request_hash TEXT NOT NULL, request_body TEXT NOT NULL,
            backend_id TEXT NOT NULL, created_at TEXT NOT NULL, sequence INTEGER NOT NULL DEFAULT 0,
            finished INTEGER NOT NULL DEFAULT 0, output TEXT NOT NULL DEFAULT '{}')""")
        await db.commit()
        yield db


async def accept_execution(workspace: str, *, request_id: str, thread_id: str, session_id: str | None,
                           endpoint: str, request_hash: str, body: dict, backend_id: str) -> tuple[dict, bool]:
    """必须先持久接受请求；相同身份只接入原任务，内容变化或同线程并发均明确拒绝。"""
    async with stream_store(workspace) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT * FROM execution_streams WHERE request_id=?", (request_id,)) as cursor:
            row = await cursor.fetchone()
        if row:
            record = dict(row)
            if (record["request_hash"], record["thread_id"], record["session_id"], record["endpoint"]) != (request_hash, thread_id, session_id, endpoint):
                raise RecoveryExecutionError("EXECUTION_REQUEST_CONFLICT", "提交身份已属于另一个请求，请同步原任务。")
            return record, False
        async with db.execute("SELECT request_id FROM execution_streams WHERE thread_id=? AND finished=0 AND backend_id=?", (thread_id, backend_id)) as cursor:
            if await cursor.fetchone():
                raise RecoveryExecutionError("EXECUTION_THREAD_BUSY", "当前会话仍有后台任务，请同步或结束原任务。")
        created_at = datetime.now(timezone.utc).isoformat()
        await db.execute("INSERT INTO execution_streams(request_id,thread_id,session_id,endpoint,request_hash,request_body,backend_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (request_id, thread_id, session_id, endpoint, request_hash, json.dumps(body, ensure_ascii=False), backend_id, created_at))
        await db.commit()
        return dict(request_id=request_id, thread_id=thread_id, session_id=session_id, endpoint=endpoint,
            backend_id=backend_id, created_at=created_at, sequence=0, finished=0), True


async def save_execution_output(workspace: str, request_id: str, output: dict, sequence: int, finished: bool) -> None:
    """输出落盘成功后才交付订阅者；最终结果不依赖前端接收回执。"""
    async with stream_store(workspace) as db:
        await db.execute("UPDATE execution_streams SET output=?,sequence=?,finished=? WHERE request_id=?",
            (json.dumps(output, ensure_ascii=False), sequence, int(finished), request_id))
        await db.commit()


async def latest_execution_output(workspace: str, thread_id: str, session_id: str | None = None) -> dict[str, Any] | None:
    """仅读取指定线程最新提交，独立于 Graph 首个 checkpoint 是否已经产生。"""
    path = execution_recovery_db_path(workspace)
    if not path.is_file():
        return None
    # 读取不能借用写入连接建表；尚无提交时返回空，存储读取失败仍明确抛出。
    async with aiosqlite.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_streams'") as cursor:
            if await cursor.fetchone() is None:
                return None
        query = "SELECT rowid AS submission_order,* FROM execution_streams WHERE session_id=? ORDER BY rowid DESC LIMIT 1" if session_id else "SELECT rowid AS submission_order,* FROM execution_streams WHERE thread_id=? ORDER BY rowid DESC LIMIT 1"
        async with db.execute(query, (session_id or thread_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None or (session_id is not None and row["session_id"] != session_id):
        return None
    from app.services.execution_output import ExecutionOutput
    record = dict(row)
    output = json.loads(record["output"])
    if not output:
        return None
    presentation = ExecutionOutput(output).presentation(record)
    if not record["finished"]:
        from app.services.backend_instance import current_backend_instance
        if record["backend_id"] != current_backend_instance().instance_id:
            # 原 Backend 已退出时只保留文字，不把旧 running 或确认展示重新当成当前事实。
            presentation.update(finished=True)
    return presentation
