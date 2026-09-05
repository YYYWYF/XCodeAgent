"""提供 V2 Reconcile 的 Run Gate、持久 Attempt 与 Roll-forward 判定。"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from app.utils.atomic_json import atomic_write_json

AttemptPhaseV2 = Literal["PREPARED", "APPLYING", "VALIDATING", "COMMITTING_STATE", "SUCCEEDED", "FAILED", "RECOVERY_REQUIRED"]


class ReconcileV2RuntimeError(ValueError):
    """表示 V2 尝试状态或恢复前置条件不满足。"""


@dataclass(frozen=True)
class ReconcileAttemptEventV2:
    """保存 Template Preparation 可展示的持久化阶段日志，不包含 Workspace 内容。"""

    timestamp: str
    phase: str
    level: Literal["INFO", "ERROR"]
    message: str


@dataclass(frozen=True)
class ReconcileAttemptV2:
    """保存 Roll-forward 所需的最小持久执行事实。"""

    attempt_id: str
    retry_of: str | None
    operation_type: Literal["UPDATE"]
    mode: Literal["APPLY", "RECONCILE"]
    protocol_version: Literal["2"]
    technical_plan_sha256: str
    package_id: str
    source_revision: str
    package_digest: str
    current_state_digest: str
    next_state_digest: str
    phase: AttemptPhaseV2
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    started_at: str
    updated_at: str
    error_code: str | None = None
    error_message: str | None = None
    events: tuple[ReconcileAttemptEventV2, ...] = ()


def reconcile_v2_root(workspace: str | Path) -> Path:
    """返回 V2 尝试和不可变 Package 的私有运行目录。"""

    return Path(workspace).expanduser().resolve() / ".xcodeagent/runtime/template-reconcile"


@contextmanager
def reconcile_run_gate(workspace: str | Path) -> Iterator[None]:
    """以非阻塞文件锁保证同一 Workspace 同时只有一个 V2 Writer。"""

    root = reconcile_v2_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".gate.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReconcileV2RuntimeError("TEMPLATE_RECONCILE_BUSY：Workspace 正在执行模板更新。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def persist_prepared_attempt(workspace: str | Path, attempt: ReconcileAttemptV2, package_zip: Path) -> None:
    """先持久化完整 Package，再原子记录 PREPARED Attempt，随后才允许写 Workspace。"""

    if attempt.phase != "PREPARED" or attempt.status != "RUNNING":
        raise ReconcileV2RuntimeError("只有 RUNNING/PREPARED Attempt 可以进入执行边界。")
    target = reconcile_v2_root(workspace) / "attempts" / attempt.attempt_id
    target.mkdir(parents=True, exist_ok=False)
    _persist_immutable_package(package_zip, target / "update-package.zip")
    _save_attempt(target / "attempt.json", attempt)
    atomic_write_json(reconcile_v2_root(workspace) / "current.json", {"attemptId": attempt.attempt_id})


def _persist_immutable_package(source: Path, destination: Path) -> None:
    """以临时文件、文件 fsync、rename 与目录 fsync 持久化 immutable ZIP。"""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as output_handle:
            with source.open("rb") as input_handle:
                while chunk := input_handle.read(64 * 1024):
                    output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary_name, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def load_current_attempt(workspace: str | Path) -> ReconcileAttemptV2 | None:
    """读取当前 Attempt；不存在代表没有待恢复的 V2 更新。"""

    current = reconcile_v2_root(workspace) / "current.json"
    if not current.exists():
        return None
    raw = json.loads(current.read_text(encoding="utf-8"))
    attempt_id = raw.get("attemptId") if isinstance(raw, dict) else None
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ReconcileV2RuntimeError("V2 current Attempt 指针无效。")
    return _load_attempt(reconcile_v2_root(workspace) / "attempts" / attempt_id / "attempt.json")


def update_attempt(workspace: str | Path, attempt: ReconcileAttemptV2, **changes: object) -> ReconcileAttemptV2:
    """原子推进当前 Attempt phase，并自动刷新更新时间。"""

    event_message = changes.pop("event_message", None)
    next_phase = str(changes.get("phase", attempt.phase))
    event = ReconcileAttemptEventV2(
        timestamp=_now(),
        phase=next_phase,
        level="ERROR" if changes.get("status") == "FAILED" else "INFO",
        message=str(event_message or changes.get("error_message") or f"Template Preparation 进入 {next_phase}。"),
    )
    updated = replace(attempt, updated_at=event.timestamp, events=(*attempt.events, event), **changes)
    _save_attempt(reconcile_v2_root(workspace) / "attempts" / attempt.attempt_id / "attempt.json", updated)
    return updated


def recovery_action(attempt: ReconcileAttemptV2, state_digest: str) -> Literal["REPLAY", "FINALIZE", "CONFLICT"]:
    """按当前 State digest 给出唯一的 Roll-forward 分支。"""

    if state_digest == attempt.current_state_digest:
        return "REPLAY"
    if state_digest == attempt.next_state_digest:
        return "FINALIZE"
    return "CONFLICT"


def _save_attempt(path: Path, attempt: ReconcileAttemptV2) -> None:
    """将 Attempt 转成冻结的 camelCase 持久化结构。"""

    atomic_write_json(path, {
        "attemptId": attempt.attempt_id, "retryOf": attempt.retry_of,
        "operationType": attempt.operation_type, "mode": attempt.mode,
        "protocolVersion": attempt.protocol_version, "technicalPlanSha256": attempt.technical_plan_sha256,
        "packageId": attempt.package_id, "sourceRevision": attempt.source_revision,
        "packageDigest": attempt.package_digest,
        "currentStateDigest": attempt.current_state_digest, "nextStateDigest": attempt.next_state_digest,
        "phase": attempt.phase, "status": attempt.status, "startedAt": attempt.started_at,
        "updatedAt": attempt.updated_at, "errorCode": attempt.error_code, "errorMessage": attempt.error_message,
        "events": [
            {"timestamp": event.timestamp, "phase": event.phase, "level": event.level, "message": event.message}
            for event in attempt.events
        ],
    })


def _load_attempt(path: Path) -> ReconcileAttemptV2:
    """读取严格固定的 Attempt 字段，拒绝旧运行态。"""

    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        required_fields = {
            "attemptId", "retryOf", "operationType", "mode", "protocolVersion",
            "technicalPlanSha256", "packageId", "sourceRevision", "packageDigest",
            "currentStateDigest", "nextStateDigest", "phase", "status", "startedAt",
            "updatedAt", "errorCode", "errorMessage", "events",
        }
        if not isinstance(raw, dict) or set(raw) != required_fields:
            raise TypeError("Attempt 字段集合无效")
        events = raw["events"]
        if not isinstance(events, list):
            raise TypeError("events 必须是数组")
        required_strings = (
            raw["attemptId"], raw["operationType"], raw["mode"], raw["protocolVersion"],
            raw["technicalPlanSha256"], raw["packageId"], raw["sourceRevision"], raw["packageDigest"],
            raw["currentStateDigest"], raw["nextStateDigest"], raw["phase"], raw["status"],
            raw["startedAt"], raw["updatedAt"],
        )
        if not all(isinstance(value, str) and value for value in required_strings):
            raise TypeError("Attempt 必填字段必须是非空字符串")
        if raw["retryOf"] is not None and (not isinstance(raw["retryOf"], str) or not raw["retryOf"]):
            raise TypeError("retryOf 必须为 null 或非空字符串")
        if raw["operationType"] != "UPDATE" or raw["mode"] not in {"APPLY", "RECONCILE"} or raw["protocolVersion"] != "2":
            raise ValueError("Attempt 协议字段无效")
        if raw["phase"] not in {"PREPARED", "APPLYING", "VALIDATING", "COMMITTING_STATE", "SUCCEEDED", "FAILED", "RECOVERY_REQUIRED"} or raw["status"] not in {"RUNNING", "SUCCEEDED", "FAILED"}:
            raise ValueError("Attempt phase 或 status 无效")
        if raw["errorCode"] is not None and not isinstance(raw["errorCode"], str):
            raise TypeError("errorCode 必须为 null 或字符串")
        if raw["errorMessage"] is not None and not isinstance(raw["errorMessage"], str):
            raise TypeError("errorMessage 必须为 null 或字符串")
        return ReconcileAttemptV2(
            attempt_id=raw["attemptId"], retry_of=raw["retryOf"], operation_type=raw["operationType"], mode=raw["mode"],
            protocol_version=raw["protocolVersion"], technical_plan_sha256=raw["technicalPlanSha256"], package_id=raw["packageId"], source_revision=raw["sourceRevision"],
            package_digest=raw["packageDigest"], current_state_digest=raw["currentStateDigest"], next_state_digest=raw["nextStateDigest"],
            phase=raw["phase"], status=raw["status"], started_at=raw["startedAt"], updated_at=raw["updatedAt"],
            error_code=raw["errorCode"], error_message=raw["errorMessage"],
            events=tuple(_event_from_raw(event) for event in events),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReconcileV2RuntimeError("V2 Attempt 不符合当前协议。") from exc


def _event_from_raw(value: object) -> ReconcileAttemptEventV2:
    """严格恢复单条持久事件，避免损坏日志被静默跳过而误导 Retry 判断。"""

    if not isinstance(value, dict):
        raise TypeError("event 必须是对象")
    timestamp = value["timestamp"]
    phase = value["phase"]
    level = value["level"]
    message = value["message"]
    if not all(isinstance(item, str) and item for item in (timestamp, phase, level, message)):
        raise TypeError("event 字段必须是非空字符串")
    if level not in {"INFO", "ERROR"}:
        raise ValueError("event level 无效")
    return ReconcileAttemptEventV2(timestamp=timestamp, phase=phase, level=level, message=message)


def _now() -> str:
    """生成 Attempt 审计使用的 UTC 时间戳。"""

    return datetime.now(timezone.utc).isoformat()
