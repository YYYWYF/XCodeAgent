from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional


AgentRunEventType = Literal[
    "run.started",
    "contract.created",
    "contract.confirmed",
    "task.scheduled",
    "task.started",
    "task.completed",
    "task.failed",
    "verification.started",
    "verification.completed",
    "run.completed",
    "run.blocked",
]


def make_event(
    event_type: AgentRunEventType,
    *,
    run_id: str,
    task_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "type": event_type,
        "runId": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if task_id:
        event["taskId"] = task_id
    if payload:
        event.update(payload)
    return event
