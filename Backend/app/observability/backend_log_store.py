"""有界的平台日志窗口与 UTF-8 JSONL 滚动日志文件。"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.observability.backend_log_redaction import LogRedactor

MAX_WINDOW_BYTES = 1024 * 1024
MAX_WINDOW_LINES = 5000
MAX_FILE_BYTES = 10 * 1024 * 1024


def timestamp() -> str:
    """为文件、界面和稳定排序生成带时区的采集时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class BackendLogStore:
    """串行保存脱敏日志片段，并按记录身份合并模型流。"""

    def __init__(self, path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> None:
        """恢复有界近期日志，文件不可写时仍保留内存查看能力。"""
        self.path = path
        self.lock = threading.RLock()
        self.redactor = LogRedactor()
        self.entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.pending: dict[str, dict[str, Any]] = {}
        self.error = ""
        self.truncated = False
        self.revision = 0
        self._size = 0
        self._handler: RotatingFileHandler | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._restore()
            self._handler = RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=5, encoding="utf-8", delay=True,
            )
        except OSError as exc:
            self.error = f"日志文件不可用：{type(exc).__name__}"

    def _restore(self) -> None:
        """只从当前文件及归档尾部重建窗口，不读取无限历史。"""
        remaining = MAX_WINDOW_BYTES
        tails: list[bytes] = []
        for path in [self.path, *(self.path.with_name(f"{self.path.name}.{i}") for i in range(1, 6))]:
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                start = max(0, size - remaining)
                stream.seek(start)
                if start:
                    stream.readline()
                    self.truncated = True
                data = stream.read(remaining)
            tails.insert(0, data)
            remaining -= len(data)
            if remaining <= 0:
                self.truncated = True
                break
        for line in b"\n".join(tails).decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
                if isinstance(record, dict) and all(isinstance(record.get(k), str) for k in ("id", "timestamp", "source", "level", "text")):
                    record["text"] = self.redactor.redact(record["text"])
                    self._merge(record)
            except (ValueError, TypeError):
                self.truncated = True

    def _merge(self, record: dict[str, Any]) -> None:
        """将同一模型调用的连续片段合并，并限制窗口内存占用。"""
        key = record["id"]
        if key in self.entries:
            self.entries[key]["text"] += record["text"]
        else:
            self.entries[key] = dict(record)
        self._size += len(record["text"].encode("utf-8"))
        while self._size > MAX_WINDOW_BYTES or len(self.entries) > MAX_WINDOW_LINES:
            first_key, first = next(iter(self.entries.items()))
            if len(self.entries) == 1:
                first["text"] = first["text"].encode("utf-8")[-MAX_WINDOW_BYTES:].decode("utf-8", errors="ignore")
                self._size = len(first["text"].encode("utf-8"))
                self.truncated = True
                break
            self._size -= len(first["text"].encode("utf-8"))
            del self.entries[first_key]
            self.truncated = True
        self.revision += 1

    def _persist(self, record: dict[str, Any]) -> None:
        """直接写入脱敏片段，持久化失败不会递归记录或中断业务。"""
        # 超长工具参数按同一逻辑身份分片，避免单条 JSONL 超过恢复窗口或轮转上限。
        for offset in range(0, len(record["text"]), 16 * 1024):
            fragment = {**record, "text": record["text"][offset:offset + 16 * 1024]}
            self._merge(fragment)
            self._write_fragment(fragment)

    def _write_fragment(self, record: dict[str, Any]) -> None:
        """写入有界磁盘片段，供重启时从文件尾部恢复逻辑条目。"""
        if self._handler is None:
            return
        try:
            message = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            event = logging.LogRecord("backend-log-file", logging.INFO, "", 0, message, (), None)
            # 直接处理 I/O，让写盘错误可见；不走 Handler.handleError 的 stderr 递归。
            if self._handler.shouldRollover(event):
                self._handler.doRollover()
            if self._handler.stream is None:
                self._handler.stream = self._handler._open()
            self._handler.stream.write(message + "\n")
            self._handler.flush()
            self.error = ""
        except (OSError, UnicodeError) as exc:
            self.error = f"日志写入失败：{type(exc).__name__}"

    def record(self, text: str, *, source: str, level: str = "INFO", record_id: str | None = None) -> None:
        """将结构化日志或完整模型工具调用保存为一个逻辑条目。"""
        if not text:
            return
        with self.lock:
            self._persist({"id": record_id or uuid4().hex, "timestamp": timestamp(),
                           "source": source, "level": level, "text": self.redactor.redact(text)})

    def write(self, text: str, *, source: str, key: str, record_id: str | None = None) -> None:
        """合并 print 分段和模型 token，完整行立即落盘，尾部保留跨片段脱敏。"""
        if not text:
            return
        with self.lock:
            item = self.pending.setdefault(key, {
                "id": record_id or uuid4().hex, "timestamp": timestamp(),
                "source": source, "level": "INFO" if source != "stderr" else "OUTPUT", "text": "",
            })
            item["text"] += text
            end = item["text"].rfind("\n") + 1
            if end:
                self._drain(key, end)
            self.revision += 1

    def _drain(self, key: str, end: int) -> None:
        """只持久化可安全脱敏的前缀，保留逻辑记录身份与时间。"""
        item = self.pending[key]
        raw = item["text"][:end]
        self._persist({**item, "text": self.redactor.redact(raw)})
        item["text"] = item["text"][end:]
        if not item["text"]:
            del self.pending[key]

    def flush(self, *, final: bool = False) -> None:
        """定期保存长行安全前缀，退出时保存所有脱敏尾部。"""
        with self.lock:
            for key, item in list(self.pending.items()):
                end = len(item["text"]) if final else self.redactor.safe_prefix(item["text"])
                if end:
                    self._drain(key, end)

    def snapshot(self) -> dict[str, Any]:
        """返回包含未换行模型输出的有界脱敏快照，不暴露原始缓冲区。"""
        with self.lock:
            self.flush()
            entries = OrderedDict((key, dict(item)) for key, item in self.entries.items())
            for item in self.pending.values():
                text = self.redactor.redact(item["text"], partial=True)
                if item["id"] in entries:
                    entries[item["id"]]["text"] += text
                else:
                    entries[item["id"]] = {**item, "text": text}
            selected: list[dict[str, Any]] = []
            size = lines = 0
            truncated = self.truncated
            for item in reversed(entries.values()):
                count = max(1, item["text"].count("\n"))
                encoded = item["text"].encode("utf-8")
                if size + len(encoded) > MAX_WINDOW_BYTES or lines + count > MAX_WINDOW_LINES:
                    truncated = True
                    if not selected:
                        text = encoded[-MAX_WINDOW_BYTES:].decode("utf-8", errors="ignore")
                        selected.append({**item, "text": "\n".join(text.splitlines()[-MAX_WINDOW_LINES:])})
                    break
                selected.append(item)
                size += len(encoded)
                lines += count
            return {"entries": list(reversed(selected)), "filePath": str(self.path),
                    "updatedAt": timestamp(), "revision": self.revision,
                    "truncated": truncated, "storageError": self.error}

    def close(self) -> None:
        """释放滚动文件句柄，保证正常退出时尾部已保存。"""
        with self.lock:
            self.flush(final=True)
            if self._handler is not None:
                self._handler.close()
