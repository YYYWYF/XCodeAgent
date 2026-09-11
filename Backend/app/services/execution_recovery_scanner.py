"""按已知 workspace 懒惰扫描失去 Backend owner 的 Durable Executions。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import (
    execution_recovery_lease_ttl_seconds,
)
from app.domain.execution_recovery import DurableExecutionRecord
from app.persistence.execution_recovery import reconcile_orphaned_executions
from app.services.backend_instance import current_backend_instance


logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class RecoveryScanResult:
    """描述一次 workspace-scoped 恢复扫描的结果。"""

    workspace: str
    interrupted_run_ids: list[str]
    active_run_ids: list[str]
    scanned_at: datetime


async def reconcile_workspace_recovery(
    workspace: str | Path,
    *,
    locally_active_run_ids: set[str] | None = None,
    current_backend_instance_id: str | None = None,
    lease_ttl_seconds: float | None = None,
    now: datetime | None = None,
) -> RecoveryScanResult:
    """扫描单个已解析 workspace，发现旧 owner 或过期 lease 后标记中断。"""

    workspace_text = str(Path(workspace).expanduser().resolve())
    identity = current_backend_instance()
    backend_instance_id = current_backend_instance_id or identity.instance_id
    if locally_active_run_ids is None:
        from app.protocols.workflow.run_control import workflow_run_registry

        locally_active_run_ids = workflow_run_registry.active_run_ids(workspace_text)
    active_ids = set(locally_active_run_ids)
    scanned_at = now or datetime.now(timezone.utc)
    logger.info(
        "recovery.workspace.scan.started workspace=%s backendInstanceId=%s",
        workspace_text,
        backend_instance_id,
    )
    try:
        interrupted = await reconcile_orphaned_executions(
            workspace=workspace_text,
            current_backend_instance_id=backend_instance_id,
            locally_active_run_ids=active_ids,
            now=scanned_at,
            lease_ttl_seconds=(
                lease_ttl_seconds
                if lease_ttl_seconds is not None
                else execution_recovery_lease_ttl_seconds()
            ),
        )
    except Exception as exc:
        logger.warning(
            "recovery.workspace.scan.failed workspace=%s "
            "backendInstanceId=%s error=%s",
            workspace_text,
            backend_instance_id,
            exc,
            exc_info=True,
        )
        return RecoveryScanResult(
            workspace=workspace_text,
            interrupted_run_ids=[],
            active_run_ids=sorted(active_ids),
            scanned_at=scanned_at,
        )
    interrupted_ids = [record.run_id for record in interrupted]
    for record in interrupted:
        logger.warning(
            "recovery.execution.interrupted runId=%s workspace=%s "
            "backendInstanceId=%s lastRecoveryPointId=%s",
            record.run_id,
            workspace_text,
            backend_instance_id,
            record.last_recovery_point_id,
        )
    result = RecoveryScanResult(
        workspace=workspace_text,
        interrupted_run_ids=interrupted_ids,
        active_run_ids=sorted(active_ids),
        scanned_at=scanned_at,
    )
    logger.info(
        "recovery.workspace.scan.finished workspace=%s backendInstanceId=%s "
        "interruptedRunIds=%s activeRunIds=%s",
        workspace_text,
        backend_instance_id,
        result.interrupted_run_ids,
        result.active_run_ids,
    )
    return result


__all__ = ["RecoveryScanResult", "reconcile_workspace_recovery"]
