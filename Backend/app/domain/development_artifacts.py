"""页面、接口与实体开发状态的当前持久化合同。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DevelopmentArtifactModel(BaseModel):
    """严格校验产物状态，统一使用公开 camelCase 字段。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DevelopmentArtifactTarget(DevelopmentArtifactModel):
    """用稳定标识绑定唯一产物，禁止索引和展示路径替代身份。"""

    type: Literal["page", "endpoint", "entity"]
    entity_id: str | None = Field(default=None, alias="entityId", min_length=1)
    page_id: str | None = Field(default=None, alias="pageId", min_length=1)
    api_contract_id: str | None = Field(default=None, alias="apiContractId", min_length=1)
    endpoint_id: str | None = Field(default=None, alias="endpointId", min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> "DevelopmentArtifactTarget":
        """确保三类目标只能携带各自完整的标识。"""

        if self.type == "entity":
            if not self.entity_id or self.page_id or self.api_contract_id or self.endpoint_id:
                raise ValueError("实体开发目标必须且只能提供 entityId。")
            return self
        if self.entity_id:
            raise ValueError("页面和接口目标不能携带 entityId。")
        if self.type == "page":
            if not self.page_id or self.api_contract_id or self.endpoint_id:
                raise ValueError("页面开发目标必须且只能提供 pageId。")
        elif self.page_id or not self.api_contract_id or not self.endpoint_id:
            raise ValueError("接口开发目标必须提供 apiContractId 和 endpointId。")
        return self


class DevelopmentArtifactProgress(DevelopmentArtifactModel):
    """保存不可被二次修改覆盖的初次完成事实。"""

    initial_development_status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending", alias="initialDevelopmentStatus"
    )
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    completed_run_id: str | None = Field(default=None, alias="completedRunId")
    completed_thread_id: str | None = Field(default=None, alias="completedThreadId")

    @model_validator(mode="after")
    def validate_completion(self) -> "DevelopmentArtifactProgress":
        """完成记录必须带齐首次完成证据，未完成记录不允许携带完成字段。"""

        evidence = (self.completed_at, self.completed_run_id, self.completed_thread_id)
        if self.initial_development_status == "completed":
            if not all(evidence):
                raise ValueError("初次开发完成必须提供时间、runId 和 threadId。")
        elif any(item is not None for item in evidence):
            raise ValueError("未完成产物不能携带完成证据。")
        return self


class EntityDevelopmentProgress(DevelopmentArtifactModel):
    """实体完成以当前正式绑定的确认状态为证据，不伪造 Build 执行记录。"""

    initial_development_status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending", alias="initialDevelopmentStatus"
    )


class DevelopmentArtifacts(DevelopmentArtifactModel):
    """保存当前已确认目录及其开发状态；目录错误时禁止测试。"""

    pages: dict[str, DevelopmentArtifactProgress] = Field(default_factory=dict)
    entities: dict[str, EntityDevelopmentProgress] = Field(default_factory=dict)
    endpoints: dict[str, dict[str, DevelopmentArtifactProgress]] = Field(default_factory=dict)
    catalog_error: str | None = Field(default="开发产物目录尚未就绪。", alias="catalogError")


class TestEntryGate(DevelopmentArtifactModel):
    """只投影、不重复持久化的应用级测试门禁。"""

    allowed: bool
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    pending: int = Field(ge=0)
    in_progress: int = Field(alias="inProgress", ge=0)
    blockers: list[DevelopmentArtifactTarget]
    reason: str | None = None
