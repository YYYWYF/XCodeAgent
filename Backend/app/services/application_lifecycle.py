"""应用生命周期状态机与原子持久化服务。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.domain.application_lifecycle import (
    ApplicationIdentity,
    ApplicationInitialization,
    ArtifactReference,
    ApplicationLifecycleError,
    ApplicationLifecycleStage,
    ApplicationLifecycle,
    ApplicationLifecycleStatus,
    ExecutionResourceClaim,
    ExecutionResourceLock,
    ExecutionResourceLocks,
    ExecutionResourceReason,
    ExecutionResourceRole,
    ExecutionResourceType,
    PendingInteraction,
    PendingInteractionType,
    WorkbenchExecution,
    WorkbenchExecutionStatus,
    utc_now,
)
from app.services.application_template_generation import (
    ApplicationTemplateGenerationError,
    validate_application_template_generation,
)


APPLICATION_LIFECYCLE_RELATIVE_PATH = Path(".xcodeagent/application-lifecycle.json")
_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


class ApplicationLifecyclePersistenceError(ValueError):
    """表示生命周期状态无法安全读取或更新。"""


class ApplicationLifecycleCorruptedError(ApplicationLifecyclePersistenceError):
    """表示现有状态文件损坏或不符合当前 schema。"""


class ApplicationLifecycleConflictError(ApplicationLifecyclePersistenceError):
    """表示 revision、交互 ID 或状态转换发生冲突。"""


ALLOWED_STAGE_TRANSITIONS: dict[ApplicationLifecycleStage, set[ApplicationLifecycleStage]] = {
    ApplicationLifecycleStage.COLLECTING_REQUIREMENT: {ApplicationLifecycleStage.ANALYZING_REQUIREMENT},
    ApplicationLifecycleStage.ANALYZING_REQUIREMENT: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
    },
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION: {
        ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
    },
    ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
    },
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
    },
    ApplicationLifecycleStage.GENERATING_UI_DESIGNS: {
        ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
        ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
    },
    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION: {
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
        ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
    },
    ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY: {
        ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
    },
    ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN: {
        ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
    },
    ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION: {
        ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
    },
    ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES: {
        ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
        ApplicationLifecycleStage.READY_FOR_WORKBENCH,
    },
}

APPLICATION_PLANNING_REVISION_STAGES = {
    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
    ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
    ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
    ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
}

APPLICATION_PLANNING_ACTIVE_STAGES = {
    ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
    ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
    ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
    ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
    ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
    ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
}


def application_lifecycle_path(workspace: str | Path) -> Path:
    """返回指定工作区的生命周期状态文件路径。"""

    return Path(workspace).expanduser().resolve() / APPLICATION_LIFECYCLE_RELATIVE_PATH


def create_application_lifecycle(
    *,
    application_id: str,
    application_name: str,
    initialization_thread_id: str | None = None,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """创建处于收集需求阶段的首个生命周期快照。"""

    return ApplicationLifecycle(
        application=ApplicationIdentity(id=application_id, name=application_name),
        updatedAt=utc_now(),
        revision=1,
        initialization=ApplicationInitialization(
            stage=ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
            status=ApplicationLifecycleStatus.PENDING,
            threadId=initialization_thread_id,
        ),
        activeRunId=active_run_id,
    )


def load_application_lifecycle(workspace: str | Path) -> ApplicationLifecycle | None:
    """读取并严格校验状态文件；缺失时返回空，损坏时显式失败。"""

    path = application_lifecycle_path(workspace)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationLifecycleCorruptedError(f"生命周期状态文件损坏：{path}") from exc
    if not isinstance(raw, dict):
        raise ApplicationLifecycleCorruptedError("生命周期状态文件根节点必须是对象。")
    try:
        return ApplicationLifecycle.model_validate(raw)
    except ValidationError as exc:
        raise ApplicationLifecycleCorruptedError("生命周期状态文件未通过 schema 校验。") from exc


def write_application_lifecycle(
    workspace: str | Path,
    state: ApplicationLifecycle,
    *,
    expected_revision: int | None = None,
) -> ApplicationLifecycle:
    """用同目录临时文件、fsync 和原子替换写入生命周期快照。"""

    path = application_lifecycle_path(workspace)
    lock = _application_lifecycle_lock(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if expected_revision is not None:
            current = load_application_lifecycle(workspace)
            current_revision = current.revision if current else 0
            if current_revision != expected_revision:
                raise ApplicationLifecycleConflictError(
                    f"生命周期 revision 冲突：期望 {expected_revision}，实际 {current_revision}。"
                )
        payload = state.model_dump_json(by_alias=True, indent=2) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            _fsync_directory(path.parent)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
    return state


def _application_lifecycle_lock(path: Path) -> threading.RLock:
    """返回进程内按状态文件隔离的可重入事务锁。"""

    key = str(path)
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_LOCKS[key] = lock
        return lock


def clear_application_lifecycle_lock(workspace: str | Path) -> bool:
    """在应用全部运行停止后移除该工作区的生命周期互斥锁缓存。"""

    key = str(application_lifecycle_path(workspace))
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.pop(key, None) is not None


def ensure_application_lifecycle(
    workspace: str | Path,
    *,
    application_id: str,
    application_name: str,
    initialization_thread_id: str | None = None,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """读取现有权威状态，缺失时以 CAS 方式创建首版。"""

    current = load_application_lifecycle(workspace)
    if current is not None:
        return current
    created = create_application_lifecycle(
        application_id=application_id,
        application_name=application_name,
        initialization_thread_id=initialization_thread_id,
        active_run_id=active_run_id,
    )
    return write_application_lifecycle(workspace, created, expected_revision=0)


def persist_application_lifecycle_transition(
    workspace: str | Path,
    *,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
    active_run_id: str | None = None,
    error: ApplicationLifecycleError | None = None,
) -> ApplicationLifecycle:
    """从文件权威状态执行幂等转换并以 revision CAS 落盘。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生命周期状态尚未初始化。")
    if _transition_already_applied(
        current,
        stage=stage,
        status=status,
        active_run_id=active_run_id,
        error=error,
    ):
        return current
    updated = transition_application_lifecycle(
        current,
        stage=stage,
        status=status,
        active_run_id=active_run_id,
        error=error,
    )
    return write_application_lifecycle(
        workspace,
        updated,
        expected_revision=current.revision,
    )


def restart_application_planning_lifecycle(
    workspace: str | Path,
    *,
    stage: ApplicationLifecycleStage,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """为设计产物修订把创建生命周期回退到指定规划入口阶段。"""

    if stage not in APPLICATION_PLANNING_REVISION_STAGES:
        raise ApplicationLifecycleConflictError(
            f"设计修订不能回退到阶段：{stage.value}"
        )
    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生命周期状态尚未初始化。")
    if current.initialization.stage not in {
        *APPLICATION_PLANNING_ACTIVE_STAGES,
        ApplicationLifecycleStage.READY_FOR_WORKBENCH,
    }:
        raise ApplicationLifecycleConflictError(
            "只有原创建规划或已进入工作台的应用可以修订设计产物，当前阶段为 "
            f"{current.initialization.stage.value}。"
        )
    if not str(current.initialization.thread_id or "").strip():
        raise ApplicationLifecycleConflictError("设计修订缺少原 application planning thread。")
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "initialization": ApplicationInitialization(
                stage=stage,
                status=ApplicationLifecycleStatus.RUNNING,
                threadId=current.initialization.thread_id,
            ),
            "active_run_id": active_run_id or current.active_run_id,
            "error": None,
        }
    )
    return write_application_lifecycle(
        workspace,
        updated,
        expected_revision=current.revision,
    )


def start_workbench_execution(
    workspace: str | Path,
    *,
    scope: str,
    target_id: str,
    page_id: str | None,
    thread_id: str,
    run_id: str,
    phase: str,
    replaces_run_id: str | None = None,
    resource_claims: list[ExecutionResourceClaim] | None = None,
    development_continuation_consume: dict[str, str] | None = None,
) -> ApplicationLifecycle:
    """原子登记计划执行及全部资源锁，并保持初始化完成状态不变。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        if current is None:
            raise ApplicationLifecycleConflictError("进入计划执行模式前必须先创建生命周期状态。")
        if current.initialization.stage != ApplicationLifecycleStage.READY_FOR_WORKBENCH:
            raise ApplicationLifecycleConflictError(
                "应用尚未完成创建规划，当前阶段 "
                f"{current.initialization.stage.value} 不能启动工作台计划执行。"
            )
        if development_continuation_consume is not None:
            # 同一把生命周期锁内复验 token，并把消费状态与 execution 原子写入。
            # 请求解析、模型校验或写盘失败都不能单独烧掉一次性续接凭据。
            from app.services.development_continuation import validate_development_continuation

            continuation = validate_development_continuation(
                workspace,
                continuation_id=development_continuation_consume["id"],
                token=development_continuation_consume["token"],
                thread_id=thread_id,
                lifecycle=current,
            )
            source = current.active_executions[continuation.source_run_id]
            if replaces_run_id != source.run_id or scope != source.scope or target_id != source.target_id:
                raise ApplicationLifecycleConflictError("续接运行必须接替原开发目标。")
            current = current.model_copy(update={"development_continuations": {
                **current.development_continuations,
                continuation.id: continuation.model_copy(update={
                    "status": "consumed", "token_sha256": None, "consumed_at": utc_now(),
                }),
            }})
        resource_locks = current.resource_locks
        transferred_claims: list[ExecutionResourceClaim] = []
        if replaces_run_id and replaces_run_id in current.active_executions:
            transferred_claims = _resource_claims_for_run(resource_locks, replaces_run_id)
            executions = dict(current.active_executions)
            executions.pop(replaces_run_id, None)
            resource_locks = _resource_locks_without_run(resource_locks, replaces_run_id)
            current = current.model_copy(
                update={
                    "active_executions": executions,
                    "resource_locks": resource_locks,
                }
            )
        claims = _deduplicated_resource_claims(
            [
                *(resource_claims or [_primary_resource_claim(scope, target_id)]),
                *transferred_claims,
            ]
        )
        acquired_at = utc_now()
        next_locks = _resource_locks_with_claims(
            resource_locks,
            claims=claims,
            run_id=run_id,
            owner_page_id=page_id,
            acquired_at=acquired_at,
        )
        return _persist_workbench_execution_snapshot(
            workspace,
            current=current,
            execution=WorkbenchExecution(
                scope=scope,
                targetId=target_id,
                pageId=page_id,
                threadId=thread_id,
                runId=run_id,
                phase=phase,
                status=WorkbenchExecutionStatus.RUNNING,
                resourceKeys=[_resource_claim_key(claim) for claim in claims],
                startedAt=acquired_at,
                updatedAt=acquired_at,
            ),
            resource_locks=next_locks,
        )


def expand_workbench_execution_resources(
    workspace: str | Path,
    *,
    run_id: str,
    resource_claims: list[ExecutionResourceClaim],
) -> ApplicationLifecycle:
    """在用户确认修复扩展后记录资源集合，不用既有登记阻断执行。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        execution = current.active_executions.get(run_id) if current else None
        if current is None or execution is None:
            raise ApplicationLifecycleConflictError("当前没有可扩展资源范围的工作台计划执行。")
        additions = [
            claim.model_copy(
                update={
                    "role": ExecutionResourceRole.DEPENDENCY,
                    "reason": ExecutionResourceReason.REPAIR_EXPANSION,
                }
            )
            for claim in resource_claims
            if _resource_claim_key(claim) not in execution.resource_keys
        ]
        if not additions:
            return current
        next_execution = execution.model_copy(
            update={
                "resource_keys": [
                    *execution.resource_keys,
                    *(_resource_claim_key(claim) for claim in additions),
                ],
                "updated_at": utc_now(),
            }
        )
        return _persist_workbench_execution_snapshot(
            workspace,
            current=current,
            execution=next_execution,
            resource_locks=_resource_locks_with_claims(
                current.resource_locks,
                claims=additions,
                run_id=run_id,
                owner_page_id=execution.page_id,
                acquired_at=utc_now(),
            ),
        )


def update_workbench_execution(
    workspace: str | Path,
    *,
    run_id: str,
    phase: str,
    status: WorkbenchExecutionStatus,
    pending_type: PendingInteractionType | None = None,
    pending_payload: dict[str, Any] | None = None,
    error: ApplicationLifecycleError | None = None,
) -> ApplicationLifecycle:
    """在节点完成、等待用户、失败或停止边界更新当前计划执行快照。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        active = current.active_executions.get(run_id) if current else None
        if current is None or active is None:
            raise ApplicationLifecycleConflictError("当前没有可更新的工作台计划执行。")
        next_revision = current.revision + 1
        pending = (
            _pending_interaction(
                current,
                interaction_type=pending_type,
                revision=next_revision,
                payload=pending_payload or {},
                artifact_refs=[],
            )
            if pending_type
            else None
        )
        next_execution = active.model_copy(
            update={
                "phase": phase,
                "status": status,
                "pending_interaction": pending,
                "error": error,
                "updated_at": utc_now(),
            }
        )
        return _persist_workbench_execution_snapshot(
            workspace,
            current=current,
            execution=next_execution,
            resource_locks=current.resource_locks,
        )


def complete_workbench_execution(
    workspace: str | Path,
    *,
    run_id: str,
    phase: str = "finalize_project",
) -> ApplicationLifecycle:
    """完成当前计划执行并释放资源锁，不改变已经完成的应用初始化状态。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        active = current.active_executions.get(run_id) if current else None
        if current is None or active is None:
            raise ApplicationLifecycleConflictError("当前没有可完成的工作台计划执行。")
        remaining = dict(current.active_executions)
        remaining.pop(run_id, None)
        return _persist_workbench_execution_removal(
            workspace,
            current=current,
            executions=remaining,
            resource_locks=_resource_locks_without_run(current.resource_locks, run_id),
        )


def end_workbench_execution(
    workspace: str | Path,
    *,
    run_id: str,
    missing_ok: bool = False,
) -> ApplicationLifecycle:
    """终止当前计划并释放工作区输入锁，同时保留最后执行快照供审计。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        if current is None:
            raise ApplicationLifecycleConflictError("生命周期状态尚未初始化。")
        if run_id not in current.active_executions:
            if missing_ok:
                # discard 边界可能与节点自身的 close 并发到达；已经收口时
                # 返回当前快照即可，不能再次递增 revision 或误删其他运行。
                return current
            raise ApplicationLifecycleConflictError("当前没有可结束的工作台计划执行。")
        ending_execution = current.active_executions[run_id]
        remaining = dict(current.active_executions)
        remaining.pop(run_id, None)
        return _persist_workbench_execution_removal(
            workspace,
            current=current,
            executions=remaining,
            resource_locks=_resource_locks_without_run(current.resource_locks, run_id),
            clear_active_formal_revision=execution_belongs_to_active_revision(
                current,
                ending_execution,
            ),
        )


def stop_workbench_execution(workspace: str | Path, *, run_id: str) -> ApplicationLifecycle:
    """在没有活动网络 Run 时把等待中的计划显式标记为可恢复停止。"""

    current = load_application_lifecycle(workspace)
    active = current.active_executions.get(run_id) if current else None
    if current is None or active is None:
        raise ApplicationLifecycleConflictError("当前没有可停止的工作台计划执行。")
    return update_workbench_execution(
        workspace,
        run_id=run_id,
        phase=active.phase,
        status=WorkbenchExecutionStatus.STOPPED,
    )


def persist_workbench_interaction_submission(
    workspace: str | Path,
    *,
    run_id: str,
    interaction_id: str,
    based_on_revision: int,
) -> ApplicationLifecycle:
    """按运行隔离地提交工作台交互令牌，不影响其他页面的待确认状态。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        execution = current.active_executions.get(run_id) if current else None
        pending = execution.pending_interaction if execution else None
        if current is None or execution is None or pending is None:
            raise ApplicationLifecycleConflictError("当前运行没有可提交的待处理交互。")
        if pending.id != interaction_id or pending.based_on_revision != based_on_revision:
            raise ApplicationLifecycleConflictError("待处理交互已过期或不属于当前页面运行。")
        if pending.submitted_at is not None:
            return current
        next_revision = current.revision + 1
        submitted = pending.model_copy(
            update={"submitted_at": utc_now(), "based_on_revision": next_revision}
        )
        executions = dict(current.active_executions)
        executions[run_id] = execution.model_copy(
            update={"pending_interaction": submitted, "updated_at": utc_now()}
        )
        updated = current.model_copy(
            update={
                "updated_at": utc_now(),
                "revision": next_revision,
                "active_executions": executions,
            }
        )
        return write_application_lifecycle(
            workspace,
            updated,
            expected_revision=current.revision,
        )


def _persist_workbench_execution_snapshot(
    workspace: str | Path,
    *,
    current: ApplicationLifecycle,
    execution: WorkbenchExecution,
    resource_locks: ExecutionResourceLocks,
) -> ApplicationLifecycle:
    """原子提交工作台执行快照，同时保持创建生命周期已经完成。"""

    executions = dict(current.active_executions)
    executions[execution.run_id] = execution
    next_revision = current.revision + 1
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
                threadId=current.initialization.thread_id,
            ),
            "active_run_id": execution.run_id,
            "active_executions": executions,
            "resource_locks": resource_locks,
        }
    )
    return write_application_lifecycle(
        workspace,
        updated,
        expected_revision=current.revision,
    )


def _persist_workbench_execution_removal(
    workspace: str | Path,
    *,
    current: ApplicationLifecycle,
    executions: dict[str, WorkbenchExecution],
    resource_locks: ExecutionResourceLocks,
    clear_active_formal_revision: bool = False,
) -> ApplicationLifecycle:
    """原子移除指定运行，并按明确结束语义释放其 formal revision。"""

    next_revision = current.revision + 1
    latest = max(executions.values(), key=lambda item: item.updated_at) if executions else None
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
                threadId=current.initialization.thread_id,
            ),
            "active_run_id": latest.run_id if latest else None,
            "active_executions": executions,
            "resource_locks": resource_locks,
            **(
                {"active_formal_revision": None}
                if clear_active_formal_revision
                else {}
            ),
        }
    )
    return write_application_lifecycle(workspace, updated, expected_revision=current.revision)


def _primary_resource_claim(scope: str, target_id: str) -> ExecutionResourceClaim:
    """为未提供依赖集合的旧调用生成安全的主目标声明。"""

    resource_type = {
        "page": ExecutionResourceType.PAGE,
        "data_source": ExecutionResourceType.DATA_SOURCE,
        "endpoint": ExecutionResourceType.ENDPOINT,
    }.get(scope, ExecutionResourceType.APPLICATION)
    return ExecutionResourceClaim(
        type=resource_type,
        targetId="application" if resource_type == ExecutionResourceType.APPLICATION else target_id,
        role=ExecutionResourceRole.PRIMARY,
        reason=ExecutionResourceReason.PRIMARY_TARGET,
    )


def execution_belongs_to_active_revision(
    lifecycle: ApplicationLifecycle,
    execution: WorkbenchExecution,
) -> bool:
    """判断用户明确结束的运行是否承载当前唯一 formal revision。"""

    active = lifecycle.active_formal_revision
    if active is None:
        return False
    # 工作台草稿节点只服务 formal revision，无需再依赖易变化的 target 投影。
    if execution.phase == "application_revision":
        return True
    target = active.target
    if target.type == "application":
        return execution.scope == "application" and execution.target_id == "application"
    if target.type == "page":
        page_id = str(target.page_id or "")
        return execution.scope == "page" and (
            execution.target_id == page_id or execution.page_id == page_id
        )
    endpoint_id = str(target.endpoint_id or "")
    return execution.scope == "endpoint" and execution.target_id == endpoint_id


def _resource_claim_key(claim: ExecutionResourceClaim) -> str:
    """生成生命周期和进程内租约共享的稳定资源键。"""

    return f"{claim.type.value}:{claim.target_id}"


def _deduplicated_resource_claims(
    claims: list[ExecutionResourceClaim],
) -> list[ExecutionResourceClaim]:
    """按稳定键去重，并优先保留新运行重新解析出的声明信息。"""

    result: dict[str, ExecutionResourceClaim] = {}
    for claim in claims:
        result.setdefault(_resource_claim_key(claim), claim)
    return list(result.values())


def _resource_claims_for_run(
    locks: ExecutionResourceLocks,
    run_id: str,
) -> list[ExecutionResourceClaim]:
    """从旧运行持有的锁重建声明，以便恢复时原子转移完整资源集合。"""

    claims: list[ExecutionResourceClaim] = []
    groups = (
        (ExecutionResourceType.PAGE, locks.pages),
        (ExecutionResourceType.ENDPOINT, locks.endpoints),
        (ExecutionResourceType.API_CONTRACT, locks.api_contracts),
        (ExecutionResourceType.DATA_SOURCE, locks.data_sources),
    )
    if locks.application is not None and locks.application.run_id == run_id:
        claims.append(
            ExecutionResourceClaim(
                type=ExecutionResourceType.APPLICATION,
                targetId="application",
                role=locks.application.role,
                reason=locks.application.reason,
            )
        )
    for resource_type, group in groups:
        for target_id, lock in group.items():
            if lock.run_id == run_id:
                claims.append(
                    ExecutionResourceClaim(
                        type=resource_type,
                        targetId=target_id,
                        role=lock.role,
                        reason=lock.reason,
                    )
                )
    return claims


def _resource_locks_with_claims(
    locks: ExecutionResourceLocks,
    *,
    claims: list[ExecutionResourceClaim],
    run_id: str,
    owner_page_id: str | None,
    acquired_at: Any,
) -> ExecutionResourceLocks:
    """把资源声明写入登记表；同键以最近一次运行记录为准。"""

    application = locks.application
    pages = dict(locks.pages)
    endpoints = dict(locks.endpoints)
    api_contracts = dict(locks.api_contracts)
    data_sources = dict(locks.data_sources)
    for claim in claims:
        lock = ExecutionResourceLock(
            runId=run_id,
            ownerPageId=owner_page_id,
            role=claim.role,
            reason=claim.reason,
            acquiredAt=acquired_at,
        )
        if claim.type == ExecutionResourceType.APPLICATION:
            application = lock
        elif claim.type == ExecutionResourceType.PAGE:
            pages[claim.target_id] = lock
        elif claim.type == ExecutionResourceType.ENDPOINT:
            endpoints[claim.target_id] = lock
        elif claim.type == ExecutionResourceType.API_CONTRACT:
            api_contracts[claim.target_id] = lock
        else:
            data_sources[claim.target_id] = lock
    return ExecutionResourceLocks(
        application=application,
        pages=pages,
        endpoints=endpoints,
        apiContracts=api_contracts,
        dataSources=data_sources,
    )


def _resource_locks_without_run(
    locks: ExecutionResourceLocks,
    run_id: str,
) -> ExecutionResourceLocks:
    """一次性移除某个运行拥有的全部资源锁，不触碰其他并行页面。"""

    return ExecutionResourceLocks(
        application=(
            None if locks.application and locks.application.run_id == run_id else locks.application
        ),
        pages={key: value for key, value in locks.pages.items() if value.run_id != run_id},
        endpoints={
            key: value for key, value in locks.endpoints.items() if value.run_id != run_id
        },
        apiContracts={
            key: value for key, value in locks.api_contracts.items() if value.run_id != run_id
        },
        dataSources={
            key: value for key, value in locks.data_sources.items() if value.run_id != run_id
        },
    )


def application_lifecycle_payload(state: ApplicationLifecycle) -> dict[str, Any]:
    """生成可安全放入 Graph State 和 AG-UI 快照的生命周期对象。"""

    payload = state.model_dump(mode="json", by_alias=True)
    # continuation 的原请求和 token 哈希只属于服务端控制面；公开运行结果通过
    # developmentContinuation 单独投射当前可执行动作，生命周期快照不暴露内部登记表。
    payload.pop("developmentContinuations", None)
    return payload


def transition_application_lifecycle(
    state: ApplicationLifecycle,
    *,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
    active_run_id: str | None = None,
    error: ApplicationLifecycleError | None = None,
) -> ApplicationLifecycle:
    """校验状态机边并生成 revision 递增的新快照。"""

    if (
        stage != state.initialization.stage
        and stage not in ALLOWED_STAGE_TRANSITIONS[state.initialization.stage]
    ):
        raise ApplicationLifecycleConflictError(
            f"非法应用初始化转换：{state.initialization.stage.value} -> {stage.value}"
        )
    next_revision = state.revision + 1
    return state.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "initialization": ApplicationInitialization(
                stage=stage,
                status=status,
                threadId=state.initialization.thread_id,
            ),
            "active_run_id": active_run_id or state.active_run_id,
            "error": error,
        }
    )


def complete_application_template_generation(
    workspace: str | Path,
    *,
    succeeded: bool,
    error_message: str | None = None,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """校验正式产物、manifest 和真实文件后把模板生成结果落为 ready 或失败。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生成应用模板文件前必须先创建生命周期状态。")
    if (
        current.initialization.stage
        != ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES
    ):
        raise ApplicationLifecycleConflictError(
            f"当前阶段 {current.initialization.stage.value} 不能提交应用模板文件生成结果。"
        )
    requirement_status = _artifact_confirmation_status(
        Path(workspace) / ".xcodeagent/specs/requirement-spec.json"
    )
    product_plan_status = _artifact_confirmation_status(
        Path(workspace) / ".xcodeagent/plans/product-plan.json"
    )
    ui_design_status = _artifact_confirmation_status(
        Path(workspace) / ".xcodeagent/specs/ui-designs.json"
    )
    technical_plan_status = _artifact_confirmation_status(
        Path(workspace) / ".xcodeagent/plans/technical-plan.json",
        expected_artifact_type="technical-plan",
    )
    # UI 阶段可以是用户明确跳过，其余正式产物仍必须处于 confirmed。
    artifacts_confirmed = (
        requirement_status == "confirmed"
        and product_plan_status == "confirmed"
        and ui_design_status in {"confirmed", "skipped"}
        and technical_plan_status == "confirmed"
    )
    if succeeded and not artifacts_confirmed:
        succeeded = False
        error_message = "需求、产品、技术正式产物必须确认，UI 设计稿必须确认或明确跳过，才能进入工作台。"
    if succeeded:
        try:
            validate_application_template_generation(workspace)
        except ApplicationTemplateGenerationError as exc:
            succeeded = False
            error_message = str(exc)
    if succeeded:
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            status=ApplicationLifecycleStatus.COMPLETED,
            active_run_id=active_run_id,
        )
    return persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
        status=ApplicationLifecycleStatus.FAILED,
        active_run_id=active_run_id,
        error=ApplicationLifecycleError(
            code="application_template_generation_failed",
            message=(error_message or "应用模板文件生成失败。")[:2048],
            recoverable=False,
            occurredAt=utc_now(),
        ),
    )


def complete_workspace_bootstrap(
    workspace: str | Path,
    *,
    succeeded: bool,
    error_message: str | None = None,
) -> ApplicationLifecycle:
    """按新 TemplateState Bootstrap 契约提交 ready 或不可恢复失败结果。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("Bootstrap 前必须先创建生命周期状态。")
    if current.initialization.stage != ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES:
        raise ApplicationLifecycleConflictError("当前生命周期不允许提交 Bootstrap 结果。")
    if succeeded:
        try:
            _validate_workspace_bootstrap_readiness(Path(workspace).expanduser().resolve())
        except Exception as exc:
            succeeded = False
            error_message = str(exc)
    if succeeded:
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            status=ApplicationLifecycleStatus.COMPLETED,
        )
    return persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
        status=ApplicationLifecycleStatus.FAILED,
        error=ApplicationLifecycleError(
            code="application_template_generation_failed",
            message=(error_message or "Workspace Bootstrap 失败。")[:2048],
            recoverable=False,
            occurredAt=utc_now(),
        ),
    )


def _validate_workspace_bootstrap_readiness(workspace: Path) -> None:
    """验证新 Bootstrap 的正式产物确认、两个根、Git 和唯一 TemplateState。"""

    from app.services.template_state import load_template_state

    statuses = {
        "RequirementSpec": _artifact_confirmation_status(workspace / ".xcodeagent/specs/requirement-spec.json"),
        "ProductPlan": _artifact_confirmation_status(workspace / ".xcodeagent/plans/product-plan.json"),
        "UiDesign": _artifact_confirmation_status(workspace / ".xcodeagent/specs/ui-designs.json"),
        "TechnicalPlan": _artifact_confirmation_status(
            workspace / ".xcodeagent/plans/technical-plan.json",
            expected_artifact_type="technical-plan",
        ),
    }
    if not (
        statuses["RequirementSpec"] == "confirmed"
        and statuses["ProductPlan"] == "confirmed"
        and statuses["UiDesign"] in {"confirmed", "skipped"}
        and statuses["TechnicalPlan"] == "confirmed"
    ):
        raise ApplicationLifecycleConflictError("正式产物未确认，不能完成 Workspace Bootstrap。")
    for name in ("frontend", "backend", ".git"):
        if not (workspace / name).is_dir() or (workspace / name).is_symlink():
            raise ApplicationLifecycleConflictError(f"Bootstrap 缺少有效 {name}。")
    load_template_state(workspace)


def begin_application_template_generation(
    workspace: str | Path,
    *,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """只允许 TechnicalPlan 确认后的模板生成阶段执行初始化。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生成应用模板文件前必须先创建生命周期状态。")
    if current.initialization.stage == ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES:
        return current
    raise ApplicationLifecycleConflictError(
        "只有用户确认 TechnicalPlan 后才能开始模板初始化；当前阶段为 "
        f"{current.initialization.stage.value}。"
    )


def _pending_interaction(
    state: ApplicationLifecycle,
    *,
    interaction_type: PendingInteractionType,
    revision: int,
    payload: dict[str, Any],
    artifact_refs: list[ArtifactReference],
) -> PendingInteraction:
    """根据应用、类型和 revision 生成稳定且可重放的交互标识。"""

    seed = f"{state.application.id}:{interaction_type.value}:{revision}"
    interaction_id = f"interaction-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"
    return PendingInteraction(
        id=interaction_id,
        type=interaction_type,
        basedOnRevision=revision,
        payload=payload,
        artifactRefs=artifact_refs,
        createdAt=utc_now(),
    )


def _artifact_confirmation_status(
    path: Path,
    *,
    expected_artifact_type: str | None = None,
) -> str | None:
    """严格读取当前正式 JSON 的确认状态和可选产物类型。"""

    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid"
    if expected_artifact_type and (
        not isinstance(value, dict)
        or value.get("artifact_type") != expected_artifact_type
    ):
        return "invalid"
    confirmation_status = value.get("confirmation_status") if isinstance(value, dict) else None
    return confirmation_status if isinstance(confirmation_status, str) else "invalid"


def _transition_already_applied(
    state: ApplicationLifecycle,
    *,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
    active_run_id: str | None,
    error: ApplicationLifecycleError | None,
) -> bool:
    """识别节点重放产生的同义更新，避免无意义 revision 增长。"""

    return (
        state.initialization.stage == stage
        and state.initialization.status == status
        and (not active_run_id or state.active_run_id == active_run_id)
        and ((error is None and state.error is None) or (error is not None and state.error == error))
    )


def _fsync_directory(directory: Path) -> None:
    """同步目录项，确保原子替换在进程崩溃后仍可见。Windows 不支持对目录执行 fsync，直接跳过。"""

    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
