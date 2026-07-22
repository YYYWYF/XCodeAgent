"""工作区级应用生命周期状态模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


APPLICATION_LIFECYCLE_SCHEMA_VERSION = "1.0.0"


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


class ApplicationLifecycleModel(BaseModel):
    """为生命周期模型提供统一的驼峰序列化配置。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ApplicationIdentity(ApplicationLifecycleModel):
    """保存应用的稳定标识和显示名称。"""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)


class ProjectIdentity(ApplicationLifecycleModel):
    """保存可选的项目级稳定标识。"""

    id: str = Field(min_length=1, max_length=256)


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


class LifecycleState(ApplicationLifecycleModel):
    """保存当前业务阶段及未来领域的轻量扩展状态。"""

    stage: ApplicationLifecycleStage
    status: ApplicationLifecycleStatus
    domain: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class ApplicationLifecycleError(ApplicationLifecycleModel):
    """保存最近一次可恢复或终止错误的短摘要。"""

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2048)
    recoverable: bool = True
    occurred_at: datetime = Field(alias="occurredAt")
    attempt: int = Field(default=1, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ApplicationLifecycle(ApplicationLifecycleModel):
    """表示 application-lifecycle.json 的完整版本化业务快照。"""

    schema_version: Literal[APPLICATION_LIFECYCLE_SCHEMA_VERSION] = Field(
        default=APPLICATION_LIFECYCLE_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    application: ApplicationIdentity
    project: ProjectIdentity | None = None
    updated_at: datetime = Field(alias="updatedAt")
    revision: int = Field(ge=1)
    lifecycle: LifecycleState
    active_thread_id: str | None = Field(default=None, alias="activeThreadId", max_length=512)
    active_run_id: str | None = Field(default=None, alias="activeRunId", max_length=512)
    pending_interaction: PendingInteraction | None = Field(default=None, alias="pendingInteraction")
    error: ApplicationLifecycleError | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pending_interaction_revision(self) -> "ApplicationLifecycle":
        """确保待交互基于当前快照，避免恢复后误提交旧确认。"""

        if (
            self.pending_interaction is not None
            and self.pending_interaction.based_on_revision != self.revision
        ):
            raise ValueError("pendingInteraction.basedOnRevision 必须等于当前 revision。")
        return self


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(UTC)
