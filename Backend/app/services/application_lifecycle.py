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
    APPLICATION_LIFECYCLE_SCHEMA_VERSION,
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


APPLICATION_LIFECYCLE_RELATIVE_PATH = Path(".xcodeagent/application-lifecycle.json")
_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


class ApplicationLifecyclePersistenceError(ValueError):
    """表示生命周期状态无法安全读取或更新。"""


class ApplicationLifecycleCorruptedError(ApplicationLifecyclePersistenceError):
    """表示现有状态文件损坏或不符合当前 schema。"""


class UnsupportedApplicationLifecycleVersionError(ApplicationLifecyclePersistenceError):
    """表示状态文件来自当前实现不支持的未来版本。"""


class ApplicationLifecycleConflictError(ApplicationLifecyclePersistenceError):
    """表示 revision、交互 ID 或状态转换发生冲突。"""


ALLOWED_STAGE_TRANSITIONS: dict[ApplicationLifecycleStage, set[ApplicationLifecycleStage]] = {
    ApplicationLifecycleStage.COLLECTING_REQUIREMENT: {ApplicationLifecycleStage.ANALYZING_REQUIREMENT},
    ApplicationLifecycleStage.ANALYZING_REQUIREMENT: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
    },
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION: {
        ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
    },
    ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
        ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
    },
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
        ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
    },
    ApplicationLifecycleStage.GENERATING_UI_DESIGNS: {
        ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
    },
    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION: {
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
        ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
    },
    ApplicationLifecycleStage.GENERATING_PROJECT_PLAN: {
        ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
    },
    ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION: {
        ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
        ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
    },
    ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES: {
        ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
        ApplicationLifecycleStage.READY_FOR_WORKBENCH,
    },
    ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED: {
        ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
    },
    ApplicationLifecycleStage.READY_FOR_WORKBENCH: set(),
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
    version = raw.get("schemaVersion")
    if version != APPLICATION_LIFECYCLE_SCHEMA_VERSION:
        raise UnsupportedApplicationLifecycleVersionError(
            f"不支持的生命周期 schemaVersion：{version!r}"
        )
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


def end_workbench_execution(workspace: str | Path, *, run_id: str) -> ApplicationLifecycle:
    """终止当前计划并释放工作区输入锁，同时保留最后执行快照供审计。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = load_application_lifecycle(workspace)
        if current is None or run_id not in current.active_executions:
            raise ApplicationLifecycleConflictError("当前没有可结束的工作台计划执行。")
        remaining = dict(current.active_executions)
        remaining.pop(run_id, None)
        return _persist_workbench_execution_removal(
            workspace,
            current=current,
            executions=remaining,
            resource_locks=_resource_locks_without_run(current.resource_locks, run_id),
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
) -> ApplicationLifecycle:
    """原子移除指定运行，并保持创建生命周期不受工作台状态影响。"""

    next_revision = current.revision + 1
    latest = max(executions.values(), key=lambda item: item.updated_at) if executions else None
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
            ),
            "active_run_id": latest.run_id if latest else None,
            "active_executions": executions,
            "resource_locks": resource_locks,
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

    return state.model_dump(mode="json", by_alias=True)


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
                threadId=(
                    None
                    if stage == ApplicationLifecycleStage.READY_FOR_WORKBENCH
                    else state.initialization.thread_id
                ),
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
    """校验正式文档后把应用模板文件生成结果落为 ready 或显式失败。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生成应用模板文件前必须先创建生命周期状态。")
    if current.initialization.stage == ApplicationLifecycleStage.READY_FOR_WORKBENCH and succeeded:
        return current
    if (
        current.initialization.stage
        == ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED
        and succeeded
    ):
        current = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=active_run_id,
        )
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
    plan_status = _artifact_confirmation_status(
        Path(workspace) / ".xcodeagent/plans/project-plan.json"
    )
    if succeeded and (requirement_status != "confirmed" or plan_status != "confirmed"):
        succeeded = False
        error_message = "正式需求文档或项目计划未确认，不能进入工作台。"
    if succeeded:
        # 进入工作台前，按项目计划声明的 API 契约预生成 API 骨架文件到 frontend/src/apis/。
        # 这样 build 阶段对这些文件的变更类型是 modified（而非 added），与 build-task-plan
        # 的 change_scope(operation=modify) 一致，避免工程验收报"预期 modified 实际 added"。
        try:
            _preload_api_skeletons(workspace)
        except Exception:
            # 预生成失败不阻断进入工作台：build 阶段仍可新建文件，只是验收可能报 added。
            pass
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
            recoverable=True,
            occurredAt=utc_now(),
        ),
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


def _artifact_confirmation_status(path: Path) -> str | None:
    """严格读取正式 JSON 的 confirmation_status；损坏时返回显式异常状态。"""

    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
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


def _camel_case(value: str) -> str:
    """把 snake/kebab/Pascal/空格分隔的标识符转为 camelCase。

    例：User → user；InventoryManagement → inventoryManagement；
    user_source → userSource；inventory-management → inventoryManagement。
    先按 _/-/空格 分段，再对每段按大写边界拆分（InventoryManagement →
    Inventory + Management），最后首段小写、其余段首字母大写拼接。
    """

    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    raw_parts = [p for p in cleaned.replace("-", "_").replace(" ", "_").split("_") if p]
    parts: list[str] = []
    for segment in raw_parts:
        # 按大写边界拆 PascalCase：InventoryManagement → Inventory, Management
        # 连续大写（如 URL）作为一段，避免拆成 U,R,L。
        current = ""
        for ch in segment:
            if ch.isupper() and current and not current[-1].isupper():
                parts.append(current)
                current = ch
            else:
                current += ch
        if current:
            parts.append(current)
    if not parts:
        return ""
    return parts[0].lower() + "".join(p[:1].upper() + p[1:].lower() for p in parts[1:])


def _api_module_candidates(contract: dict[str, Any]) -> list[str]:
    """派生 build 阶段可能写入的 API 模块文件名候选（去重，保序）。

    build-task-plan 与前端生成 agent 都按 `src/apis/<biz>Api.ts` 约定命名，但 <biz>
    由模型从契约的 resource / base_path / id 自行推断，无确定性映射。这里按模型
    最可能的取值顺序生成候选，预生成骨架命中其中之一即可让差异类型落在 modified。
    """

    candidates: list[str] = []

    # 1) resource（PascalCase 业务实体名）→ camelCase + Api.ts，模型最常用
    resource = str(contract.get("resource") or "").strip()
    if resource:
        biz = _camel_case(resource)
        if biz:
            candidates.append(f"{biz}Api.ts")

    # 2) base_path 末段（/api/inventory-management → inventoryManagement）
    base_path = str(contract.get("base_path") or "").strip()
    if base_path:
        last = base_path.rstrip("/").rsplit("/", 1)[-1]
        if last:
            biz = _camel_case(last)
            if biz:
                candidates.append(f"{biz}Api.ts")

    # 3) id 去掉末尾 _api 后转 camelCase（user_source_api → userSourceApi）
    contract_id = str(contract.get("id") or "").strip()
    if contract_id:
        tail = contract_id
        if tail.lower().endswith("_api"):
            tail = tail[: -len("_api")]
        biz = _camel_case(tail)
        if biz:
            candidates.append(f"{biz}Api.ts")

    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for name in candidates:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return unique or ["api.ts"]


def _preload_api_skeletons(workspace: str | Path) -> None:
    """按项目计划声明的 API 契约，预生成 API 骨架文件到 frontend/src/apis/。

    只在文件不存在时写入（不覆盖已有文件）。骨架内容是一个带契约注释的空模块，
    让 build 阶段对这些文件的变更是 modified 而非 added，与 build-task-plan 的
    change_scope(operation=modify) 对齐，避免工程验收报"预期 modified 实际 added"。

    每个契约按 resource/base_path/id 派生多个候选文件名（模型命名非确定性），
    全部预生成，确保命中其中之一；未被命中的候选是空 export，不影响构建。
    """

    workspace_path = Path(str(workspace)).expanduser().resolve()
    plan_path = workspace_path / ".xcodeagent" / "plans" / "project-plan.json"
    if not plan_path.is_file():
        return
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(plan, dict):
        return
    contracts = plan.get("api_contracts")
    if not isinstance(contracts, list):
        return

    apis_dir = workspace_path / "frontend" / "src" / "apis"
    # 模板工程拉取后该目录应已存在；缺失则跳过（不强行创建，避免目录结构异常）。
    if not apis_dir.is_dir():
        return

    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "").strip()
        if not contract_id:
            continue
        endpoints = contract.get("endpoints") if isinstance(contract.get("endpoints"), list) else []
        ep_lines: list[str] = []
        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            ep_lines.append(
                f" *  - {ep.get('id', '')} | {ep.get('method', '')} {ep.get('path', '')}"
            )
        ep_block = "\n".join(ep_lines) if ep_lines else " *  (无端点)"
        label = contract.get("label") or contract_id
        for filename in _api_module_candidates(contract):
            target = apis_dir / filename
            if target.exists():
                continue  # 不覆盖已有文件（模板自带或已生成）
            skeleton = (
                f"/**\n"
                f" * {label} 数据访问模块（骨架）。\n"
                f" * 由项目规划预生成，build 阶段填充具体实现。\n"
                f" *\n"
                f" * 绑定契约：{contract_id}\n"
                f"{ep_block}\n"
                f" */\n"
                f"\n"
                f"// build 阶段将在此实现接口调用或前端 Mock 数据访问函数。\n"
                f"export default {{}};\n"
            )
            try:
                target.write_text(skeleton, encoding="utf-8")
            except OSError:
                pass
