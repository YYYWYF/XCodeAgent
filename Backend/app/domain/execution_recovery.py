"""Durable Execution Recovery 的独立记录模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ExecutionLeaseStatus(StrEnum):
    """定义持久化执行租约的生命周期状态。"""

    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class RecoveryPointKind(StrEnum):
    """区分没有真实 checkpoint 的入口现场和 LangGraph checkpoint 现场。"""

    ENTRY = "entry"
    CHECKPOINT = "checkpoint"


class RecoveryDecision(StrEnum):
    """定义 P0.3A 对一次恢复判断给出的安全决策。"""

    READY_NATIVE = "ready_native"
    REQUIRES_HANDLER = "requires_handler"
    AWAITING_USER = "awaiting_user"
    NOT_RECOVERABLE = "not_recoverable"
    INVALID_RECOVERY_POINT = "invalid_recovery_point"
    STATE_DRIFT = "state_drift"


class RecoveryStrategy(StrEnum):
    """定义恢复结果交给哪一种后续执行策略。"""

    NATIVE_CHECKPOINT = "native_checkpoint"
    HANDLER = "handler"
    NONE = "none"


class RecoveryAttemptStatus(StrEnum):
    """定义一次恢复子执行从 claim 到正式启动的 lineage 状态。"""

    PREPARING = "preparing"
    HANDED_OFF = "handed_off"
    STARTED = "started"
    FAILED_PRESTART = "failed_prestart"


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

    def __init__(self, code: str, message: str) -> None:
        """保存协议层需要透出的稳定错误码与安全消息。"""

        self.code = code
        super().__init__(message)


class DurableExecutionRecord(ExecutionRecoveryModel):
    """保存一次 AG-UI Graph 执行的轻量观察索引。"""

    run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    workspace: str = Field(min_length=1, max_length=4096)
    project_id: str | None = Field(default=None, max_length=512)
    execution_kind: Literal["application_planning", "workbench"]
    workflow_scope: str | None = Field(default=None, max_length=128)
    first_node: str = Field(min_length=1, max_length=256)
    current_node: str | None = Field(default=None, max_length=256)
    status: DurableExecutionStatus
    last_recovery_point_id: str | None = Field(default=None, max_length=512)
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None


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


class RecoveryPoint(ExecutionRecoveryModel):
    """保存一个可供未来恢复协调器检索的 checkpoint 边界索引。"""

    recovery_point_id: str = Field(min_length=1, max_length=512)
    run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    kind: RecoveryPointKind
    checkpoint_id: str | None = Field(default=None, max_length=512)
    checkpoint_ns: str = Field(default="", max_length=512)
    graph_node: str | None = Field(default=None, max_length=256)
    completed_node: str | None = Field(default=None, max_length=256)
    next_nodes: list[str] = Field(default_factory=list, max_length=256)
    phase: str | None = Field(default=None, max_length=256)
    state_status: str | None = Field(default=None, max_length=128)
    lifecycle_revision: int | None = Field(default=None, ge=0)
    workspace_revision: str | None = Field(default=None, max_length=512)
    workspace_snapshot_hash: str | None = Field(default=None, max_length=512)
    replay_safety: Literal["unassessed"] = "unassessed"
    captured_at: datetime


class RecoveryPlan(ExecutionRecoveryModel):
    """保存只读恢复协调结果，不复制完整 Graph State 或业务产物。"""

    source_run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(default="", max_length=512)
    decision: RecoveryDecision
    strategy: RecoveryStrategy
    recovery_point_id: str | None = Field(default=None, max_length=512)
    checkpoint_id: str | None = Field(default=None, max_length=512)
    checkpoint_ns: str = Field(default="", max_length=512)
    next_nodes: list[str] = Field(default_factory=list, max_length=256)
    reason_code: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2048)
    lifecycle_revision: int | None = Field(default=None, ge=0)
    workspace_revision: str | None = Field(default=None, max_length=512)
    workspace_snapshot_hash: str | None = Field(default=None, max_length=512)


class RecoveryAttempt(ExecutionRecoveryModel):
    """记录 source execution 到新 execution attempt 的持久化 lineage。"""

    source_run_id: str = Field(min_length=1, max_length=512)
    new_run_id: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    source_recovery_point_id: str = Field(min_length=1, max_length=512)
    source_checkpoint_id: str = Field(min_length=1, max_length=512)
    source_checkpoint_ns: str = Field(default="", max_length=512)
    replay_checkpoint_id: str | None = Field(default=None, max_length=512)
    replay_checkpoint_ns: str = Field(default="", max_length=512)
    strategy: RecoveryStrategy
    status: RecoveryAttemptStatus
    created_at: datetime
    handed_off_at: datetime | None = None
    started_at: datetime | None = None
    failed_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=128)
