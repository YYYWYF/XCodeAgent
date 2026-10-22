"""无摘要的 Capability Update V4 严格协议模型。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Field, StrictInt, StringConstraints, model_validator

from app.services.template_reconcile.protocol_v3 import (
    AddFileOperationV3,
    EnsureJavaAnnotationOperationV3,
    EnsureMavenDependencyOperationV3,
    NonBlankStringV3,
    ProtocolV3Model,
    ReplaceManagedFileOperationV3,
    TemplateRevisionV3,
    TemplateStateV3,
    OperationBaseV3,
)


class EnsureCapabilityConfigParametersV4(ProtocolV3Model):
    """声明由新安装能力拥有的配置结构，实际值仍由本地 updater 写入。"""

    format: Literal["dotenv", "yaml"]
    capabilityIds: list[NonBlankStringV3] = Field(min_length=1)


class EnsureCapabilityConfigOperationV4(OperationBaseV3):
    """确保模板已创建受管 dotenv/YAML 叶子，禁止覆盖现有用户值。"""

    type: Literal["ENSURE_CAPABILITY_CONFIG"]
    target: Literal[
        "frontend/.env",
        "backend/src/main/resources/application.yml",
        "backend/src/main/resources/application-dev.yml",
    ]
    parameters: EnsureCapabilityConfigParametersV4
    payloadRef: NonBlankStringV3

    @model_validator(mode="after")
    def validate_surface(self) -> "EnsureCapabilityConfigOperationV4":
        """校验配置格式与固定工程表面一一对应。"""

        expected = "dotenv" if self.target == "frontend/.env" else "yaml"
        if self.parameters.format != expected:
            raise ValueError("ENSURE_CAPABILITY_CONFIG target 与 format 不匹配。")
        if any(item not in {"login", "authorization"} for item in self.parameters.capabilityIds):
            raise ValueError("ENSURE_CAPABILITY_CONFIG capabilityIds 非法。")
        return self


OperationV4 = Annotated[
    Union[AddFileOperationV3, ReplaceManagedFileOperationV3, EnsureJavaAnnotationOperationV3, EnsureMavenDependencyOperationV3, EnsureCapabilityConfigOperationV4],
    Field(discriminator="type"),
]


class PayloadDescriptorV4(ProtocolV3Model):
    """V4 payload 只记录字节长度。"""

    size: StrictInt = Field(ge=0)


class ExtensionUpdatePackageV4(ProtocolV3Model):
    """冻结 V4 包的字段集合，拒绝 V3 摘要和卸载操作。"""

    protocolVersion: Literal["4"]
    packageId: Annotated[str, StringConstraints(pattern=r"^pkg-[0-9a-f]{8}-[0-9a-f-]{27,}$")]
    mode: Literal["APPLY", "RECONCILE"]
    sourceRevision: TemplateRevisionV3
    expectedStateSequence: StrictInt = Field(ge=0)
    operations: list[OperationV4]
    validationPlan: list[Any]
    payloadManifest: dict[NonBlankStringV3, PayloadDescriptorV4]
    nextTemplateState: TemplateStateV3
    diagnostics: list[Any]

    @model_validator(mode="after")
    def validate_contract(self) -> "ExtensionUpdatePackageV4":
        """保证操作身份唯一、顺序连续，且没有非空诊断任务。"""

        if self.validationPlan or self.diagnostics:
            raise ValueError("V4 validationPlan 与 diagnostics 必须为空。")
        if [item.index for item in self.operations] != list(range(len(self.operations))):
            raise ValueError("V4 operations.index 必须连续。")
        if len({item.operationId for item in self.operations}) != len(self.operations):
            raise ValueError("V4 operationId 必须唯一。")
        if self.nextTemplateState.managedConfiguration is not None or "stateSequence" not in self.nextTemplateState.model_fields_set:
            raise ValueError("V4 nextTemplateState 必须显式给出 stateSequence，且不得携带 managedConfiguration。")
        return self
