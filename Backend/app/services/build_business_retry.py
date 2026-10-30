"""显式重试时重新判断已恢复的来源或静态证据，不复用旧验收结论。"""

from pathlib import Path
from typing import Any

from app.services.business_acceptance_verifier import _source_hash_errors, verify_business_acceptance
from app.services.runtime_business_acceptance import RUNTIME_VERIFIER_NAMES


def restored_source_task_ids(
    tasks: list[dict[str, Any]],
    *,
    formal_artifacts: dict[str, Any] | None,
    workspace_root: str | Path | None,
) -> set[str]:
    """只释放来源恢复或静态验收现已通过的失败任务，交回正常派发与验收。"""

    if not workspace_root:
        return set()
    restored: set[str] = set()
    for task in tasks:
        if task.get("status") != "failed" or task.get("failure_category") not in {
            "business_acceptance_blocked", "business_acceptance_failed",
        }:
            continue
        evidence = task.get("business_acceptance_evidence")
        checks = task.get("business_acceptance_checks")
        if not isinstance(evidence, list) or not evidence or not isinstance(checks, list) or not checks:
            continue
        if (any(not isinstance(item, dict) for item in [*checks, *evidence])
                or {item.get("check_id") for item in evidence} != {item.get("id") for item in checks}):
            continue
        blocked = [item for item in evidence if item.get("status") != "passed"]
        runtime_checks = all(check.get("kind") in RUNTIME_VERIFIER_NAMES for check in checks)
        # 只处理来源缺失及静态分析造成的旧失败，不按报错文案猜测。
        if not blocked or any(
            item.get("status") not in {"blocked", "failed"}
            or ((item.get("facts") or {}).get("reason_code") not in {
                "formal_source_unavailable", "python_evidence_unavailable", "implementation_mismatch", "verifier_internal_error",
            } and not (runtime_checks and not (item.get("facts") or {}).get("reason_code")))
            for item in blocked
        ):
            continue
        if any(_source_hash_errors(check, formal_artifacts, workspace_root=workspace_root) for check in checks):
            continue
        # 当前策略允许静态诊断作为提示；仍重新检查以保留正式来源和合同硬门禁。
        if any((item.get("facts") or {}).get("reason_code") != "formal_source_unavailable" for item in blocked):
            current = verify_business_acceptance(task, workspace_root, formal_artifacts=formal_artifacts)
            if current.get("status") not in {"passed", "advisory"}:
                continue
        restored.add(str(task["id"]))
    return restored
