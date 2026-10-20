"""内置数据库操作记录的统一只读合同，不扩大可提交的数据库动作。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator


class DatabaseOperationResult(BaseModel):
    """仅投射安全结果摘要，不公开 SQL、执行步骤和内部审计账号。"""

    model_config = ConfigDict(strict=True, extra="ignore")
    errorCode: str | None = Field(default=None, max_length=256)
    errorSummary: str | None = Field(default=None, max_length=2000)
    recoveryState: str = Field(max_length=128)


class DatabaseTableOperation(BaseModel):
    """统一单表与整份资源操作记录，允许资源回收使用空单表名称。"""

    model_config = ConfigDict(strict=True, extra="ignore")
    operationId: str = Field(pattern=r"^[1-9][0-9]*$")
    mode: Literal["CREATE", "REBUILD", "DROP", "RELEASE"]
    tableName: str = Field(max_length=64)
    tableNames: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(max_length=200)
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"]
    createdAt: str = Field(max_length=64)
    executionResult: DatabaseOperationResult

    @model_validator(mode="after")
    def validate_single_table(self) -> "DatabaseTableOperation":
        """单表动作必须声明目标，整份资源回收由目标清单表达范围。"""

        if self.mode != "RELEASE" and not self.tableName:
            raise ValueError("单表操作缺少目标表名。")
        return self

    def targets_table(self, table: str) -> bool:
        """精确匹配单表名称或资源操作清单，不根据相同前缀推断归属。"""

        return self.tableName == table or table in self.tableNames


def parse_database_operations(raw: Any) -> list[DatabaseTableOperation]:
    """校验有界历史与编号唯一性，为实体页和数据源页返回同一安全投影。"""

    try:
        entries = TypeAdapter(list[DatabaseTableOperation]).validate_python(raw)
    except ValidationError:
        raise ValueError("内置数据库操作记录不符合当前合同，请核对 SaaS 接口。") from None
    if len(entries) > 100:
        raise ValueError("内置数据库操作记录超出支持范围。")
    if len({entry.operationId for entry in entries}) != len(entries):
        raise ValueError("内置数据库操作记录编号重复。")
    return entries
