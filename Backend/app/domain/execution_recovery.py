"""Durable Execution Recovery 的独立记录模型。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionRecoveryModel(BaseModel):
    """为恢复记录提供稳定的当前合同与严格字段校验。"""

    model_config = ConfigDict(extra="forbid")


class DurableExecutionRunConflictError(RuntimeError):
    """表示 runId 已经属于另一轮 Durable Execution Attempt。"""

    code = "DURABLE_EXECUTION_RUN_ID_CONFLICT"

    def __init__(
        self,
        *,
        run_id: str,
        existing_status: str,
        existing_thread_id: str,
    ) -> None:
        """保存冲突双方可供协议层使用的稳定身份字段。"""

        self.run_id = run_id
        self.existing_status = existing_status
        self.existing_thread_id = existing_thread_id
        super().__init__(
            "Durable execution runId already exists: "
            f"runId={run_id} existingStatus={existing_status} "
            f"existingThreadId={existing_thread_id}"
        )


class DurableExecutionStatus(StrEnum):
    """定义恢复记录层观察到的执行状态，不参与业务生命周期状态机。"""

    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"


class ExecutionFailureOrigin(StrEnum):
    """定义失败证据来自模型、外部依赖、业务逻辑、不变量还是未知来源。"""

    MODEL_CALL = "model_call"
    EXTERNAL_DEPENDENCY = "external_dependency"
    BUSINESS = "business"
    INVARIANT = "invariant"
    UNKNOWN = "unknown"


class ExecutionFailureEvidence(ExecutionRecoveryModel):
    """保存可供恢复安全判断使用的结构化事实与安全诊断摘要。

    diagnostic_message 是 Backend 脱敏、限长后允许向用户展示的失败摘要，
    不是未经处理的原始异常或完整 provider response。
    """

    origin: ExecutionFailureOrigin
    code: str = Field(min_length=1, max_length=256)
    operation: str | None = Field(default=None, max_length=256)
    dependency: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)
    http_status: int | None = Field(default=None, ge=100, le=599)
    replay_compatible: bool = False
    diagnostic_message: str | None = Field(default=None, max_length=2048)


def execution_failure_sha256(
    failure: ExecutionFailureEvidence | None,
) -> str | None:
    """为失败证据生成稳定摘要，供 RecoveryAttempt 固化 source identity。"""

    if failure is None:
        return None
    # 展示诊断不属于恢复安全身份，避免新增可选字段改变既有 lineage hash。
    identity = {
        "origin": failure.origin.value,
        "code": failure.code,
        "operation": failure.operation,
        "dependency": failure.dependency,
        "provider": failure.provider,
        "model": failure.model,
        "http_status": failure.http_status,
        "replay_compatible": failure.replay_compatible,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ExecutionLeaseStatus(StrEnum):
    """定义持久化执行租约的生命周期状态。"""

    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class WorkflowReentryReason(StrEnum):
    """区分失败重试、中断继续与正式修订三种 Workflow Node 重入来源。"""

    FAILURE_RETRY = "failure_retry"
    INTERRUPTED_CONTINUE = "interrupted_continue"
    REVISION = "revision"


class WorkflowReentryContextAuthorityKind(StrEnum):
    """区分 checkpoint 原语义现场与协调器新建的修订语义现场。"""

    CHECKPOINT = "checkpoint"
    REVISION_CONTEXT = "revision_context"


class NodeEntryBoundary(ExecutionRecoveryModel):
    """索引真实已提交的 Node Entry checkpoint，不复制完整 Graph State。"""

    boundary_id: str = Field(min_length=1, max_length=512)
    source_run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    target_node: str = Field(min_length=1, max_length=256)
    checkpoint_id: str = Field(min_length=1, max_length=512)
    checkpoint_ns: str = Field(default="", max_length=512)
    captured_at: datetime


class WorkflowReentryContextAuthority(ExecutionRecoveryModel):
    """保存重入语义上下文的权威身份，不在 Recovery DB 复制业务 State。"""

    kind: WorkflowReentryContextAuthorityKind
    source_run_id: str | None = Field(default=None, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    target_node: str | None = Field(default=None, max_length=256)
    checkpoint_id: str | None = Field(default=None, max_length=512)
    checkpoint_ns: str = Field(default="", max_length=512)
    revision_context_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_authority_identity(self) -> "WorkflowReentryContextAuthority":
        """确保两类语义 authority 只能携带各自必需的身份字段。"""

        if self.kind is WorkflowReentryContextAuthorityKind.CHECKPOINT:
            if (
                not self.source_run_id
                or not self.thread_id
                or not self.target_node
                or not self.checkpoint_id
                or self.revision_context_sha256
            ):
                raise ValueError("CHECKPOINT context authority 缺少 Node Entry 身份。")
        elif (
            not self.revision_context_sha256
            or self.source_run_id is not None
            or self.thread_id is not None
            or self.target_node is not None
            or self.checkpoint_id is not None
            or self.checkpoint_ns
        ):
            raise ValueError("REVISION_CONTEXT authority 必须只包含修订语义摘要。")
        return self


class WorkflowReentryLifecycleAuthority(ExecutionRecoveryModel):
    """固定调用方已经决定的 lifecycle owner 与 revision。"""

    owner_run_id: str = Field(min_length=1, max_length=512)
    revision: int | None = Field(default=None, ge=0)


class WorkflowReentryPlan(ExecutionRecoveryModel):
    """统一描述从哪个 Node、携带哪份语义上下文重新进入 Workflow。"""

    reason: WorkflowReentryReason
    execution_kind: Literal["application_planning", "workbench"]
    target_node: str = Field(min_length=1, max_length=256)
    thread_id: str = Field(min_length=1, max_length=512)
    source_run_id: str | None = Field(default=None, max_length=512)
    context_authority: WorkflowReentryContextAuthority
    lifecycle_authority: WorkflowReentryLifecycleAuthority
    lineage_parent_run_id: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_reason_contract(self) -> "WorkflowReentryPlan":
        """强制 checkpoint 重入绑定 source lineage，正式修订只接受新语义上下文。"""

        if self.reason in {
            WorkflowReentryReason.FAILURE_RETRY,
            WorkflowReentryReason.INTERRUPTED_CONTINUE,
        }:
            if (
                not self.source_run_id
                or self.lineage_parent_run_id != self.source_run_id
                or self.context_authority.kind
                is not WorkflowReentryContextAuthorityKind.CHECKPOINT
                or self.context_authority.source_run_id != self.source_run_id
                or self.context_authority.thread_id != self.thread_id
                or self.context_authority.target_node != self.target_node
                or self.context_authority.checkpoint_ns != ""
            ):
                raise ValueError(
                    "checkpoint re-entry 必须绑定 source lineage 与 root CHECKPOINT authority。"
                )
        elif (
            self.context_authority.kind
            is not WorkflowReentryContextAuthorityKind.REVISION_CONTEXT
        ):
            raise ValueError("revision re-entry 必须绑定 REVISION_CONTEXT authority。")
        return self


class RecoveryLifecycleOwnershipMode(StrEnum):
    """定义 Native Recovery 在 fork 前如何取得 ApplicationLifecycle ownership。"""

    SOURCE_OWNED = "source_owned"
    PRE_OWNERSHIP = "pre_ownership"


class RecoveryAttemptStatus(StrEnum):
    """定义一次恢复子执行从 claim 到正式启动的 lineage 状态。"""

    PREPARING = "preparing"
    HANDED_OFF = "handed_off"
    FINALIZING = "finalizing"
    STARTED = "started"
    FAILED_PRESTART = "failed_prestart"
    FINALIZATION_FAILED = "finalization_failed"


class RecoveryAttemptAlreadyClaimedError(RuntimeError):
    """表示 source execution 已经存在未终结的恢复分支。"""

    code = "RECOVERY_ALREADY_CLAIMED"

    def __init__(self, *, source_run_id: str, active_run_id: str) -> None:
        """保存冲突 source 与现有 child 的稳定身份。"""

        self.source_run_id = source_run_id
        self.active_run_id = active_run_id
        super().__init__(
            "Recovery source already has an active attempt: "
            f"sourceRunId={source_run_id} activeRunId={active_run_id}"
        )


class RecoveryExecutionError(RuntimeError):
    """表示 Native Recovery 在执行前被明确拒绝或准备失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """保存协议层需要透出的稳定错误码与安全消息。"""

        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


class DurableExecutionRecord(ExecutionRecoveryModel):
    """保存一次 AG-UI Graph 执行的轻量观察索引。"""

    run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    owner_session_id: str | None = Field(default=None, max_length=512)
    workspace: str = Field(min_length=1, max_length=4096)
    project_id: str | None = Field(default=None, max_length=512)
    execution_kind: Literal["application_planning", "workbench"]
    workflow_scope: str | None = Field(default=None, max_length=128)
    first_node: str = Field(min_length=1, max_length=256)
    current_node: str | None = Field(default=None, max_length=256)
    status: DurableExecutionStatus
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    failure: ExecutionFailureEvidence | None = None

    @model_validator(mode="after")
    def validate_failure_status(self) -> "DurableExecutionRecord":
        """确保失败证据不会附着到非 FAILED execution。"""

        if self.status is not DurableExecutionStatus.FAILED and self.failure is not None:
            raise ValueError("只有 FAILED execution 可以保存 failure evidence。")
        return self


class ExecutionLease(ExecutionRecoveryModel):
    """记录当前 Durable Execution 由哪个 Backend 实例持有。"""

    run_id: str = Field(min_length=1, max_length=512)
    owner_backend_instance_id: str = Field(min_length=1, max_length=256)
    owner_pid: int = Field(ge=1)
    status: ExecutionLeaseStatus
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    released_at: datetime | None = None


class RecoveryPlan(ExecutionRecoveryModel):
    """保存一次 checkpoint transaction 所需的最小 durable authority。"""

    source_run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    target_node: str = Field(min_length=1, max_length=256)
    checkpoint_id: str = Field(min_length=1, max_length=512)
    checkpoint_ns: str = Field(default="", max_length=512)
    lifecycle_ownership_mode: RecoveryLifecycleOwnershipMode = (
        RecoveryLifecycleOwnershipMode.SOURCE_OWNED
    )
    lifecycle_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_root_checkpoint(self) -> "RecoveryPlan":
        """限制 Generic Recovery 只使用 root namespace 的单节点 checkpoint。"""

        if self.checkpoint_ns:
            raise ValueError("RecoveryPlan 只允许 root checkpoint namespace。")
        return self


class RecoveryActionKind(StrEnum):
    """定义恢复旅程层可以向用户公开的下一步动作。"""

    CONTINUE_CHECKPOINT = "continue_checkpoint"
    RETRY_FAILED_NODE = "retry_failed_node"
    RECONCILE_STATE = "reconcile_state"
    AWAIT_USER = "await_user"
    NEEDS_ATTENTION = "needs_attention"


class RecoveryIncidentStatus(StrEnum):
    """定义当前恢复事件是否仍有 Backend-authoritative 下一步。"""

    RECOVERABLE = "recoverable"
    AWAITING_USER = "awaiting_user"
    NEEDS_ATTENTION = "needs_attention"


class RecoveryAction(ExecutionRecoveryModel):
    """描述一个不携带内部 checkpoint authority 的可执行恢复动作。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action_id: str = Field(alias="actionId", min_length=1, max_length=512)
    kind: RecoveryActionKind
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2048)
    requires_confirmation: bool = Field(alias="requiresConfirmation", default=False)


class RecoveryActionPlan(ExecutionRecoveryModel):
    """保存统一 Recovery Incident 的当前动作规划，策略由 Backend 决定。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["recovery-action-plan.v1"] = Field(
        default="recovery-action-plan.v1",
        alias="schemaVersion",
    )
    incident_id: str = Field(alias="incidentId", min_length=1, max_length=512)
    source_run_id: str = Field(alias="sourceRunId", min_length=1, max_length=512)
    thread_id: str = Field(alias="threadId", min_length=1, max_length=512)
    execution_kind: Literal["application_planning", "workbench"] = Field(
        alias="executionKind"
    )
    status: RecoveryIncidentStatus
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2048)
    primary_action: RecoveryAction | None = Field(
        default=None,
        alias="primaryAction",
    )
    alternate_actions: list[RecoveryAction] = Field(
        default_factory=list,
        alias="alternateActions",
        max_length=8,
    )
    updated_at: datetime = Field(alias="updatedAt")


class ExecutionRecoveryProjectionCandidate(ExecutionRecoveryModel):
    """定义 lifecycle GET 仅向前端公开的单条中断执行投影。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_run_id: str = Field(alias="sourceRunId", min_length=1, max_length=512)
    owner_session_id: str = Field(alias="ownerSessionId", min_length=1, max_length=512)
    thread_id: str = Field(alias="threadId", min_length=1, max_length=512)
    execution_kind: Literal["application_planning", "workbench"] = Field(
        alias="executionKind"
    )
    workflow_scope: str | None = Field(
        default=None,
        alias="workflowScope",
        max_length=128,
    )
    execution_status: str = Field(alias="executionStatus", min_length=1, max_length=64)
    current_node: str | None = Field(default=None, alias="currentNode", max_length=256)
    availability: Literal["ready", "requires_handler", "blocked", "awaiting_user"]
    can_continue: bool = Field(alias="canContinue")
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2048)
    updated_at: datetime = Field(alias="updatedAt")
    failure_diagnostic: dict[str, Any] | None = Field(
        default=None,
        alias="failureDiagnostic",
    )
    recovery_action_plan: RecoveryActionPlan | None = Field(
        default=None,
        alias="recoveryActionPlan",
    )


class ExecutionRecoveryProjection(ExecutionRecoveryModel):
    """定义 application lifecycle GET 的非持久化恢复投影外层。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["execution-recovery.v1"] = Field(
        default="execution-recovery.v1",
        alias="schemaVersion",
    )
    generated_at: datetime = Field(alias="generatedAt")
    candidates: list[ExecutionRecoveryProjectionCandidate] = Field(default_factory=list)


class RecoveryAttempt(ExecutionRecoveryModel):
    """记录 source execution 到新 execution attempt 的持久化 lineage。"""

    source_run_id: str = Field(min_length=1, max_length=512)
    new_run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    source_lifecycle_revision: int | None = Field(default=None, ge=0)
    source_checkpoint_id: str | None = Field(default=None, max_length=512)
    source_checkpoint_ns: str = Field(default="", max_length=512)
    lifecycle_ownership_mode: RecoveryLifecycleOwnershipMode = (
        RecoveryLifecycleOwnershipMode.SOURCE_OWNED
    )
    status: RecoveryAttemptStatus
    created_at: datetime
    handed_off_at: datetime | None = None
    started_at: datetime | None = None
    failed_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=128)
    source_status: DurableExecutionStatus
    source_failure_sha256: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_source_checkpoint(self) -> "RecoveryAttempt":
        """活动 transaction 必须持有 root checkpoint；历史终态 edge 可缺少旧身份。"""

        if self.source_checkpoint_ns:
            raise ValueError("RecoveryAttempt 只允许 root checkpoint namespace。")
        if self.status in {
            RecoveryAttemptStatus.PREPARING,
            RecoveryAttemptStatus.HANDED_OFF,
            RecoveryAttemptStatus.FINALIZING,
        } and not self.source_checkpoint_id:
            raise ValueError("活动 RecoveryAttempt 必须包含 source checkpoint identity。")
        return self
