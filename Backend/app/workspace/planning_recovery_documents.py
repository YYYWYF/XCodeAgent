"""Planning Recovery Snapshot 的显式 ID 存储与完整性校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.planning_recovery_contracts import PlanningRecoverySnapshot
from app.workspace.json_documents import write_json_atomic
from app.workspace.spec_documents import workflow_artifact_root


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
