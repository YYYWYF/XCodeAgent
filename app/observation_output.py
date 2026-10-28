"""共享线程池异步输出模型快照；容量不足和输出失败不阻塞模型请求。"""

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from . import model_call_logging

logger = logging.getLogger(__name__)


@dataclass
class _OutputTask:
    """为每次占用容量提供可撤销、仅结算一次的票据。"""

    cancelled: bool = False
    settled: bool = False


class ObservationOutput:
    """单工作线程、有限待处理数量、冻结 JSON 和可排空的服务级输出组件。"""

    def __init__(self, enabled: bool = True, max_pending: int = 256,
                 writer: Callable[[str], None] | None = None):
        """创建无网络、无工作线程的组件，首个任务或服务启动时才初始化线程池。"""
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending 必须为正整数")
        self.enabled, self.max_pending, self._writer = enabled, max_pending, writer
        self._condition = threading.Condition()
        self._executor: ThreadPoolExecutor | None = None
        self._inflight: dict[Future[None], tuple[_OutputTask, ThreadPoolExecutor]] = {}
        self._retired: set[ThreadPoolExecutor] = set()
        self._closed = False
        self._pending = 0
        self._counts = {key: 0 for key in (
            "submitted", "completed", "failed", "dropped_full", "dropped_closed", "serialization_failed",
        )}
        self._last_diagnostic = 0.0

    def _start_locked(self) -> None:
        """持有状态锁时初始化共享池，避免每个请求重复创建线程池。"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-observation-output")

    def start(self) -> None:
        """服务启动时准备输出池；禁用、关闭或初始化失败均不阻断服务。"""
        if not self.enabled:
            return
        with self._condition:
            if not self._closed:
                try:
                    self._start_locked()
                except Exception:
                    self._counts["failed"] += 1

    def _settle_locked(self, task: _OutputTask, outcome: str) -> None:
        """持有状态锁时单次结算，取消回调和工作线程不能重复释放容量。"""
        if not task.settled:
            task.settled = True
            self._counts[outcome] += 1
            self._pending -= 1
            self._condition.notify_all()

    def _future_done(self, future: Future[None]) -> None:
        """回收已完成或取消的 Future，并清理没有在途工作的退役线程池引用。"""
        with self._condition:
            entry = self._inflight.pop(future, None)
            if entry is None:
                return
            task, pool = entry
            if future.cancelled():
                task.cancelled = True
            else:
                # 读取异常也涵盖工作线程未进入正常 finally 的基础设施故障。
                future.exception()
            self._settle_locked(task, "failed")
            if not any(owner is pool for _, owner in self._inflight.values()):
                self._retired.discard(pool)

    def _retire_locked(self, pool: ThreadPoolExecutor) -> None:
        """清空故障池的排队任务而不等待正在输出的任务，后者由正常关闭负责排空。"""
        if self._executor is pool:
            self._executor = None
        if any(owner is pool for _, owner in self._inflight.values()):
            self._retired.add(pool)
        # submit 可能在入队后因创建线程失败而抛错；必须丢弃这类不可见的工作项。
        # cancel_futures 的回调会在当前线程执行，Condition 的可重入锁保证单次结算。
        pool.shutdown(wait=False, cancel_futures=True)

    def submit(self, record: dict[str, Any]) -> bool:
        """只冻结小型摘要并非阻塞投递，不等待控制台或未来 ES 的任何 I/O。"""
        if not self.enabled:
            return False
        with self._condition:
            if self._closed:
                self._counts["dropped_closed"] += 1
                return False
            if self._pending >= self.max_pending:
                self._counts["dropped_full"] += 1
                return False
            # 序列化中的任务也占容量，保证并发提交时不会突破总上限。
            self._pending += 1
            task = _OutputTask()
        try:
            payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except Exception:
            with self._condition:
                self._settle_locked(task, "serialization_failed")
            return False
        with self._condition:
            if self._closed:
                task.cancelled = True
                self._settle_locked(task, "dropped_closed")
                return False
            pool = None
            try:
                self._start_locked()
                pool = self._executor
                future = pool.submit(self._deliver, task, payload)
                self._counts["submitted"] += 1
                self._inflight[future] = (task, pool)
                future.add_done_callback(self._future_done)
                return True
            except Exception:
                task.cancelled = True
                self._settle_locked(task, "failed")
                if pool is not None:
                    self._retire_locked(pool)
                return False

    def _deliver(self, task: _OutputTask, payload: str) -> None:
        """在线程内执行统一输出入口，捕获异常并始终释放待处理容量。"""
        with self._condition:
            # 投递异常可能发生在 work item 入队之后，执行 I/O 前必须检查撤销票据。
            if task.cancelled:
                self._settle_locked(task, "failed")
                return
        success = False
        try:
            writer = self._writer if self._writer is not None else model_call_logging.deliver_record
            writer(payload)
            success = True
        except Exception:
            # 不回显异常原文，避免未来 ES 异常包含地址、认证或完整文档。
            pass
        finally:
            with self._condition:
                self._settle_locked(task, "completed" if success else "failed")
        self._diagnose()

    def _diagnose(self) -> None:
        """只在后台线程限频报告累计异常计数，不在请求协程中同步打印告警。"""
        now = time.monotonic()
        with self._condition:
            counts = dict(self._counts)
            failed = sum(counts[key] for key in ("failed", "dropped_full", "dropped_closed", "serialization_failed"))
            if not failed or (self._last_diagnostic and now - self._last_diagnostic < 30):
                return
            self._last_diagnostic = now
        try:
            logger.warning("模型统计异步输出累计：失败=%d，队列满=%d，关闭后提交=%d，序列化失败=%d",
                           counts["failed"], counts["dropped_full"], counts["dropped_closed"], counts["serialization_failed"])
        except Exception:
            pass

    def stats(self) -> dict[str, int]:
        """供本地诊断读取计数和待处理数量，不暴露新的产品 HTTP 接口。"""
        with self._condition:
            return {**self._counts, "pending": self._pending}

    def drain(self, timeout: float | None = None) -> bool:
        """在测试或正常停机时等待已提交任务排空，模型请求路径不调用此方法。"""
        with self._condition:
            return self._condition.wait_for(lambda: self._pending == 0, timeout=timeout)

    def close(self) -> None:
        """先拒绝新任务，再排空并关闭线程池；允许重复关闭。"""
        with self._condition:
            self._closed = True
            executors = set(self._retired)
            if self._executor is not None:
                executors.add(self._executor)
        for executor in executors:
            executor.shutdown(wait=True)
        # 同时等待关闭发生时尚在冻结快照的提交者释放其容量。
        self.drain()
