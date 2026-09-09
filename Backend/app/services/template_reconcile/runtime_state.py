"""Template Reconcile 的最小持久化 Attempt 与原子 JSON 存储。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.services.workspace_bootstrap.models import TemplateStateError

TEMPLATE_RUNTIME_STATE_RELATIVE_PATH = Path(
    ".xcodeagent/runtime/template-runtime-state.json"
)
AttemptPhase = Literal[
    "APPLYING", "VALIDATING", "FAILED_CLEAN", "COMMITTING_METADATA", "RECONCILED"
]


@dataclass(frozen=True)
class ReconcileAttempt:
    """保存可重试或恢复一次文件操作所需的最小事实。"""

    change_id: str
    technical_plan_sha256: str
    pre_reconcile_head: str
    phase: AttemptPhase
    added_paths: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None


def template_runtime_state_path(workspace: str | Path) -> Path:
    """返回 Workspace 内唯一 Reconcile Runtime State 的规范路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_RUNTIME_STATE_RELATIVE_PATH


def load_reconcile_attempt(workspace: str | Path) -> ReconcileAttempt | None:
    """读取 Runtime State；不存在表示尚未开始 Reconcile。"""

    path = template_runtime_state_path(workspace)
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise TemplateStateError("Template Reconcile Runtime State 不是普通文件。")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateStateError("Template Reconcile Runtime State 无法读取。") from exc
    if not isinstance(document, dict) or set(document) != {"reconcileAttempt"}:
        raise TemplateStateError("Template Reconcile Runtime State 字段无效。")
    attempt = document["reconcileAttempt"]
    if attempt is None:
        return None
    if not isinstance(attempt, dict) or set(attempt) != {
        "changeId", "technicalPlanSha256", "preReconcileHead", "phase", "addedPaths", "error"
    }:
        raise TemplateStateError("Template Reconcile Attempt 字段无效。")
    phase = attempt["phase"]
    if phase not in {"APPLYING", "VALIDATING", "FAILED_CLEAN", "COMMITTING_METADATA", "RECONCILED"}:
        raise TemplateStateError("Template Reconcile Attempt phase 无效。")
    values = (attempt["changeId"], attempt["technicalPlanSha256"], attempt["preReconcileHead"])
    if not all(isinstance(value, str) and value for value in values):
        raise TemplateStateError("Template Reconcile Attempt 标识无效。")
    added_paths = attempt["addedPaths"]
    if not isinstance(added_paths, list) or not all(isinstance(path, str) and path for path in added_paths):
        raise TemplateStateError("Template Reconcile Attempt addedPaths 无效。")
    error = attempt["error"]
    if error is not None and not isinstance(error, str):
        raise TemplateStateError("Template Reconcile Attempt error 无效。")
    return ReconcileAttempt(
        change_id=attempt["changeId"],
        technical_plan_sha256=attempt["technicalPlanSha256"],
        pre_reconcile_head=attempt["preReconcileHead"],
        phase=phase,
        added_paths=tuple(added_paths),
        error=error,
    )


def save_reconcile_attempt(workspace: str | Path, attempt: ReconcileAttempt | None) -> None:
    """原子写入最小 Attempt；`None` 表示成功后清空运行态。"""

    payload: dict[str, Any] = {"reconcileAttempt": _attempt_payload(attempt)}
    atomic_write_json(template_runtime_state_path(workspace), payload)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """用 fsync 与同目录 replace 持久化 JSON，降低中断导致半文件的风险。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _attempt_payload(attempt: ReconcileAttempt | None) -> dict[str, Any] | None:
    """将内部 snake_case Attempt 转成冻结的持久化字段名。"""

    if attempt is None:
        return None
    value = asdict(attempt)
    return {
        "changeId": value["change_id"],
        "technicalPlanSha256": value["technical_plan_sha256"],
        "preReconcileHead": value["pre_reconcile_head"],
        "phase": value["phase"],
        "addedPaths": list(value["added_paths"]),
        "error": value["error"],
    }
