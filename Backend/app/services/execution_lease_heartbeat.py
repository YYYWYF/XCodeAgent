"""为真实 Durable Execution 维护持久化 lease 的心跳。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.persistence.execution_recovery import renew_execution_lease


logger = logging.getLogger("uvicorn.error")


async def maintain_execution_heartbeat(
    *,
    workspace: str,
    run_id: str,
    backend_instance_id: str,
    interval_seconds: float,
    lease_ttl_seconds: float,
) -> None:
    """在 Graph 运行期间周期性续租，失去 lease 后自动结束。"""

    while True:
        await asyncio.sleep(interval_seconds)
        heartbeat_at = datetime.now(timezone.utc)
        try:
            renewed = await renew_execution_lease(
                workspace=workspace,
                run_id=run_id,
                owner_backend_instance_id=backend_instance_id,
                heartbeat_at=heartbeat_at,
                expires_at=heartbeat_at + timedelta(seconds=lease_ttl_seconds),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "recovery.heartbeat.failed runId=%s backendInstanceId=%s "
                "workspace=%s error=%s",
                run_id,
                backend_instance_id,
                workspace,
                exc,
                exc_info=True,
            )
            continue
        if not renewed:
            logger.debug(
                "recovery.lease.heartbeat stopped runId=%s backendInstanceId=%s",
                run_id,
                backend_instance_id,
            )
            return
        logger.debug(
            "recovery.lease.heartbeat runId=%s backendInstanceId=%s "
            "heartbeatAt=%s expiresAt=%s",
            run_id,
            backend_instance_id,
            heartbeat_at.isoformat(),
            (heartbeat_at + timedelta(seconds=lease_ttl_seconds)).isoformat(),
        )


async def stop_execution_heartbeat(
    task: asyncio.Task[None] | None,
) -> None:
    """幂等停止心跳任务，并吞掉清理路径自身的 CancelledError。"""

    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return


__all__ = ["maintain_execution_heartbeat", "stop_execution_heartbeat"]
