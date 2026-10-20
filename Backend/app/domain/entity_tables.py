"""实体建表动作与 SaaS 安全结果投影。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EntityTableRequest(BaseModel):
    """只接受实体身份和当前 SQL 指纹，不允许前端指定库、表或任意 SQL。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)
    action: Literal["prepare", "execute", "rebuild", "refresh"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    entity_id: str = Field(alias="entityId", min_length=1, max_length=256)
    preview_hash: str | None = Field(default=None, alias="previewHash", pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False
    allow_data_loss: bool = Field(default=False, alias="allowDataLoss")


class SafeSaasModel(BaseModel):
    """仅返回明确允许的 SaaS 字段，忽略未展示的审计与内部信息。"""

    model_config = ConfigDict(extra="ignore", strict=True)


class OperationReceipt(SafeSaasModel):
    """受理仅代表运行中，不当成已建表。"""

    operationId: str = Field(pattern=r"^[1-9][0-9]*$")
    status: Literal["RUNNING"]


class TableDifference(SafeSaasModel):
    """当前实体字段或约束的结构差异。"""

    category: str
    target: str
    expected: str | None = None
    actual: str | None = None


class TableCheckEntry(SafeSaasModel):
    """一张真实表的本次检查状态。"""

    tableName: str
    status: Literal["SYNCED", "NOT_CREATED", "STRUCTURE_MISMATCH", "TABLE_CONFLICT"]
    differences: list[TableDifference]


class TableReadiness(SafeSaasModel):
    """仅对本次提交的目标表检查，不投射为整个应用启动通过。"""

    ready: bool
    datasourceId: int
    reason: str | None = None
    operationId: str | None = None
    operationStatus: str | None = None
    tables: list[TableCheckEntry]
