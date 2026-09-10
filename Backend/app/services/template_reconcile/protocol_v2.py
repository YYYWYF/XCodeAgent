"""冻结 Template Capability Reconcile V2 的单次 Strategy 协议。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class TemplateReconcileProtocolV2Error(ValueError):
    """表示 V2 Strategy 协议不满足当前冻结约束。"""

    code = "TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED"


class ProtocolV2Model(BaseModel):
    """为 V2 wire/storage DTO 统一启用严格字段校验。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class CapabilityStateV2(ProtocolV2Model):
    """表示已 canonicalize 的单个 Capability 状态。"""

    enabled: StrictBool
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_enabled(self) -> "CapabilityStateV2":
        """拒绝写入未启用的 Capability，保持 effective/requested 闭包明确。"""

        if self.enabled is not True:
            raise ValueError("Capability 状态必须是 enabled=true。")
        return self


class AppliedAdditionV2(ProtocolV2Model):
    """记录已成功物化 Addition 的生命周期事实，不保存文件内容基线。"""

    capabilityId: str = Field(min_length=1)
    target: str = Field(min_length=1)
    installedRevision: str = Field(min_length=1)
    origin: Literal["GENERATED", "UPDATED"]


class TemplateStateV2(ProtocolV2Model):
    """表示当前唯一支持的 TemplateState V2 持久化结构。"""

    schemaVersion: Literal[2]
    templateRevision: str = Field(min_length=1)
    releaseDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    requested: dict[str, CapabilityStateV2]
    effective: dict[str, CapabilityStateV2]
    appliedAdditions: dict[str, AppliedAdditionV2]


class PayloadDescriptorV2(ProtocolV2Model):
    """描述 Strategy 引用的不可变 ZIP payload。"""

    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


StrategyTypeV2 = Literal[
    "ADD_FILE",
    "TEXT_ANCHOR_INSERT",
    "ENSURE_IMPORT",
    "ENSURE_NPM_DEPENDENCY",
    "ENSURE_MAVEN_DEPENDENCY",
    "ENSURE_REACT_PROVIDER",
    "ENSURE_ROUTE",
    "ENSURE_MENU_ITEM",
    "ENSURE_SPRING_BEAN",
    "ENSURE_INTERCEPTOR",
]


class StrategyDescriptorV2(ProtocolV2Model):
    """描述一个按 Package 全局顺序执行的 Workspace 收敛动作。"""

    strategyId: str = Field(min_length=1)
    index: int = Field(ge=0)
    schemaVersion: Literal[1]
    type: StrategyTypeV2
    target: str = Field(min_length=1)
    precondition: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    payloadRef: str | None = None


ValidationTypeV2 = Literal[
    "CAPABILITY_POSTCONDITION",
    "FILE_EXISTS",
    "STRUCTURE_CHECK",
    "JSON_STRUCTURE_CHECK",
    "NPM_BUILD",
    "NPM_TEST",
    "MAVEN_TEST",
    "MAVEN_PACKAGE",
]


class ValidationPlanItemV2(ProtocolV2Model):
    """描述 Package Apply 后必须执行的一项结构化验收。"""

    validationId: str = Field(min_length=1)
    index: int = Field(ge=0)
    type: ValidationTypeV2
    capabilityId: str | None = None
    workingDirectory: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    blocking: StrictBool
    timeoutSeconds: int = Field(gt=0)
    executionMode: Literal["REAL_WORKSPACE", "SANDBOX"]

    @model_validator(mode="after")
    def validate_postcondition_subject(self) -> "ValidationPlanItemV2":
        """要求 Capability 后置条件显式绑定唯一 Capability 身份。"""

        if self.type == "CAPABILITY_POSTCONDITION" and not self.capabilityId:
            raise ValueError("CAPABILITY_POSTCONDITION 必须提供 capabilityId。")
        return self


class StrategyUpdatePackageV2(ProtocolV2Model):
    """表示 Service 返回的完整且可验证的 V2 Strategy Package。"""

    protocolVersion: Literal["2"]
    packageId: str = Field(min_length=1)
    mode: Literal["APPLY", "RECONCILE"]
    sourceRevision: str = Field(min_length=1)
    currentStateDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    nextStateDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategies: list[StrategyDescriptorV2]
    validationPlan: list[ValidationPlanItemV2]
    payloadManifest: dict[str, PayloadDescriptorV2]
    nextTemplateState: TemplateStateV2
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_package_contract(self) -> "StrategyUpdatePackageV2":
        """校验全局顺序、Payload 引用和 RECONCILE 的 State 不变式。"""

        _validate_indexed_ids(
            "strategies", self.strategies, "strategyId"
        )
        _validate_indexed_ids(
            "validationPlan", self.validationPlan, "validationId"
        )
        for strategy in self.strategies:
            if strategy.payloadRef is not None and strategy.payloadRef not in self.payloadManifest:
                raise ValueError(f"Strategy 引用了不存在的 payloadRef：{strategy.payloadRef}。")
        if self.mode == "RECONCILE":
            if self.currentStateDigest != self.nextStateDigest:
                raise ValueError("RECONCILE 不得改变 TemplateState digest。")
            effective_ids = set(self.nextTemplateState.effective)
            validated_ids = {
                str(item.capabilityId)
                for item in self.validationPlan
                if item.type == "CAPABILITY_POSTCONDITION"
            }
            if not effective_ids.issubset(validated_ids):
                raise ValueError("RECONCILE 缺少 effective Capability 的后置条件验收。")
        return self


def assert_reconcile_state_invariant_v2(
    current_template_state: TemplateStateV2,
    next_template_state: TemplateStateV2,
) -> None:
    """拒绝 RECONCILE 改写 requested、effective 或 Capability Config 的任何语义内容。"""

    current = current_template_state.model_dump(mode="json")
    next_state = next_template_state.model_dump(mode="json")
    for field in ("requested", "effective"):
        if current[field] != next_state[field]:
            raise TemplateReconcileProtocolV2Error(f"RECONCILE 不得改变 TemplateState.{field}。")


def _validate_indexed_ids(label: str, values: list[Any], id_field: str) -> None:
    """要求 Package 数组按零起连续索引并且逻辑标识唯一。"""

    identifiers = [getattr(value, id_field) for value in values]
    indexes = [value.index for value in values]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{label} 包含重复标识。")
    if indexes != list(range(len(values))):
        raise ValueError(f"{label} 的 index 必须从零起连续且与数组顺序一致。")
