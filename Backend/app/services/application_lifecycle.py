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
    ArtifactReference,
    ApplicationLifecycleError,
    ApplicationLifecycleStage,
    ApplicationLifecycle,
    ApplicationLifecycleStatus,
    LifecycleState,
    PendingInteraction,
    PendingInteractionType,
    ProjectIdentity,
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
        ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
    },
    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION: {
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
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
    project_id: str | None = None,
    active_thread_id: str | None = None,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """创建处于收集需求阶段的首个生命周期快照。"""

    return ApplicationLifecycle(
        application=ApplicationIdentity(id=application_id, name=application_name),
        project=ProjectIdentity(id=project_id) if project_id else None,
        updatedAt=utc_now(),
        revision=1,
        lifecycle=LifecycleState(
            stage=ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
            status=ApplicationLifecycleStatus.PENDING,
        ),
        activeThreadId=active_thread_id,
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
    if version != "1.0.0":
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
    project_id: str | None = None,
    active_thread_id: str | None = None,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """读取现有权威状态，缺失时以 CAS 方式创建首版。"""

    current = load_application_lifecycle(workspace)
    if current is not None:
        return repair_misclassified_requirement_clarification(workspace, current)
    created = create_application_lifecycle(
        application_id=application_id,
        application_name=application_name,
        project_id=project_id,
        active_thread_id=active_thread_id,
        active_run_id=active_run_id,
    )
    return write_application_lifecycle(workspace, created, expected_revision=0)


def repair_misclassified_requirement_clarification(
    workspace: str | Path,
    state: ApplicationLifecycle | None = None,
) -> ApplicationLifecycle:
    """纠正早期 v1 把 ask_user_question 错写成需求文档确认的状态。"""

    current = state or load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生命周期状态尚未初始化。")
    pending = current.pending_interaction
    if (
        current.lifecycle.stage != ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION
        or pending is None
        or pending.type != PendingInteractionType.REQUIREMENT_CONFIRMATION
        or pending.payload.get("mode") != "ask_user_question"
    ):
        return current
    repaired = transition_application_lifecycle(
        current,
        stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        status=ApplicationLifecycleStatus.AWAITING_USER,
        pending_type=PendingInteractionType.REQUIREMENT_CLARIFICATION,
        pending_payload=dict(pending.payload),
        active_thread_id=current.active_thread_id,
        active_run_id=current.active_run_id,
    )
    return write_application_lifecycle(
        workspace,
        repaired,
        expected_revision=current.revision,
    )


def persist_application_lifecycle_transition(
    workspace: str | Path,
    *,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
    pending_type: PendingInteractionType | None = None,
    pending_payload: dict[str, Any] | None = None,
    artifact_refs: list[ArtifactReference] | None = None,
    active_thread_id: str | None = None,
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
        pending_type=pending_type,
        active_thread_id=active_thread_id,
        active_run_id=active_run_id,
        error=error,
    ):
        return current
    updated = transition_application_lifecycle(
        current,
        stage=stage,
        status=status,
        pending_type=pending_type,
        pending_payload=pending_payload,
        artifact_refs=artifact_refs,
        active_thread_id=active_thread_id,
        active_run_id=active_run_id,
        error=error,
    )
    return write_application_lifecycle(
        workspace,
        updated,
        expected_revision=current.revision,
    )


def application_lifecycle_payload(state: ApplicationLifecycle) -> dict[str, Any]:
    """生成可安全放入 Graph State 和 AG-UI 快照的生命周期对象。"""

    return state.model_dump(mode="json", by_alias=True)


def transition_application_lifecycle(
    state: ApplicationLifecycle,
    *,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
    pending_type: PendingInteractionType | None = None,
    pending_payload: dict[str, Any] | None = None,
    artifact_refs: list[ArtifactReference] | None = None,
    active_thread_id: str | None = None,
    active_run_id: str | None = None,
    error: ApplicationLifecycleError | None = None,
) -> ApplicationLifecycle:
    """校验状态机边并生成 revision 递增的新快照。"""

    if stage != state.lifecycle.stage and stage not in ALLOWED_STAGE_TRANSITIONS[state.lifecycle.stage]:
        raise ApplicationLifecycleConflictError(
            f"非法生命周期转换：{state.lifecycle.stage.value} -> {stage.value}"
        )
    next_revision = state.revision + 1
    pending = (
        _pending_interaction(
            state,
            interaction_type=pending_type,
            revision=next_revision,
            payload=pending_payload or {},
            artifact_refs=artifact_refs or [],
        )
        if pending_type
        else None
    )
    domain = dict(state.lifecycle.domain)
    if state.pending_interaction is not None and state.pending_interaction.submitted_at is not None:
        domain["lastSubmittedInteraction"] = {
            "id": state.pending_interaction.id,
            "basedOnRevision": state.pending_interaction.based_on_revision,
            "submittedAt": state.pending_interaction.submitted_at.isoformat(),
        }
    return state.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "lifecycle": LifecycleState(
                stage=stage,
                status=status,
                domain=domain,
                extensions=state.lifecycle.extensions,
            ),
            "active_thread_id": active_thread_id or state.active_thread_id,
            "active_run_id": active_run_id or state.active_run_id,
            "pending_interaction": pending,
            "error": error,
        }
    )


def submit_pending_interaction(
    state: ApplicationLifecycle,
    *,
    interaction_id: str,
    based_on_revision: int,
) -> ApplicationLifecycle:
    """以稳定交互 ID 和 revision 幂等记录一次用户提交。"""

    pending = state.pending_interaction
    if pending is None:
        raise ApplicationLifecycleConflictError("当前没有可提交的待处理交互。")
    if pending.id != interaction_id:
        raise ApplicationLifecycleConflictError("待处理交互已过期或不属于当前生命周期 revision。")
    if pending.submitted_at is not None:
        return state
    if pending.based_on_revision != based_on_revision:
        raise ApplicationLifecycleConflictError("待处理交互已过期或不属于当前生命周期 revision。")
    next_revision = state.revision + 1
    submitted = pending.model_copy(
        update={"submitted_at": utc_now(), "based_on_revision": next_revision}
    )
    return state.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "pending_interaction": submitted,
        }
    )


def persist_pending_interaction_submission(
    workspace: str | Path,
    *,
    interaction_id: str,
    based_on_revision: int,
) -> ApplicationLifecycle:
    """提交当前交互，并对已经推进阶段的同一重复请求保持幂等。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生命周期状态尚未初始化。")
    last_submitted = current.lifecycle.domain.get("lastSubmittedInteraction")
    if (
        isinstance(last_submitted, dict)
        and last_submitted.get("id") == interaction_id
    ):
        return current
    submitted = submit_pending_interaction(
        current,
        interaction_id=interaction_id,
        based_on_revision=based_on_revision,
    )
    if submitted is current:
        return current
    return write_application_lifecycle(
        workspace,
        submitted,
        expected_revision=current.revision,
    )


def complete_application_template_generation(
    workspace: str | Path,
    *,
    succeeded: bool,
    error_message: str | None = None,
    active_thread_id: str | None = None,
    active_run_id: str | None = None,
) -> ApplicationLifecycle:
    """校验正式文档后把应用模板文件生成结果落为 ready 或显式失败。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("生成应用模板文件前必须先创建生命周期状态。")
    if current.lifecycle.stage == ApplicationLifecycleStage.READY_FOR_WORKBENCH and succeeded:
        return current
    if (
        current.lifecycle.stage
        == ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED
        and succeeded
    ):
        current = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            status=ApplicationLifecycleStatus.RUNNING,
            active_thread_id=active_thread_id,
            active_run_id=active_run_id,
        )
    if current.lifecycle.stage != ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES:
        raise ApplicationLifecycleConflictError(
            f"当前阶段 {current.lifecycle.stage.value} 不能提交应用模板文件生成结果。"
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
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            status=ApplicationLifecycleStatus.COMPLETED,
            active_thread_id=active_thread_id,
            active_run_id=active_run_id,
        )
    return persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
        status=ApplicationLifecycleStatus.FAILED,
        active_thread_id=active_thread_id,
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
    pending_type: PendingInteractionType | None,
    active_thread_id: str | None,
    active_run_id: str | None,
    error: ApplicationLifecycleError | None,
) -> bool:
    """识别节点重放产生的同义更新，避免无意义 revision 增长。"""

    current_pending_type = (
        state.pending_interaction.type if state.pending_interaction is not None else None
    )
    pending_is_open = (
        state.pending_interaction is not None
        and state.pending_interaction.submitted_at is None
    )
    return (
        state.lifecycle.stage == stage
        and state.lifecycle.status == status
        and current_pending_type == pending_type
        and (pending_type is None or pending_is_open)
        and (not active_thread_id or state.active_thread_id == active_thread_id)
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
