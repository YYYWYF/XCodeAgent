"""保存单次 Workspace Bootstrap 的独立运行日志。"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.services.workspace_bootstrap.run_log_errors import exception_details

_SENSITIVE_KEY = r"(?:access[_-]?token|refresh[_-]?token|token|password|passwd|secret|signature|credential|authorization|cookie|(?:private|public)[_-]?key|api[_-]?key|access[_-]?key|client[_-]?(?:secret|id))"
_SENSITIVE_QUOTED_ASSIGNMENT = re.compile(
    rf"(?is)([\"']?{_SENSITIVE_KEY}[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    rf"(?i)([\"']?{_SENSITIVE_KEY}[\"']?\s*[:=]\s*)(?!\[REDACTED\])([\"']?)([^,\s\"'&}}\]]+)([\"']?)"
)
_SENSITIVE_QUERY = re.compile(rf"(?i)([?&]{_SENSITIVE_KEY}=)(?!\[REDACTED\])[^&#\s]+")
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/@\s]+@")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    flags=re.DOTALL,
)


class TemplateRunLog:
    """管理一个 Bootstrap executionId 的日志句柄、阶段记录和清理。"""

    def __init__(self, workspace: Path, execution_id: str) -> None:
        """初始化单次运行的路径和内存状态。"""

        self.workspace = workspace
        self.execution_id = execution_id
        # 文件名使用本机当地时间且避开 Windows 禁用的冒号；短执行标识区分同毫秒任务。
        created_at = datetime.now().astimezone()
        timestamp = created_at.strftime("%Y-%m-%d_%H-%M-%S")
        milliseconds = created_at.microsecond // 1000
        self.path = (
            workspace
            / ".devagentstudio"
            / "runtime"
            / "template-download"
            / f"{timestamp}-{milliseconds:03d}_{execution_id[:8]}.log"
        )
        self.started_at = time.monotonic()
        self._stream: Any = None
        self._created = False
        self._lock = threading.RLock()
        self._stages: list[str] = []
        self.last_failed_stage = "bootstrap"
        self._failures: list[tuple[BaseException, str, str]] = []

    @classmethod
    def create(cls, workspace: str | Path) -> "TemplateRunLog":
        """创建本次执行专属日志；目录或文件不可写时返回静默禁用实例。"""

        root = Path(workspace)
        instance = cls(root, uuid4().hex)
        descriptor = -1
        try:
            root = root.expanduser().resolve(strict=False)
            instance.workspace = root
            instance.path = root / ".devagentstudio/runtime/template-download" / instance.path.name
            for directory in (
                root / ".devagentstudio",
                root / ".devagentstudio" / "runtime",
                instance.path.parent,
            ):
                if directory.is_symlink():
                    return instance
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not directory.resolve().is_relative_to(root):
                    return instance
            descriptor = os.open(
                instance.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND,
                0o600,
            )
            instance._stream = os.fdopen(descriptor, "a", encoding="utf-8")
            descriptor = -1
            instance._created = True
        except Exception:
            instance._stream = None
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return instance

    @property
    def current_stage(self) -> str:
        """返回当前嵌套阶段，供最外层失败事件标注真实位置。"""

        return self._stages[-1] if self._stages else self.last_failed_stage

    def event(
        self,
        *,
        stage: str,
        event: str,
        message: str,
        level: str = "INFO",
        duration_ms: int | None = None,
        exception: BaseException | None = None,
        **fields: Any,
    ) -> None:
        """写一条结构化事件，任何日志 I/O 错误都不会传回业务流程。"""

        try:
            record: dict[str, Any] = {
                "timestamp": datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                ),
                "level": level,
                "executionId": self.execution_id,
                "stage": stage,
                "event": event,
                "message": message,
            }
            if duration_ms is not None:
                record["durationMs"] = max(0, duration_ms)
            record.update(fields)
            if exception is not None:
                record["exceptionType"] = type(exception).__name__
                record["exceptionMessage"] = str(exception)
                root, details = exception_details(exception)
                record.update(details)
                with self._lock:
                    previous = next((item for item in self._failures if item[0] is root), None)
                    if previous is None:
                        previous = (root, f"error-{len(self._failures) + 1}", stage)
                        self._failures.append(previous)
                        record["traceback"] = "".join(
                            traceback.format_exception(type(exception), exception, exception.__traceback__)
                        )
                    record["errorRef"] = previous[1]
                    record["failedStage"] = previous[2]
                    if level == "ERROR":
                        self.last_failed_stage = previous[2]
            safe_record = {
                key: _safe_value(value, field_name=key) for key, value in record.items()
            }
            traceback_text = safe_record.pop("traceback", "")
            line = " ".join(
                f"{key}={_render_value(value)}" for key, value in safe_record.items()
            )
            if traceback_text:
                line += "\ntraceback:\n" + "\n".join(
                    f"  {part}" for part in traceback_text.rstrip("\n").splitlines()
                )
            with self._lock:
                if self._stream is None:
                    return
                self._stream.write(line + "\n")
                self._stream.flush()
        except Exception:
            with self._lock:
                self._disable_stream()

    @contextmanager
    def stage(self, stage: str, message: str, **fields: Any) -> Iterator[None]:
        """记录阶段开始和成功；异常时保留堆栈并原样向上抛出。"""

        started_at = time.monotonic()
        self._stages.append(stage)
        self.event(stage=stage, event="stage_started", message=message, **fields)
        try:
            yield
        except BaseException as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            self.event(
                stage=stage,
                event="stage_failed",
                message=f"{message}失败",
                level="ERROR",
                duration_ms=duration_ms,
                exception=exc,
            )
            raise
        else:
            self.event(
                stage=stage,
                event="stage_completed",
                message=f"{message}完成",
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        finally:
            self._stages.pop()

    def finish_failure(self, exception: BaseException) -> None:
        """记录整次 Bootstrap 失败并关闭文件，保留失败日志。"""

        self.event(
            stage=self.current_stage,
            event="bootstrap_failed",
            message="模板初始化失败，运行日志已保留",
            level="ERROR",
            duration_ms=int((time.monotonic() - self.started_at) * 1000),
            exception=exception,
        )
        self.close()

    def finish_success(self, *, final_path: str, **fields: Any) -> None:
        """记录最终成功，先关闭句柄，再只删除本次 executionId 的日志。"""

        self.event(
            stage="bootstrap",
            event="bootstrap_succeeded",
            message="模板初始化成功",
            duration_ms=int((time.monotonic() - self.started_at) * 1000),
            finalPath=final_path,
            **fields,
        )
        self.close()
        if not self._created:
            return
        try:
            if not self.path.parent.resolve().is_relative_to(self.workspace):
                raise OSError("template download log path escaped the workspace")
            self.path.unlink()
        except FileNotFoundError:
            return
        except Exception as exc:
            self._record_cleanup_failure(exc)

    def close(self) -> None:
        """关闭日志文件句柄，Windows 下删除前必须完成此操作。"""

        with self._lock:
            self._disable_stream()

    def _disable_stream(self) -> None:
        """尽力关闭并禁用日志句柄，不传播文件系统异常。"""

        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass

    def _record_cleanup_failure(self, exception: Exception) -> None:
        """日志删除失败时重新短暂打开本文件补记告警，不影响初始化结果。"""

        try:
            if not self.path.parent.resolve().is_relative_to(self.workspace):
                return
            line = " ".join(
                (
                    f"timestamp={_render_value(datetime.now().astimezone().isoformat(timespec='milliseconds'))}",
                    f"level={_render_value('WARNING')}",
                    f"executionId={_render_value(self.execution_id)}",
                    f"stage={_render_value('log_cleanup')}",
                    f"event={_render_value('log_cleanup_failed')}",
                    f"message={_render_value('初始化成功，但运行日志删除失败')}",
                    f"exceptionType={_render_value(type(exception).__name__)}",
                    f"exceptionMessage={_render_value(str(exception))}",
                )
            )
            traceback_text = _redact(
                "".join(
                    traceback.format_exception(
                        type(exception), exception, exception.__traceback__
                    )
                )
            )
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(_redact(line) + "\ntraceback:\n")
                stream.write(
                    "\n".join(
                        f"  {part}" for part in traceback_text.rstrip("\n").splitlines()
                    )
                    + "\n"
                )
        except Exception:
            pass


def _safe_value(value: Any, *, field_name: str = "") -> Any:
    """把任意事件字段转换为可序列化且已脱敏的内容。"""

    if re.search(
        r"(?i)(token|password|passwd|secret|signature|credential|authorization|cookie|private.?key|public.?key|api.?key|access.?key|client.?id)",
        field_name,
    ):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact(value)
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    return _redact(serialized)


def _render_value(value: Any) -> str:
    """以单行 JSON 字面量输出字段值，保留空格和堆栈换行。"""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _redact(value: str) -> str:
    """脱敏常见凭据、查询参数、Bearer 值和意外出现的私钥块。"""

    value = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = _BEARER.sub(r"\1[REDACTED]", value)
    value = _URL_USERINFO.sub(r"\1[REDACTED]@", value)
    value = _SENSITIVE_QUERY.sub(r"\1[REDACTED]", value)
    value = _SENSITIVE_QUOTED_ASSIGNMENT.sub(r"\1\2[REDACTED]\4", value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]\4", value)
