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
)


OperationV4 = Annotated[
    Union[AddFileOperationV3, ReplaceManagedFileOperationV3, EnsureJavaAnnotationOperationV3, EnsureMavenDependencyOperationV3],
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
