"""Planning Recovery Snapshot 的显式 ID 存储与完整性校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any

from app.services.planning_recovery_contracts import PlanningRecoverySnapshot
from app.workspace.json_documents import write_json_atomic
from app.workspace.spec_documents import workflow_artifact_root


@dataclass(frozen=True)
class PlanningRecoveryGcResult:
    """记录一次保守 Recovery GC 的精确删除与保留结果。"""

    deleted: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()


def planning_recovery_directory(state: dict[str, Any]) -> Path:
    """返回当前工作区的 Recovery Snapshot 专用目录，不混入 plans 或 drafts。"""

    return workflow_artifact_root(state) / "runtime" / "planning-recovery"


def _validate_workflow_run_id(source_workflow_run_id: str) -> str:
    """校验进入 Recovery 文件名的 Workflow ID 是单一安全路径组件。"""

    if not isinstance(source_workflow_run_id, str):
        raise TypeError("source_workflow_run_id 必须是字符串。")
    value = source_workflow_run_id
    if (
        not value
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("source_workflow_run_id 不是安全的 Recovery 文件名组件。")
    return value


def planning_recovery_path(
    state: dict[str, Any], source_workflow_run_id: str
) -> Path:
    """按显式 source_workflow_run_id 返回唯一 Recovery Snapshot 路径。"""

    source_id = _validate_workflow_run_id(source_workflow_run_id)
    directory = planning_recovery_directory(state)
    resolved_directory = directory.resolve(strict=False)
    candidate = directory / f"{source_id}.json"
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_directory)
    except ValueError as exc:
        raise ValueError("Recovery Snapshot 路径不能逃出 planning-recovery 目录。") from exc
    return candidate


def write_planning_recovery_atomic(
    state: dict[str, Any], snapshot: PlanningRecoverySnapshot
) -> str:
    """校验并原子写入一个以 source workflow execution 命名的 Recovery Snapshot。"""

    validated = PlanningRecoverySnapshot.model_validate(snapshot)
    path = planning_recovery_path(state, validated.source_workflow_run_id)
    write_json_atomic(path, validated.model_dump(mode="json"))
    return str(path)


def load_planning_recovery(
    state: dict[str, Any], source_workflow_run_id: str
) -> PlanningRecoverySnapshot | None:
    """只按调用方提供的精确 Workflow ID 读取并验证 Recovery Snapshot。"""

    source_id = _validate_workflow_run_id(source_workflow_run_id)
    path = planning_recovery_path(state, source_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError("Recovery Snapshot 必须是 JSON object。")
    snapshot = PlanningRecoverySnapshot.model_validate(payload)
    if snapshot.source_workflow_run_id != source_id:
        raise ValueError("Recovery Snapshot source_workflow_run_id 与请求的 Workflow execution 不匹配。")
    return snapshot


def delete_planning_recovery(
    state: dict[str, Any], source_workflow_run_id: str
) -> bool:
    """删除指定 source Workflow ID 的 Recovery Snapshot，不执行任何链路清理。"""

    path = planning_recovery_path(state, source_workflow_run_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def clear_planning_recovery_directory(state: dict[str, Any]) -> int:
    """在明确的 Application 删除路径中清理当前工作区的 Recovery 文件。"""

    directory = planning_recovery_directory(state)
    if not directory.is_dir():
        return 0
    deleted = 0
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        # Recovery 合同只写平铺 JSON；未知子目录不递归删除，避免扩大清理范围。
        if not (path.is_file() or path.is_symlink()):
            continue
        path.unlink()
        deleted += 1
    try:
        directory.rmdir()
    except OSError:
        # 目录非空或并发消失都不改变已经完成的逐文件清理结果。
        pass
    return deleted


def garbage_collect_stale_planning_recovery(
    state: dict[str, Any],
    *,
    max_age_seconds: float,
    now: datetime | None = None,
) -> PlanningRecoveryGcResult:
    """只清理已无法通过当前 lifecycle 精确 Retry 的过期 Recovery。"""

    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        raise ValueError("Recovery GC 的 max_age_seconds 必须是正数。")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("Recovery GC 的 now 必须携带时区。")

    directory = planning_recovery_directory(state)
    if not directory.is_dir():
        return PlanningRecoveryGcResult()

    # lifecycle 不可读或缺失时无法证明 exact resumeExecutionRunId 已失效，全部保留。
    try:
        from app.services.application_lifecycle import load_application_lifecycle

        lifecycle = load_application_lifecycle(str(state.get("workspace") or ""))
    except Exception:
        lifecycle = None
        lifecycle_readable = False
    else:
        lifecycle_readable = lifecycle is not None

    deleted: list[str] = []
    retained: list[str] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        source_id = path.stem
        try:
            snapshot = load_planning_recovery(state, source_id)
        except Exception:
            # 损坏 Snapshot 的身份和年龄都不可信，GC 不能借机扩大删除范围。
            retained.append(source_id)
            continue
        if snapshot is None:
            retained.append(source_id)
            continue
        try:
            created_at = _recovery_timestamp(snapshot.created_at)
        except (TypeError, ValueError):
            retained.append(source_id)
            continue
        age_seconds = (current_time - created_at).total_seconds()
        if age_seconds < max_age_seconds or age_seconds < 0:
            retained.append(source_id)
            continue
        if not lifecycle_readable:
            retained.append(source_id)
            continue
        active_executions = getattr(lifecycle, "active_executions", {})
        if source_id in active_executions:
            # 只要 lifecycle 仍保留该 execution，就不能断言 exact Retry 已失效。
            retained.append(source_id)
            continue
        try:
            if delete_planning_recovery(state, source_id):
                deleted.append(source_id)
            else:
                retained.append(source_id)
        except OSError:
            retained.append(source_id)
    return PlanningRecoveryGcResult(
        deleted=tuple(deleted),
        retained=tuple(retained),
    )


def _recovery_timestamp(value: str) -> datetime:
    """解析当前 Recovery 合同中的 UTC 时间，无法解析时让 GC 保守保留。"""

    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("Recovery timestamp 缺少时区。")
    return parsed
