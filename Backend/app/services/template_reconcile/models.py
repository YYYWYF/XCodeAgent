"""当前 Template Engine OpenAPI/代码协议的严格消费模型。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class TemplateReconcileProtocolError(ValueError):
    """表示 Engine 返回内容不符合当前已冻结协议。"""


class EngineModel(BaseModel):
    """为 Engine 协议模型统一拒绝未声明字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class CapabilityConfig(EngineModel):
    """表示当前 Engine 请求配置中的单个 Capability。"""

    enabled: StrictBool
    config: dict[str, Any] | None = None


class RequestedConfig(EngineModel):
    """表示传递给当前 Engine 的完整能力请求配置。"""

    capabilities: dict[str, CapabilityConfig]


class TemplateState(EngineModel):
    """表示当前 OpenAPI 定义的四字段 TemplateState。"""

    templateRevision: str = Field(min_length=1)
    managedFiles: dict[str, str]
    requested: dict[str, Any]
    effective: dict[str, Any]

    @model_validator(mode="after")
    def validate_template_revision(self) -> "TemplateState":
        """拒绝仅由空白组成的 Engine Template Revision。"""

        if not self.templateRevision.strip():
            raise ValueError("templateRevision 必须为非空字符串。")
        return self


class AddFileOperation(EngineModel):
    """表示 Engine 输出的新增文本文件操作。"""

    type: Literal["ADD_FILE"]
    path: str = Field(min_length=1)
    content: str


class UpdateFileOperation(EngineModel):
    """表示 Engine 输出的更新文本文件操作。"""

    type: Literal["UPDATE_FILE"]
    path: str = Field(min_length=1)
    content: str


class DeleteFileOperation(EngineModel):
    """表示 Engine 输出的删除文件操作。"""

    type: Literal["DELETE_FILE"]
    path: str = Field(min_length=1)


FileOperation = Annotated[
    AddFileOperation | UpdateFileOperation | DeleteFileOperation,
    Field(discriminator="type"),
]


class ChangeSetBody(EngineModel):
    """表示当前 Engine Package 内仅含 operations 的 ChangeSet。"""

    operations: list[FileOperation]


class PlanResponse(EngineModel):
    """表示当前 OpenAPI 的 PlanResponse，并校验 kind/body 关系。"""

    kind: Literal["CHANGE", "NO_CHANGE"]
    nextTemplateState: TemplateState
    body: ChangeSetBody | None
    diagnostics: list[Any]

    @model_validator(mode="after")
    def validate_kind_and_body(self) -> "PlanResponse":
        """要求 NO_CHANGE 无 body，CHANGE 必须包含 Engine ChangeSet。"""

        if self.kind == "NO_CHANGE" and self.body is not None:
            raise ValueError("NO_CHANGE 响应的 body 必须为 null。")
        if self.kind == "CHANGE" and self.body is None:
            raise ValueError("CHANGE 响应必须包含 body。")
        return self
