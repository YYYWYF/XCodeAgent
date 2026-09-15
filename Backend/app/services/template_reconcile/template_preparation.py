"""将 V2 持久 Reconcile Attempt 投影为 Workflow 可展示的 Template Preparation 状态。"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from app.services.template_reconcile.runtime_v2 import (
    ReconcileV2RuntimeError,
    load_current_attempt,
)


def template_preparation_projection_v2(workspace: str | Path) -> dict[str, Any] | None:
    """读取唯一 V2 Attempt 并生成可刷新 StateSnapshot；没有 V2 Attempt 时不伪造状态。"""

    try:
        attempt = load_current_attempt(workspace)
    except (OSError, UnicodeError, ValueError, ReconcileV2RuntimeError):
        return {
            "operationType": "UPDATE",
            "status": "FAILED",
            "phase": "RECOVERY_REQUIRED",
            "completedOperations": 0,
            "totalOperations": 4,
            "retryable": True,
            "errorCode": "TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED",
            "errorMessage": "Template Preparation V2 运行态无法读取。",
        }
    if attempt is None:
        return None
    completed = {
        "PREPARED": 0,
        "APPLYING": 1,
        "VALIDATING": 2,
        "COMMITTING_STATE": 3,
        "SUCCEEDED": 4,
        "FAILED": 0,
        "RECOVERY_REQUIRED": 0,
    }[attempt.phase]
    return {
        "operationType": attempt.operation_type,
        "attemptId": attempt.attempt_id,
        "retryOf": attempt.retry_of,
        "status": attempt.status,
        "phase": attempt.phase,
        "completedOperations": completed,
        "totalOperations": 4,
        "retryable": attempt.status == "FAILED" or attempt.phase == "RECOVERY_REQUIRED",
        "errorCode": attempt.error_code,
        "errorMessage": attempt.error_message,
        "validationResults": _validation_results(workspace, attempt.attempt_id),
        "startedAt": attempt.started_at,
        "updatedAt": attempt.updated_at,
        "logs": [
            {"timestamp": event.timestamp, "phase": event.phase, "level": event.level, "message": event.message}
            for event in attempt.events[-20:]
        ],
    }


def _validation_results(workspace: str | Path, attempt_id: str) -> list[dict[str, Any]]:
    """从本轮验收报告恢复结果，保留错误分类、命令与日志引用。"""

    path = Path(workspace) / ".xcodeagent/runtime/template-reconcile/attempts" / attempt_id / "validation-results.json"
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value["checks"]
