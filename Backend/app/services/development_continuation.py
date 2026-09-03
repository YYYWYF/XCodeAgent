"""页面/API开发跨独立 EntitySourceBinding execution 的一次性续接服务。"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    DevelopmentContinuation,
    DevelopmentContinuationTarget,
    PendingInteractionType,
    WorkbenchExecutionStatus,
    utc_now,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.artifact_invalidation import canonical_sha256
from app.services.development_readiness import development_readiness
from app.workspace.plan_documents import load_project_plan_json


def register_development_continuation(
    workspace: str | Path,
    *,
    source_thread_id: str,
    source_run_id: str,
    request: str,
    target: DevelopmentContinuationTarget,
    required_entity_ids: list[str],
) -> DevelopmentContinuation:
    """在开发门禁阻断时登记原 execution 与实体前置之间的服务端合同。"""

    current = _required_lifecycle(workspace)
    source = current.active_executions.get(source_run_id)
    if (
        source is None
        or source.thread_id != source_thread_id
        or source.status != WorkbenchExecutionStatus.AWAITING_USER
        or source.pending_interaction is None
        or source.pending_interaction.type != PendingInteractionType.ENTITY_SOURCE_BINDING
    ):
        raise ApplicationLifecycleConflictError("实体绑定 continuation 缺少有效的原开发 execution。")
    continuation_id = _continuation_id(current.application.id, source_run_id)
    existing = current.development_continuations.get(continuation_id)
    if existing is not None and existing.status != "consumed":
        return existing
    entity_ids = list(dict.fromkeys(item.strip() for item in required_entity_ids if item.strip()))
    continuation = DevelopmentContinuation(
        id=continuation_id,
        sourceThreadId=source_thread_id,
        sourceRunId=source_run_id,
        request=request,
        target=target,
        requiredEntityIds=entity_ids,
        status="awaiting_entity_binding",
        createdAt=utc_now(),
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "development_continuations": {
                **current.development_continuations,
                continuation_id: continuation,
            },
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return continuation


def validate_entity_binding_continuation(
    workspace: str | Path,
    *,
    continuation_id: str,
    entity_id: str,
    binding_thread_id: str,
) -> DevelopmentContinuation:
    """验证实体绑定使用独立 thread，且实体确实属于原开发门禁。"""

    current = _required_lifecycle(workspace)
    continuation = _required_continuation(current.development_continuations, continuation_id)
    source = current.active_executions.get(continuation.source_run_id)
    if continuation.status != "awaiting_entity_binding":
        raise ApplicationLifecycleConflictError("当前开发 continuation 已不再等待实体绑定。")
    if entity_id not in continuation.required_entity_ids:
        raise ApplicationLifecycleConflictError("当前实体不属于原开发目标的缺失前置。")
    if binding_thread_id == continuation.source_thread_id:
        raise ApplicationLifecycleConflictError("EntitySourceBinding 必须使用独立 execution thread。")
    if (
        source is None
        or source.status != WorkbenchExecutionStatus.AWAITING_USER
        or source.pending_interaction is None
        or source.pending_interaction.type != PendingInteractionType.ENTITY_SOURCE_BINDING
    ):
        raise ApplicationLifecycleConflictError("原开发 execution 已失效，不能继续实体绑定。")
    return continuation


def issue_development_continuation(
    workspace: str | Path,
    *,
    continuation_id: str,
) -> dict[str, Any]:
    """实体确认后复检最新 TechnicalPlan，仅在原目标 ready 时签发一次性 token。"""

    current = _required_lifecycle(workspace)
    continuation = _required_continuation(current.development_continuations, continuation_id)
    if continuation.status == "consumed":
        raise ApplicationLifecycleConflictError("开发 continuation 已消费。")
    plan_path = _technical_plan_path(workspace)
    plan = load_project_plan_json(plan_path, hydrate_detail_designs=True)
    readiness = _continuation_readiness(plan, continuation.target)
    if not readiness.get("ready"):
        return development_continuation_payload(
            continuation,
            remaining_entity_ids=[
                str(item.get("entity_id") or "")
                for item in readiness.get("missing_entities", [])
                if isinstance(item, dict) and str(item.get("entity_id") or "")
            ],
        )
    token = secrets.token_urlsafe(48)
    ready = continuation.model_copy(
        update={
            "status": "ready",
            "technical_plan_sha256": canonical_sha256(plan_path),
            "token_sha256": _token_sha256(token),
            "ready_at": utc_now(),
        }
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "development_continuations": {
                **current.development_continuations,
                continuation_id: ready,
            },
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return development_continuation_payload(ready, token=token)


def validate_development_continuation(
    workspace: str | Path,
    *,
    continuation_id: str,
    token: str,
    thread_id: str,
    lifecycle: ApplicationLifecycle | None = None,
) -> DevelopmentContinuation:
    """只读验证续接；消费必须与新 execution 登记在同一次生命周期写入中完成。"""

    current = lifecycle if lifecycle is not None else _required_lifecycle(workspace)
    continuation = _required_continuation(current.development_continuations, continuation_id)
    source = current.active_executions.get(continuation.source_run_id)
    if continuation.status != "ready" or continuation.consumed_at is not None:
        raise ApplicationLifecycleConflictError("开发 continuation 已消费或当前不可用。")
    if thread_id != continuation.source_thread_id:
        raise ApplicationLifecycleConflictError("开发 continuation 必须恢复原 execution thread。")
    if not secrets.compare_digest(
        str(continuation.token_sha256 or ""),
        _token_sha256(token),
    ):
        raise ApplicationLifecycleConflictError("开发 continuation token 无效。")
    if (
        source is None
        or source.status != WorkbenchExecutionStatus.AWAITING_USER
        or source.pending_interaction is None
        or source.pending_interaction.type != PendingInteractionType.ENTITY_SOURCE_BINDING
    ):
        raise ApplicationLifecycleConflictError("原开发 execution 已失效，不能续接。")
    plan_path = _technical_plan_path(workspace)
    if continuation.technical_plan_sha256 != canonical_sha256(plan_path):
        raise ApplicationLifecycleConflictError("TechnicalPlan 已变化，开发 continuation 作废。")
    plan = load_project_plan_json(plan_path, hydrate_detail_designs=True)
    if not _continuation_readiness(plan, continuation.target).get("ready"):
        raise ApplicationLifecycleConflictError("原开发目标的实体前置尚未全部完成。")
    return continuation


def development_continuation_payload(
    continuation: DevelopmentContinuation,
    *,
    token: str | None = None,
    remaining_entity_ids: list[str] | None = None,
) -> dict[str, Any]:
    """投射不含 token 哈希的公开 continuation，ready 状态才携带一次性 token。"""

    target = continuation.target.model_dump(mode="json", by_alias=True, exclude_none=True)
    remaining = (
        list(remaining_entity_ids)
        if remaining_entity_ids is not None
        else list(continuation.required_entity_ids)
        if continuation.status == "awaiting_entity_binding"
        else []
    )
    payload: dict[str, Any] = {
        "id": continuation.id,
        "status": continuation.status,
        "action": (
            "continue_after_entity_binding"
            if continuation.status == "ready"
            else "start_entity_binding"
        ),
        "sourceThreadId": continuation.source_thread_id,
        "sourceRunId": continuation.source_run_id,
        "target": target,
        "requiredEntityIds": list(continuation.required_entity_ids),
        "remainingEntityIds": remaining,
    }
    if token:
        payload["token"] = token
        payload["technicalPlanSha256"] = continuation.technical_plan_sha256
    return payload


def _continuation_readiness(
    project_plan: dict[str, Any],
    target: DevelopmentContinuationTarget,
) -> dict[str, Any]:
    """使用 continuation 的服务端目标复检实体绑定，不接受客户端目标覆盖。"""

    if target.type == "page":
        return development_readiness(
            project_plan,
            target_type="page",
            target_id=str(target.page_id or ""),
        )
    return development_readiness(
        project_plan,
        target_type="endpoint",
        target_id=str(target.endpoint_id or ""),
        api_contract_id=str(target.api_contract_id or ""),
    )


def _technical_plan_path(workspace: str | Path) -> Path:
    """返回当前契约唯一允许的 TechnicalPlan JSON 路径。"""

    path = Path(workspace).expanduser() / ".xcodeagent" / "plans" / "technical-plan.json"
    if not path.is_file():
        raise ApplicationLifecycleConflictError("缺少 TechnicalPlan，不能续接开发。")
    return path


def _continuation_id(application_id: str, source_run_id: str) -> str:
    """由应用和原 execution 生成稳定 continuation ID，保证节点重放幂等。"""

    seed = f"{application_id}:{source_run_id}:entity-binding"
    return f"devcont_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _token_sha256(token: str) -> str:
    """只持久化一次性 token 的哈希。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _required_lifecycle(workspace: str | Path):
    """加载已初始化生命周期，缺失时返回稳定业务错误。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("application lifecycle 尚未初始化。")
    return current


def _required_continuation(
    continuations: dict[str, DevelopmentContinuation],
    continuation_id: str,
) -> DevelopmentContinuation:
    """按 ID 获取当前 continuation，禁止把不存在的客户端引用当作新记录。"""

    continuation = continuations.get(continuation_id)
    if continuation is None:
        raise ApplicationLifecycleConflictError("开发 continuation 不存在或已过期。")
    return continuation
