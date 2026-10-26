"""后端业务事实成立后，在专用线程池直接 POST 一期埋点。"""

from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.observability.telemetry_models import ExecutionTraceRecord, TelemetryEventRecord


logger = logging.getLogger(__name__)
# 埋点使用独立于业务线程池的两个工作线程；最多保留十六个执行中或待执行任务。
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telemetry")
_slots = BoundedSemaphore(value=16)


def telemetry_post_url() -> str:
    """读取后端埋点接收地址，未配置时关闭发送。"""

    return os.getenv("DEVAGENTSTUDIO_TELEMETRY_URL", "").strip()


def _now() -> str:
    """生成北京时间，格式为 YYYY-MM-DD HH:mm:ss，不依赖主机时区。"""

    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _valid_url(url: str) -> bool:
    """检查埋点地址是否为无内嵌凭证的 HTTP(S) 完整 URL。"""

    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        return False


def _request_body(
    record: TelemetryEventRecord | ExecutionTraceRecord,
    record_type: Literal["telemetry_event", "execution_trace"],
    occurred_at: str,
) -> dict[str, Any]:
    """保留显式 null，并将一条记录装入一期批次结构。"""

    payload = record.model_dump(mode="json", exclude_unset=True)
    payload.setdefault("type", record_type)
    payload.setdefault("record_id", uuid4().hex)
    payload.setdefault("occurred_at", occurred_at)
    # 只填缺失的键；显式 null（包括整个嵌套对象）始终保留。
    producer = payload.setdefault("producer", {})
    if producer is not None:
        for key, value in _producer_defaults().items():
            producer.setdefault(key, value)
    identity = payload.setdefault("identity", {})
    if identity is not None:
        identity.setdefault("user_id", "Hupy/test")
    return {
        "schema_version": 1,
        "batch_id": uuid4().hex,
        "sent_at": _now(),
        "records": [payload],
    }


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
        logger.warning("telemetry.product_version_unavailable")
    return defaults


def _post(url: str, body: dict[str, Any]) -> None:
    """直接发送单条记录；失败仅写状态日志，不反向影响业务。"""

    try:
        with httpx.Client(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = client.post(url, json=body)
    except httpx.RequestError as exc:
        logger.warning("telemetry.post_failed errorType=%s", type(exc).__name__)
        return
    except Exception as exc:
        logger.warning("telemetry.post_failed errorType=%s", type(exc).__name__)
        return
    if not 200 <= response.status_code < 300:
        logger.warning("telemetry.post_rejected status=%s", response.status_code)


def _emit(
    record: TelemetryEventRecord | ExecutionTraceRecord | dict[str, Any],
    record_type: Literal["telemetry_event", "execution_trace"],
    occurred_at: str,
) -> None:
    """在埋点专用线程中完成校验、配置读取、请求组装和发送。"""

    try:
        model_type = (
            TelemetryEventRecord if record_type == "telemetry_event" else ExecutionTraceRecord
        )
        validated = model_type.model_validate(record)
        url = telemetry_post_url()
        if not url:
            return
        if not _valid_url(url):
            logger.warning("telemetry.invalid_url")
            return
        body = _request_body(validated, record_type, occurred_at)
    except Exception as exc:
        logger.warning("telemetry.prepare_failed errorType=%s", type(exc).__name__)
        return
    _post(url, body)


def _release_slot(_future: Future[None]) -> None:
    """任务结束后释放一个待处理名额。"""

    _slots.release()


def _submit(
    record: TelemetryEventRecord | ExecutionTraceRecord | dict[str, Any],
    record_type: Literal["telemetry_event", "execution_trace"],
) -> None:
    """非阻塞提交到专用线程池，满载时丢弃埋点。"""

    if not _slots.acquire(blocking=False):
        logger.warning("telemetry.pool_full")
        return
    try:
        # 入口只捕获调用时刻；字段校验、默认值和发送留在专用工作线程。
        future = _executor.submit(_emit, record, record_type, _now())
    except Exception as exc:
        _slots.release()
        logger.warning("telemetry.submit_failed errorType=%s", type(exc).__name__)
        return
    future.add_done_callback(_release_slot)


def emit_telemetry_event(record: TelemetryEventRecord | dict[str, Any]) -> None:
    """将业务事件交给专用线程池，不等待埋点发送。"""

    _submit(record, "telemetry_event")


def emit_telemetry_trace(record: ExecutionTraceRecord | dict[str, Any]) -> None:
    """将执行 Trace 交给专用线程池，不等待埋点发送。"""

    _submit(record, "execution_trace")
