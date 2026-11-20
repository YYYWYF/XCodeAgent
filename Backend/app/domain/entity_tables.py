"""实体建表动作与 SaaS 安全结果投影。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityTableRequest(BaseModel):
    """接受实体身份及受控动作，DDL 修订和确认只引用后端签发的草稿。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)
    action: Literal["preview_ddl", "revise_ddl", "confirm_ddl", "prepare", "execute", "rebuild", "refresh"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    entity_id: str = Field(alias="entityId", min_length=1, max_length=256)
    preview_hash: str | None = Field(default=None, alias="previewHash", pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False
    allow_data_loss: bool = Field(default=False, alias="allowDataLoss")
    draft_id: str | None = Field(default=None, alias="draftId", pattern=r"^[0-9a-f]{32}$")
    feedback: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_ddl_action(self) -> "EntityTableRequest":
        """修订和确认必须绑定审核版本，确认必须携带显式确认标记。"""
        if self.action in {"revise_ddl", "confirm_ddl"} and self.draft_id is None:
            raise ValueError("请先读取待确认的 DDL 草稿。")
        if self.action == "revise_ddl" and (not self.feedback or not self.feedback.strip()):
            raise ValueError("请输入 DDL 修改意见。")
        if self.action == "confirm_ddl" and not self.confirmed:
            raise ValueError("请明确确认保存当前 DDL 草稿。")
        if self.action != "revise_ddl" and self.feedback is not None:
            raise ValueError("只有修订动作可以提交 DDL 修改意见。")
        return self


class EntityDdlRecord(BaseModel):
    """同一不可变内容由待确认草稿提升为工作区正式记录。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    entity_id: str = Field(min_length=1, max_length=256)
    table_name: str = Field(min_length=1, max_length=64)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sql: str = Field(min_length=1, max_length=65536)


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
