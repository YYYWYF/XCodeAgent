"""定义 Template Engine V3 的严格 State、Operation 与 Update Package wire DTO。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, StringConstraints, field_validator, model_validator

NonBlankStringV3 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TemplateRevisionV3 = Annotated[str, StringConstraints(pattern=r"^\d{4}\.\d{2}\.\d{2}\.\d+$")]
DigestV3 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
_MANAGED_FRONTEND_TARGETS = frozenset({"frontend/src/extensions/providers.ts", "frontend/src/extensions/rootRoutes.tsx", "frontend/src/extensions/systemPageRoutes.ts", "frontend/src/extensions/initializers.ts", "frontend/src/extensions/errorReporters.ts"})
_APPLICATION_TARGET = "backend/src/main/java/com/cmbchina/backend/Application.java"
_POM_TARGET = "backend/pom.xml"
_JAVA = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$")


class ProtocolV3Model(BaseModel):
    """为 V3 协议对象拒绝隐式类型转换与未声明字段。"""
    model_config = ConfigDict(extra="forbid", strict=True)


class CapabilityStateV3(ProtocolV3Model):
    """保存 State 中已经启用的 Capability 及其不透明配置。"""
    enabled: StrictBool
    config: dict[str, Any]

    @model_validator(mode="after")
    def validate_enabled(self) -> "CapabilityStateV3":
        """禁止把 disabled Capability 写入 V3 State。"""
        if self.enabled is not True:
            raise ValueError("V3 TemplateState 只能保存 enabled=true 的 Capability。")
        return self


class InstalledArtifactStateV3(ProtocolV3Model):
    """保存普通 Extension 文件实际安装时的版本。"""
    capabilityId: NonBlankStringV3
    target: NonBlankStringV3
    installedRevision: TemplateRevisionV3


class JavaAnnotationSpecV3(ProtocolV3Model):
    """描述 Java Annotation 受管资源。"""
    annotationClass: NonBlankStringV3


class MavenDependencySpecV3(ProtocolV3Model):
    """描述 Maven 依赖受管资源。"""
    groupId: NonBlankStringV3
    artifactId: NonBlankStringV3
    version: StrictStr | None
    scope: StrictStr | None


class JavaAnnotationContributionV3(ProtocolV3Model):
    """保存物理 Java Annotation 的聚合投影。"""
    type: Literal["JAVA_ANNOTATION"]
    target: Literal["backend/src/main/java/com/cmbchina/backend/Application.java"]
    owners: list[NonBlankStringV3]
    spec: JavaAnnotationSpecV3
    appliedRevision: TemplateRevisionV3


class MavenDependencyContributionV3(ProtocolV3Model):
    """保存物理 Maven 依赖的聚合投影。"""
    type: Literal["MAVEN_DEPENDENCY"]
    target: Literal["backend/pom.xml"]
    owners: list[NonBlankStringV3]
    spec: MavenDependencySpecV3
    appliedRevision: TemplateRevisionV3


ManagedContributionV3 = Annotated[Union[JavaAnnotationContributionV3, MavenDependencyContributionV3], Field(discriminator="type")]


class TemplateStateV3(ProtocolV3Model):
    """表示当前唯一接受的六字段 TemplateState。"""
    schemaVersion: Literal[3]
    templateRevision: TemplateRevisionV3
    requested: dict[NonBlankStringV3, CapabilityStateV3]
    effective: dict[NonBlankStringV3, CapabilityStateV3]
    installedArtifacts: dict[NonBlankStringV3, InstalledArtifactStateV3]
    managedContributions: dict[NonBlankStringV3, ManagedContributionV3]

    @model_validator(mode="after")
    def validate_relations(self) -> "TemplateStateV3":
        """校验 requested 闭包、Artifact 身份和 Contribution Owner。"""
        for name, capability in self.requested.items():
            if self.effective.get(name) != capability:
                raise ValueError("requested Capability 必须以同配置存在于 effective。")
        for artifact_id, artifact in self.installedArtifacts.items():
            parts = artifact_id.split(":", 2)
            if len(parts) != 3 or parts[0] not in {"frontend", "backend"} or not parts[2]:
                raise ValueError("installedArtifacts key 必须是 V3 Artifact ID。")
            if artifact.capabilityId != parts[1] or artifact.target != f"{parts[0]}/{parts[2]}":
                raise ValueError("installedArtifacts 身份与 target 不一致。")
            _safe_target(artifact.target)
            if artifact.capabilityId not in self.effective:
                raise ValueError("Artifact Capability 必须属于 effective。")
        for key, item in self.managedContributions.items():
            expected = f"annotation:{item.spec.annotationClass}" if item.type == "JAVA_ANNOTATION" else f"maven:{item.spec.groupId}:{item.spec.artifactId}"
            if key != expected or not item.owners or item.owners != sorted(set(item.owners)) or any(owner not in self.effective for owner in item.owners):
                raise ValueError("managedContributions 不满足 V3 聚合约束。")
        return self


class PayloadDescriptorV3(ProtocolV3Model):
    """描述 ZIP 中不可变 payload 的字节摘要。"""
    size: StrictInt = Field(ge=0)
    sha256: DigestV3


class EmptyParametersV3(ProtocolV3Model):
    """表示没有参数的固定 Operation。"""


class JavaParametersV3(ProtocolV3Model):
    """保存 Java Annotation 操作的完整身份。"""
    annotationClass: NonBlankStringV3

    @field_validator("annotationClass")
    @classmethod
    def validate_class(cls, value: str) -> str:
        """拒绝不完整的 Java 类名。"""
        if not _JAVA.fullmatch(value):
            raise ValueError("annotationClass 必须是完整 Java 类名。")
        return value


class MavenParametersV3(MavenDependencySpecV3):
    """复用 Maven Contribution 的完整参数结构。"""


class OperationBaseV3(ProtocolV3Model):
    """保存每种 V3 Operation 的共同身份字段。"""
    schemaVersion: Literal[1]
    operationId: NonBlankStringV3
    index: StrictInt = Field(ge=0)
    type: str
    target: NonBlankStringV3

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        """拒绝不安全的相对路径。"""
        _safe_target(value)
        return value


class AddFileOperationV3(OperationBaseV3):
    """表示不可覆盖的普通文件新增。"""
    type: Literal["ADD_FILE"]
    parameters: EmptyParametersV3
    payloadRef: NonBlankStringV3


class ReplaceManagedFileOperationV3(OperationBaseV3):
    """表示五个前端 Registry 的整体受管替换。"""
    type: Literal["REPLACE_MANAGED_FILE"]
    parameters: EmptyParametersV3
    payloadRef: NonBlankStringV3

    @model_validator(mode="after")
    def validate_managed_target(self) -> "ReplaceManagedFileOperationV3":
        """限制整体覆盖的目标范围。"""
        if self.target not in _MANAGED_FRONTEND_TARGETS:
            raise ValueError("REPLACE_MANAGED_FILE target 非法。")
        return self


class EnsureJavaAnnotationOperationV3(OperationBaseV3):
    """表示确保 Application 注解存在。"""
    type: Literal["ENSURE_JAVA_ANNOTATION"]
    target: Literal["backend/src/main/java/com/cmbchina/backend/Application.java"]
    parameters: JavaParametersV3
    payloadRef: None


class RemoveJavaAnnotationOperationV3(EnsureJavaAnnotationOperationV3):
    """表示移除 Application 注解。"""
    type: Literal["REMOVE_JAVA_ANNOTATION"]


class EnsureMavenDependencyOperationV3(OperationBaseV3):
    """表示确保 pom.xml 依赖存在。"""
    type: Literal["ENSURE_MAVEN_DEPENDENCY"]
    target: Literal["backend/pom.xml"]
    parameters: MavenParametersV3
    payloadRef: None


class RemoveMavenDependencyOperationV3(EnsureMavenDependencyOperationV3):
    """表示按完整 spec 移除 pom.xml 依赖。"""
    type: Literal["REMOVE_MAVEN_DEPENDENCY"]


UpdateOperationV3 = Annotated[Union[AddFileOperationV3, ReplaceManagedFileOperationV3, EnsureJavaAnnotationOperationV3, RemoveJavaAnnotationOperationV3, EnsureMavenDependencyOperationV3, RemoveMavenDependencyOperationV3], Field(discriminator="type")]


class ExtensionUpdatePackageV3(ProtocolV3Model):
    """表示严格冻结的 V3 Update Package 元数据。"""
    protocolVersion: Literal["3"]
    packageId: Annotated[str, StringConstraints(pattern=r"^pkg-[0-9a-f]{16}$")]
    mode: Literal["APPLY", "RECONCILE"]
    sourceRevision: TemplateRevisionV3
    currentStateDigest: DigestV3
    nextStateDigest: DigestV3
    operations: list[UpdateOperationV3]
    validationPlan: list[Any]
    payloadManifest: dict[NonBlankStringV3, PayloadDescriptorV3]
    nextTemplateState: TemplateStateV3
    diagnostics: list[Any]

    @model_validator(mode="after")
    def validate_contract(self) -> "ExtensionUpdatePackageV3":
        """固定 V3 的空 validation/diagnostics 与 Operation 顺序标识。"""
        if self.validationPlan or self.diagnostics:
            raise ValueError("V3 validationPlan 与 diagnostics 必须为空数组。")
        if [operation.index for operation in self.operations] != list(range(len(self.operations))):
            raise ValueError("operations.index 必须连续且与数组顺序一致。")
        if len({operation.operationId for operation in self.operations}) != len(self.operations):
            raise ValueError("operationId 必须唯一。")
        if self.mode == "RECONCILE" and self.currentStateDigest != self.nextStateDigest:
            raise ValueError("RECONCILE 不得改变 State digest。")
        return self


def _safe_target(value: str) -> None:
    """统一校验 V3 target 不会逃逸 Workspace。"""
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Operation target 非法。")
