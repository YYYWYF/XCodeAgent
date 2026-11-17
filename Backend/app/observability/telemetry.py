"""后端分析事件的唯一提交入口。"""

import atexit
import os
from threading import Lock

from app.observability.telemetry_pipeline import TelemetryPipeline
from app.observability.telemetry_sinks import HttpSink, LocalSink

_pipeline = None
_start_lock = Lock()


def mode() -> str:
    """解析启动环境，未知显式模式按 OFF 处理。"""
    selected = os.getenv("DEVAGENTSTUDIO_OBSERVABILITY_MODE")
    if selected is None:
        return "http" if os.getenv("DEVAGENTSTUDIO_TELEMETRY_URL", "").strip() else "off"
    selected = selected.strip().lower()
    return selected if selected in {"local", "http"} else "off"


def enabled() -> bool:
    """在构造事件或读取统计内容前执行轻量开关检查。"""
    return mode() != "off"


def publish(record, record_type="execution_trace") -> None:
    """尽力提交；OFF 立即返回，输出及校验均在同一后台管道执行。"""
    global _pipeline
    try:
        selected = mode()
        if selected == "off":
            return
        if _pipeline is None:
            with _start_lock:
                if _pipeline is None:
                    sink = LocalSink() if selected == "local" else HttpSink()
                    _pipeline = TelemetryPipeline(sink)
                    atexit.register(_pipeline.close)
        _pipeline.submit(record, record_type)
    except Exception:
        # 启动失败和提交异常都不向业务侧传播，也不产生递归埋点。
        return


def diagnostics():
    """读取当前管道的内存诊断。"""
    if _pipeline is None:
        return dict.fromkeys(("accepted", "written", "sent", "dropped", "validation_failed",
                              "local_write_failed", "http_failed", "pending"), 0)
    return _pipeline.diagnostics()


def emit_telemetry_event(record) -> None:
    """兼容既有业务事件提交入口。"""
    publish(record, "telemetry_event")


def emit_telemetry_trace(record) -> None:
    """兼容既有执行 Trace 提交入口。"""
    publish(record)
