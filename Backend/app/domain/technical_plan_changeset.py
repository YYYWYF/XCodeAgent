"""技术规划修订唯一的严格变更集契约，不接受完整候选或业务专用操作。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

NonEmptyText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class _Operation(BaseModel):
    """所有操作共用严格路径，字段差异由判别联合明确表达给结构化模型。"""

    model_config = ConfigDict(extra="forbid")
    path: NonEmptyText


class AddOperation(_Operation):
    """新增不存在的节点，显式 null 也是合法值。"""

    op: Literal["add"]
    value: JsonValue


class RemoveOperation(_Operation):
    """核对完整旧值后删除节点，禁止凭模型省略推断删除。"""

    op: Literal["remove"]
    expected_before: JsonValue = Field(alias="expectedBefore")


class ReplaceOperation(_Operation):
    """核对旧值后替换节点，未提供字段与显式 null 严格区分。"""

    op: Literal["replace"]
    value: JsonValue
    expected_before: JsonValue = Field(alias="expectedBefore")


class MoveOperation(_Operation):
    """将已有节点移动到尚不存在的路径，不以移动隐式覆盖内容。"""

    op: Literal["move"]
    from_path: NonEmptyText = Field(alias="from")
    expected_before: JsonValue = Field(alias="expectedBefore")


class TestOperation(_Operation):
    """只验证当前值，不构成已落实的修改需求。"""

    op: Literal["test"]
    value: JsonValue


LogicalOperation = Annotated[
    AddOperation | RemoveOperation | ReplaceOperation | MoveOperation | TestOperation,
    Field(discriminator="op"),
]


class RevisionIntent(BaseModel):
    """登记平台绑定的一次完整原始请求及其有序操作。"""

    model_config = ConfigDict(extra="forbid")
    id: NonEmptyText
    source_text: NonEmptyText = Field(alias="sourceText")
    operations: list[LogicalOperation] = Field(min_length=1)

    @model_validator(mode="after")
    def require_mutation(self) -> "RevisionIntent":
        """只做 test 不算落实修改，每次请求必须包含实际写操作。"""

        if all(operation.op == "test" for operation in self.operations):
            raise ValueError(f"请求 {self.id} 只有 test，未提供实际修改操作。")
        return self


class TechnicalPlanChangeSet(BaseModel):
    """固定外层格式、基线摘要和唯一平台绑定事务，拒绝旧候选及额外字段。"""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = Field(alias="schemaVersion")
    base_sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")] = Field(alias="baseSha256")
    intents: list[RevisionIntent] = Field(min_length=1, max_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        """拒绝把布尔值或浮点值当作协议整数版本。"""

        if type(value) is not int:
            raise ValueError("schemaVersion 必须是整数 1。")
        return value


class RevisionOperationBatch(BaseModel):
    """模型只提交有序操作；请求原文和内部事务身份由平台确定性绑定。"""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = Field(alias="schemaVersion")
    base_sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")] = Field(alias="baseSha256")
    operations: list[LogicalOperation] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        """拒绝把布尔值或浮点值当作协议整数版本。"""

        if type(value) is not int:
            raise ValueError("schemaVersion 必须是整数 1。")
        return value

    def bind_request(self, request: str) -> TechnicalPlanChangeSet:
        """用服务端完整原文构造唯一内部事务，模型无需复制或拆分自然语言。"""

        return TechnicalPlanChangeSet.model_validate({
            "schemaVersion": self.schema_version,
            "baseSha256": self.base_sha256,
            "intents": [{
                "id": "current-request",
                "sourceText": request,
                "operations": [operation.model_dump(by_alias=True) for operation in self.operations],
            }],
        })
