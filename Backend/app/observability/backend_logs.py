"""Python 日志、标准输出和模型流的统一采集安装入口。"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, TextIO

from app.branding import DEFAULT_USER_DATA_DIRECTORY_NAME, USER_DATA_DIRECTORY_NAMES
from app.observability.backend_log_store import BackendLogStore

_model_record: ContextVar[str | None] = ContextVar("backend_model_log_record", default=None)
_store: BackendLogStore | None = None
_install_lock = threading.RLock()
_stop = threading.Event()


@contextmanager
def model_log_record(record_id: str) -> Iterator[None]:
    """为模型 token 绑定逻辑条目，避免并发调用在日志窗口中交错。"""
    token = _model_record.set(record_id)
    try:
        yield
    finally:
        _model_record.reset(token)


class CapturedStream:
    """透传原控制台输出，同时采集 print 和流式 token。"""

    def __init__(self, original: TextIO, source: str, store: BackendLogStore) -> None:
        """保存原流，供 logging 直接输出以免重复采集。"""
        self.original = original
        self.source = source
        self.store = store

    def write(self, text: str) -> int:
        """保持原有输出行为，并按模型调用或线程隔离缓冲区。"""
        result = self.original.write(text)
        record_id = _model_record.get()
        key = record_id or f"{self.source}:{threading.get_ident()}"
        self.store.write(text, source="model" if record_id else self.source,
                         key=key, record_id=record_id)
        return result

    def flush(self) -> None:
        """只刷新控制台；持久化由后台批处理，避免每个 token 单独写盘。"""
        self.original.flush()

    def __getattr__(self, name: str) -> Any:
        """透传 encoding、isatty、fileno 等标准流接口。"""
        return getattr(self.original, name)


class BackendLogHandler(logging.Handler):
    """以完整 LogRecord 为单位保存级别、来源和异常堆栈。"""

    def __init__(self, store: BackendLogStore) -> None:
        """安装独立日志输出，不改变应用的日志级别。"""
        super().__init__()
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        """过滤调试接口自己的访问记录，并避免传播链重复采集。"""
        if getattr(record, "_backend_log_captured", False):
            return
        record._backend_log_captured = True
        message = self.format(record)
        if record.name == "uvicorn.access" and "/backend-logs/run" in message:
            return
        self.store.record(message + "\n", source=record.name, level=record.levelname)


def get_backend_log_store() -> BackendLogStore:
    """返回当前环境唯一日志存储，不接收客户端提供的文件路径。"""
    global _store
    with _install_lock:
        if _store is None:
            directory = os.getenv("DEVAGENTSTUDIO_WORKING_DIR", DEFAULT_USER_DATA_DIRECTORY_NAME).strip()
            if directory not in USER_DATA_DIRECTORY_NAMES:
                raise RuntimeError("DEVAGENTSTUDIO_WORKING_DIR 不是有效的用户环境目录。")
            _store = BackendLogStore(Path.home() / directory / "logs" / "backend.log")
        return _store


def _flush_loop(store: BackendLogStore) -> None:
    """每 200 毫秒批量保存安全文本前缀，不依赖抽屉是否打开。"""
    while not _stop.wait(0.2):
        store.flush()


def _shutdown() -> None:
    """在 Python 正常退出时停止后台刷新并保存末尾输出。"""
    _stop.set()
    if _store is not None:
        _store.close()


def install_backend_log_capture() -> None:
    """幂等接管控制台，并在 Uvicorn 配置之后重新接入日志处理器。"""
    with _install_lock:
        store = get_backend_log_store()
        first_install = not isinstance(sys.stdout, CapturedStream)
        if first_install:
            sys.stdout = CapturedStream(sys.stdout, "stdout", store)  # type: ignore[assignment]
            sys.stderr = CapturedStream(sys.stderr, "stderr", store)  # type: ignore[assignment]
            threading.Thread(target=_flush_loop, args=(store,), name="backend-log-writer", daemon=True).start()
            atexit.register(_shutdown)
        loggers = [logging.getLogger(), *(item for item in logging.root.manager.loggerDict.values()
                                         if isinstance(item, logging.Logger))]
        for logger in loggers:
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler) and isinstance(handler.stream, CapturedStream):
                    handler.setStream(handler.stream.original)
            if not any(isinstance(handler, BackendLogHandler) for handler in logger.handlers):
                # 根日志捕获传播链；有独立 console handler 的 logger 也直接捕获。
                if logger is logging.getLogger() or not logger.propagate or logger.handlers:
                    logger.addHandler(BackendLogHandler(store))
