"""统一队列、后台校验与内存诊断。"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from queue import Empty, Full, Queue
import sys
from threading import Event, Lock, Thread
from uuid import uuid4

from pydantic import BaseModel

from app.observability.telemetry_models import ExecutionTraceRecord, TelemetryEventRecord


def now() -> str:
    """使用固定 UTC+08:00，与已有采集端的北京时间字段一致。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def freeze(value, budget):
    """有界复制 JSON 形状和已构造模型，隔离提交后的调用方修改。"""
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("oversized telemetry")
    if isinstance(value, BaseModel):
        return freeze({key: getattr(value, key) for key in value.model_fields_set}, budget)
    if type(value) is dict:
        return {freeze(key, budget): freeze(item, budget) for key, item in value.items()}
    if type(value) is list:
        return [freeze(item, budget) for item in value]
    if type(value) is str:
        budget[1] -= len(value)
        if budget[1] < 0:
            raise ValueError("oversized telemetry")
        return value
    if value is None or type(value) in (bool, float):
        return value
    if type(value) is int and value.bit_length() <= 64:
        return value
    raise ValueError("unsupported telemetry")


def _producer_defaults() -> dict[str, str]:
    """仅从当前系统平台及产品版本来源生成采集端默认字段。"""

    defaults: dict[str, str] = {}
    platform = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform)
    if platform:
        defaults["platform"] = platform
    # 开发与打包均读取同一份产品 package.json；不使用后端 API 版本冒充产品版本。
    package_path = (
        Path(__file__).resolve().parents[1] / "product_metadata" / "package.json"
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[3] / "Frontend" / "package.json"
    )
    try:
        version = json.loads(package_path.read_text(encoding="utf-8"))["version"]
        if isinstance(version, str) and version.strip():
            defaults["xcodeagent_version"] = version
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return defaults


class TelemetryPipeline:
    """单后台线程处理有界记录，正常退出仅限时尝试清空。"""

    def __init__(self, sink, *, capacity=128):
        """为启用的唯一 Sink 分配内存和后台线程。"""
        self.sink = sink
        self.queue = Queue(maxsize=capacity)
        self.counts = Counter()
        self.count_lock = Lock()
        self.stop = Event()
        self.worker = Thread(target=self._run, name="telemetry-pipeline", daemon=True)
        self.worker.start()

    def submit(self, record, record_type):
        """冻结小型事件后立即入队，满载时丢弃最新记录。"""
        if self.stop.is_set():
            self._increment("dropped")
            return
        try:
            if self.queue.full():
                raise Full
            self.queue.put_nowait((freeze(record, [2048, 16384]), record_type, now()))
        except Full:
            self._increment("dropped")
        except Exception:
            self._increment("validation_failed")
        else:
            self._increment("accepted")

    def diagnostics(self):
        """提供进程内近实时诊断，pending 包括当前处理中的记录。"""
        names = ("accepted", "written", "sent", "dropped", "validation_failed",
                 "local_write_failed", "http_failed")
        with self.count_lock:
            counts = {name: self.counts[name] for name in names}
            counts["pending"] = max(0, counts["accepted"] - self.counts["processed"])
            return counts

    def _increment(self, name, amount=1):
        """同步多个提交线程及输出线程的计数更新。"""
        with self.count_lock:
            self.counts[name] += amount

    def close(self, timeout=0.25):
        """设置终止信号并限制等待时间，后台 I/O 卡住时仍可退出。"""
        self.stop.set()
        self.worker.join(timeout)

    def _run(self):
        """校验并按最多 16 条组批，隔离每个无效事件和每批输出失败。"""
        defaults = _producer_defaults()
        while not self.stop.is_set() or not self.queue.empty():
            try:
                items = [self.queue.get(timeout=0.05)]
            except Empty:
                continue
            while len(items) < 16:
                try:
                    items.append(self.queue.get_nowait())
                except Empty:
                    break
            batch = []
            for item in items:
                try:
                    batch.append(self._prepare(*item, defaults))
                except Exception:
                    self._increment("validation_failed")
            if batch:
                try:
                    self.sink.write(batch)
                except Exception:
                    self._increment(self.sink.failure, len(batch))
                else:
                    self._increment(self.sink.success, len(batch))
            self._increment("processed", len(items))

    @staticmethod
    def _prepare(record, record_type, occurred_at, defaults):
        """严格校验、补齐缺省字段并排除无法编码的单条记录。"""
        models = {"execution_trace": ExecutionTraceRecord, "telemetry_event": TelemetryEventRecord}
        model = models[record_type]
        payload = model.model_validate(record).model_dump(mode="json", exclude_unset=True)
        payload.setdefault("type", record_type)
        payload.setdefault("record_id", uuid4().hex)
        payload.setdefault("occurred_at", occurred_at)
        producer = payload.setdefault("producer", {})
        if producer is not None:
            for key, value in defaults.items():
                producer.setdefault(key, value)
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return payload
