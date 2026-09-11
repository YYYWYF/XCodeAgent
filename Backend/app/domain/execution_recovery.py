"""Durable Execution Recovery 的独立记录模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionRecoveryModel(BaseModel):
    """为恢复记录提供稳定的当前合同与严格字段校验。"""

    model_config = ConfigDict(extra="forbid")


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
