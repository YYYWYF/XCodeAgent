"""工作区级应用生命周期状态模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


APPLICATION_LIFECYCLE_SCHEMA_VERSION = "1.2.0"


class ApplicationLifecycleStage(StrEnum):
    """定义用户可见的应用开发业务阶段。"""

    COLLECTING_REQUIREMENT = "collecting_requirement"
    ANALYZING_REQUIREMENT = "analyzing_requirement"
    AWAITING_REQUIREMENT_CLARIFICATION = "awaiting_requirement_clarification"
    GENERATING_REQUIREMENT_SPEC = "generating_requirement_spec"
    AWAITING_REQUIREMENT_CONFIRMATION = "awaiting_requirement_confirmation"
    GENERATING_PROJECT_PLAN = "generating_project_plan"
    AWAITING_PROJECT_PLAN_CONFIRMATION = "awaiting_project_plan_confirmation"
    GENERATING_APPLICATION_TEMPLATE_FILES = "generating_application_template_files"
    APPLICATION_TEMPLATE_GENERATION_FAILED = "application_template_generation_failed"
    READY_FOR_WORKBENCH = "ready_for_workbench"


class ApplicationLifecycleStatus(StrEnum):
    """定义阶段的通用执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STOPPING = "stopping"
    STOPPED = "stopped"


class PendingInteractionType(StrEnum):
    """定义可跨会话恢复的用户交互类型。"""

    REQUIREMENT_CLARIFICATION = "requirement_clarification"
    REQUIREMENT_CONFIRMATION = "requirement_confirmation"
    PROJECT_PLAN_CONFIRMATION = "project_plan_confirmation"
    PAGE_DESIGN_CONFIRMATION = "page_design_confirmation"
    TASK_PLAN_CONFIRMATION = "task_plan_confirmation"
    IMPACT_CONFIRMATION = "impact_confirmation"
    PAGE_ACCEPTANCE = "page_acceptance"
    APPLICATION_ACCEPTANCE = "application_acceptance"
    AGENT_APPROVAL = "agent_approval"
    REPAIR_SCOPE_CONFIRMATION = "repair_scope_confirmation"
    PLAN_ADJUSTMENT = "plan_adjustment"


class WorkbenchExecutionStatus(StrEnum):
    """定义工作台计划执行控制面的可恢复状态。"""

    RUNNING = "running"
    STOPPING = "stopping"
    AWAITING_USER = "awaiting_user"
    FAILED = "failed"
    STOPPED = "stopped"
    COMPLETED = "completed"


class ExecutionResourceType(StrEnum):
    """定义计划执行可独占的稳定业务资源类型。"""

    APPLICATION = "application"
    PAGE = "page"
    API_CONTRACT = "api_contract"
    DATA_SOURCE = "data_source"


class ExecutionResourceRole(StrEnum):
    """区分执行主目标与由计划依赖或修复扩展得到的资源。"""

    PRIMARY = "primary"
    DEPENDENCY = "dependency"


class ExecutionResourceReason(StrEnum):
    """记录资源进入锁集合的稳定业务原因。"""

    PRIMARY_TARGET = "primary_target"
    PLAN_DEPENDENCY = "plan_dependency"
    REPAIR_EXPANSION = "repair_expansion"


class ApplicationLifecycleModel(BaseModel):
    """为生命周期模型提供统一的驼峰序列化配置。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ApplicationIdentity(ApplicationLifecycleModel):
    """保存应用的稳定标识和显示名称。"""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)


class ArtifactReference(ApplicationLifecycleModel):
    """引用正式产物而不复制其正文。"""

    kind: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)
    revision: str | int | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PendingInteraction(ApplicationLifecycleModel):
    """描述一个具有乐观并发版本的待处理业务交互。"""

    id: str = Field(min_length=1, max_length=256)
    type: PendingInteractionType
    based_on_revision: int = Field(alias="basedOnRevision", ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactReference] = Field(default_factory=list, alias="artifactRefs")
    created_at: datetime = Field(alias="createdAt")
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")


class ApplicationInitialization(ApplicationLifecycleModel):
    """保存应用初始化阶段及其通用执行状态。"""

    stage: ApplicationLifecycleStage
    status: ApplicationLifecycleStatus
    thread_id: str | None = Field(default=None, alias="threadId", max_length=512)


class ApplicationLifecycleError(ApplicationLifecycleModel):
    """保存最近一次可恢复或终止错误的短摘要。"""

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2048)
    recoverable: bool = True
    occurred_at: datetime = Field(alias="occurredAt")
    attempt: int = Field(default=1, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ExecutionResourceClaim(ApplicationLifecycleModel):
    """描述一次执行准备独占的规范化业务资源。"""

    type: ExecutionResourceType
    target_id: str = Field(alias="targetId", min_length=1, max_length=512)
    role: ExecutionResourceRole = ExecutionResourceRole.DEPENDENCY
    reason: ExecutionResourceReason = ExecutionResourceReason.PLAN_DEPENDENCY


class ExecutionResourceLock(ApplicationLifecycleModel):
    """保存一个业务资源的独占锁所有者与获取原因。"""

    run_id: str = Field(alias="runId", min_length=1, max_length=512)
    owner_page_id: str | None = Field(default=None, alias="ownerPageId", max_length=512)
    mode: Literal["exclusive"] = "exclusive"
    role: ExecutionResourceRole
    reason: ExecutionResourceReason
    acquired_at: datetime = Field(alias="acquiredAt")


class ExecutionResourceLocks(ApplicationLifecycleModel):
    """按资源类型建立可直接供前端查询的锁索引。"""

    application: ExecutionResourceLock | None = None
    pages: dict[str, ExecutionResourceLock] = Field(default_factory=dict)
    api_contracts: dict[str, ExecutionResourceLock] = Field(
        default_factory=dict,
        alias="apiContracts",
    )
    data_sources: dict[str, ExecutionResourceLock] = Field(
        default_factory=dict,
        alias="dataSources",
    )


class WorkbenchExecution(ApplicationLifecycleModel):
    """保存当前占用工作区的页面或应用级计划执行。"""

    scope: Literal["application", "page", "data_source"]
    target_id: str = Field(alias="targetId", min_length=1, max_length=512)
    page_id: str | None = Field(default=None, alias="pageId", max_length=512)
    thread_id: str = Field(alias="threadId", min_length=1, max_length=512)
    run_id: str = Field(alias="runId", min_length=1, max_length=512)
    phase: str = Field(min_length=1, max_length=128)
    status: WorkbenchExecutionStatus
    resource_keys: list[str] = Field(default_factory=list, alias="resourceKeys")
    pending_interaction: PendingInteraction | None = Field(
        default=None,
        alias="pendingInteraction",
    )
    error: ApplicationLifecycleError | None = None
    started_at: datetime = Field(alias="startedAt")
    updated_at: datetime = Field(alias="updatedAt")


class ApplicationLifecycle(ApplicationLifecycleModel):
    """表示 application-lifecycle.json 的完整版本化业务快照。"""

    schema_version: Literal[APPLICATION_LIFECYCLE_SCHEMA_VERSION] = Field(
        default=APPLICATION_LIFECYCLE_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    application: ApplicationIdentity
    updated_at: datetime = Field(alias="updatedAt")
    revision: int = Field(ge=1)
    initialization: ApplicationInitialization
    active_run_id: str | None = Field(default=None, alias="activeRunId", max_length=512)
    active_executions: dict[str, WorkbenchExecution] = Field(
        default_factory=dict,
        alias="activeExecutions",
    )
    resource_locks: ExecutionResourceLocks = Field(
        default_factory=ExecutionResourceLocks,
        alias="resourceLocks",
    )
    error: ApplicationLifecycleError | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(UTC)
