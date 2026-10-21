"""独立调用历史存储，不承担正式聊天保存或上下文拼接。"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """生成可排序的 UTC 持久化时间。"""
    return datetime.now(timezone.utc).isoformat()


def json_text(value: Any) -> str:
    """统一保存无 ASCII 转义的 JSON 业务载荷。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class DuplicateRunError(Exception):
    """阻止重复运行身份触发第二次模型调用。"""


class HistoryStore:
    """每次事务使用独立连接，SQLite 唯一约束保障并发防重。"""

    def __init__(self, path: Path):
        """创建当前存储形态，并把上次进程遗留调用标记中断。"""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS model_calls (
                run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, request_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL, session_id TEXT NOT NULL, upstream_type TEXT NOT NULL,
                model_name TEXT NOT NULL, input_context TEXT NOT NULL, model_output TEXT,
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                duration_ms INTEGER, usage TEXT, finish_reason TEXT, error TEXT,
                langsmith_run_id TEXT, user_id TEXT, project_id TEXT, studio_data_ref TEXT,
                analysis TEXT NOT NULL, extensions TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS calls_session ON model_calls(session_id,started_at)")
            db.execute("UPDATE model_calls SET status='interrupted',finished_at=?,error=? WHERE status='running'",
                       (utc_now(), json_text({"code": "PROCESS_INTERRUPTED", "message": "进程在调用结束前停止"})))

    @contextmanager
    def connect(self):
        """以有界锁等待打开事务，提交或异常回滚后关闭连接。"""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, record: dict[str, Any]) -> None:
        """先持久化调用身份与完整实际输入，再允许访问上游。"""
        columns = list(record)
        try:
            with self.connect() as db:
                db.execute(f"INSERT INTO model_calls ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           [record[key] for key in columns])
        except sqlite3.IntegrityError as exc:
            raise DuplicateRunError("runId 已登记，不能重复发起推理") from exc

    def finish(self, run_id: str, *, status: str, output: dict[str, Any], duration_ms: int,
               error: dict[str, Any] | None, analysis: dict[str, Any], langsmith_run_id: str | None) -> None:
        """原子结束调用，失败和取消也保留已经收到的部分模型产出。"""
        reasons = [choice.get("finish_reason") for choice in output.get("choices", [])]
        with self.connect() as db:
            db.execute("""UPDATE model_calls SET status=?,model_output=?,finished_at=?,duration_ms=?,
                       usage=?,finish_reason=?,error=?,analysis=?,langsmith_run_id=? WHERE run_id=? AND status='running'""",
                       (status, json_text(output), utc_now(), duration_ms, json_text(output.get("usage")),
                        json_text(reasons), json_text(error), json_text(analysis), langsmith_run_id, run_id))

    def checkpoint(self, run_id: str, output: dict[str, Any], langsmith_run_id: str | None) -> None:
        """周期保存部分输出，进程意外停止后仍可分析最近一次已提交片段。"""
        with self.connect() as db:
            db.execute("UPDATE model_calls SET model_output=?,langsmith_run_id=? WHERE run_id=? AND status='running'",
                       (json_text(output), langsmith_run_id, run_id))

    def get(self, run_id: str) -> dict[str, Any] | None:
        """供运维脚本与测试按运行身份读取历史，不暴露未鉴权查询接口。"""
        with self.connect() as db:
            row = db.execute("SELECT * FROM model_calls WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("input_context", "model_output", "usage", "finish_reason", "error", "analysis", "extensions"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result
