"""已交付数据库结构快照，仅保存版本与结构，不保存连接信息。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeliveryColumn(BaseModel):
    """保存当前实体编译器支持的列定义，未知类型不能进入增量脚本。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    type: Literal["BIGINT", "VARCHAR(255)", "TEXT", "DECIMAL(12,2)", "DATE", "DATETIME", "VARCHAR(32)", "TINYINT(1)"]
    nullable: bool
    default: None = None
    comment: str
    auto_increment: bool = False


class DeliveryTable(BaseModel):
    """保存带前缀的完整表结构，主键始终沿用实体建表合同。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    comment: str
    columns: list[DeliveryColumn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_columns(self) -> "DeliveryTable":
        """拒绝重复列和被改写的主键，防止损坏快照被当作空基线。"""

        names = [column.name for column in self.columns]
        primary = self.columns[0]
        if len(names) != len(set(names)) or primary.name != "id" or primary.type != "BIGINT" \
                or primary.nullable or not primary.auto_increment or any(c.auto_increment for c in self.columns[1:]):
            raise ValueError("数据库交付快照的字段或主键无效。")
        return self


class DeliveredDatabase(BaseModel):
    """记录一个版本的完整结构、固定升级来源及两个脚本的完整性摘要。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    branchName: str
    baseBranchName: str | None
    datasourceKey: str = Field(pattern=r"^[0-9a-f]{12}$")
    tables: list[DeliveryTable]
    createHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    alterHash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatabaseDeliveries(BaseModel):
    """按已交付顺序保存当前格式的版本链，不探测旧格式或从真实库补基线。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1] = 1
    versions: list[DeliveredDatabase] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chain(self) -> "DatabaseDeliveries":
        """每个版本固定指向上一交付版本，同名版本与重复表均拒绝。"""

        seen: set[str] = set()
        previous = None
        for version in self.versions:
            names = [table.name for table in version.tables]
            if version.branchName in seen or version.baseBranchName != previous or len(names) != len(set(names)) \
                    or any(not name.startswith(version.datasourceKey + "_") for name in names):
                raise ValueError("数据库交付版本链或表归属无效。")
            seen.add(version.branchName)
            previous = version.branchName
        return self
