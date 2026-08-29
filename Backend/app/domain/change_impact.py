"""二次修改的契约影响分析领域模型。

这个模块只描述“事实证据”本身，不包含工作流节点、分支或写入动作。这样
Analyzer 即使由模型驱动，也不能通过返回一个节点名称来获得执行权限。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChangeImpactModel(BaseModel):
    """为影响分析结果提供严格的当前 JSON 合同。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


# 当前合同只包含这四个已确认 JSON 产物。这里放在领域层是为了让脱离
# workspace 的单元调用也不能凭空构造一个不存在的 artifact key。
_CURRENT_ARTIFACT_KEYS = frozenset(
    {"requirement-spec", "product-plan", "ui-design", "technical-plan"}
)


class ContractStage(StrEnum):
    """表示事实属于产品需求层还是技术规划层。"""

    REQUIREMENT_DESIGN = "requirement_design"
    PLANNING_DESIGN = "planning_design"


class ContractImpact(StrEnum):
    """表示用户请求与某条已确认事实的关系。"""

    INVALIDATES = "invalidates"
    PRESERVES = "preserves"
    UNKNOWN = "unknown"


class ConflictRelation(StrEnum):
    """描述失效事实与用户请求之间的冲突关系。"""

    CONTRADICTS = "contradicts"
    REMOVES = "removes"
    REASSIGNS = "reassigns"
    MODIFIES = "modifies"
    PRESERVES = "preserves"


class AnalysisStatus(StrEnum):
    """描述 Analyzer 是否拥有足够的已确认 JSON 证据。"""

    COMPLETED = "completed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ContractEvidence(ChangeImpactModel):
    """定位一条已确认 JSON 事实并说明它为何被保留或推翻。

    这里故意不设置全局 ``contract_id``。同一事实的身份由当前请求范围内的
    artifact_key、json_pointer、selector 和 artifact_sha256 共同确定，避免把
    临时推导出来的 ID 写入正式产物或变成跨版本注册表。
    """

    artifact_key: str = Field(alias="artifactKey", min_length=1, max_length=128)
    json_pointer: str = Field(alias="jsonPointer", min_length=1, max_length=2_000)
    selector: dict[str, str] = Field(default_factory=dict, max_length=20)
    artifact_sha256: str = Field(
        alias="artifactSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    contract_stage: ContractStage = Field(alias="contractStage")
    existing_fact: str = Field(alias="existingFact", min_length=1, max_length=4_000)
    requested_change: str = Field(alias="requestedChange", min_length=1, max_length=4_000)
    conflict_relation: ConflictRelation = Field(alias="conflictRelation")
    reason: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def validate_pointer(self) -> "ContractEvidence":
        """确保证据始终携带可复核的 JSON Pointer。"""

        if self.artifact_key not in _CURRENT_ARTIFACT_KEYS:
            raise ValueError("契约证据 artifactKey 不属于当前四类 JSON 产物。")
        if not self.json_pointer.startswith("/") and self.json_pointer != "":
            raise ValueError("契约证据 jsonPointer 必须是 JSON Pointer。")
        expected_stage = (
            ContractStage.PLANNING_DESIGN
            if self.artifact_key == "technical-plan"
            else ContractStage.REQUIREMENT_DESIGN
        )
        if self.contract_stage != expected_stage:
            raise ValueError("契约证据 contractStage 与 artifactKey 不一致。")
        return self


class CodeFinding(ChangeImpactModel):
    """保存目标导向代码扫描返回的原始定位证据，不替代码扫描器下结论。"""

    path: str = Field(min_length=1, max_length=1_000)
    summary: str = Field(min_length=1, max_length=2_000)
    symbol: str | None = Field(default=None, max_length=512)
    relevant_code: str | None = Field(default=None, alias="relevantCode", max_length=8_000)
    line_start: int | None = Field(default=None, alias="lineStart", ge=1)
    line_end: int | None = Field(default=None, alias="lineEnd", ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> "CodeFinding":
        """保证代码证据的结束行不会早于起始行。"""

        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("代码证据行号范围无效。")
        return self


class CodeScanEvidence(ChangeImpactModel):
    """描述是否执行了目标代码扫描及其只读发现。"""

    performed: bool
    reason: str = Field(min_length=1, max_length=2_000)
    findings: list[CodeFinding] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_performed_findings(self) -> "CodeScanEvidence":
        """禁止未执行的 code.scan 携带可被误用的源码发现。"""

        if not self.performed and self.findings:
            raise ValueError("code.scan 未执行时不能携带 findings。")
        return self


class AtomicChange(ChangeImpactModel):
    """表示从一条用户消息拆出的最小独立变更。"""

    change_id: str = Field(alias="changeId", min_length=1, max_length=128)
    requested_change: str = Field(alias="requestedChange", min_length=1, max_length=4_000)
    contract_impact: ContractImpact = Field(alias="contractImpact")
    contract_evidence: list[ContractEvidence] = Field(
        default_factory=list,
        alias="contractEvidence",
        max_length=100,
    )
    code_scan: CodeScanEvidence = Field(alias="codeScan")

    @model_validator(mode="after")
    def validate_impact_evidence(self) -> "AtomicChange":
        """禁止没有 JSON 定位的 invalidates 结论，也禁止伪造 preserve。"""

        if self.contract_impact == ContractImpact.INVALIDATES and not self.contract_evidence:
            raise ValueError("invalidates 必须携带至少一条契约证据。")
        if self.contract_impact == ContractImpact.INVALIDATES and any(
            evidence.conflict_relation == ConflictRelation.PRESERVES
            for evidence in self.contract_evidence
        ):
            raise ValueError("invalidates 的契约证据关系不能是 preserves。")
        if self.contract_impact == ContractImpact.PRESERVES:
            if not self.contract_evidence:
                raise ValueError("preserves 必须携带已确认 JSON 的保留证据。")
            if any(
                evidence.conflict_relation != ConflictRelation.PRESERVES
                for evidence in self.contract_evidence
            ):
                raise ValueError("preserves 的证据关系必须全部为 preserves。")
        if self.contract_impact == ContractImpact.INVALIDATES and self.code_scan.performed:
            raise ValueError("已经确认契约失效时不得执行 code.scan。")
        return self


class ChangeImpactAnalysis(ChangeImpactModel):
    """Analyzer 的完整事实结果，供确定性 Router 消费。"""

    analysis_status: AnalysisStatus = Field(alias="analysisStatus")
    request_summary: str = Field(alias="requestSummary", min_length=1, max_length=4_000)
    atomic_changes: list[AtomicChange] = Field(alias="atomicChanges", min_length=1, max_length=100)
    earliest_affected_contract_stage: ContractStage | None = Field(
        default=None,
        alias="earliestAffectedContractStage",
    )
    invalidated_contracts: list[ContractEvidence] = Field(
        default_factory=list,
        alias="invalidatedContracts",
        max_length=200,
    )
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_analysis_consistency(self) -> "ChangeImpactAnalysis":
        """校验顶层最早阶段和 invalidated 合同与原子变更保持一致。"""

        invalidated = [
            evidence
            for change in self.atomic_changes
            if change.contract_impact == ContractImpact.INVALIDATES
            for evidence in change.contract_evidence
        ]
        if self.earliest_affected_contract_stage is None and invalidated:
            raise ValueError("存在失效证据时必须声明最早受影响阶段。")
        if self.earliest_affected_contract_stage is not None and not invalidated:
            raise ValueError("没有失效证据时不得声明最早受影响阶段。")
        if invalidated:
            expected = min(
                (evidence.contract_stage for evidence in invalidated),
                key=lambda stage: 0 if stage == ContractStage.REQUIREMENT_DESIGN else 1,
            )
            if expected != self.earliest_affected_contract_stage:
                raise ValueError("最早受影响阶段与契约证据不一致。")
        expected_refs = {
            (
                evidence.artifact_key,
                evidence.json_pointer,
                evidence.artifact_sha256,
                evidence.conflict_relation,
            )
            for evidence in invalidated
        }
        actual_refs = {
            (
                evidence.artifact_key,
                evidence.json_pointer,
                evidence.artifact_sha256,
                evidence.conflict_relation,
            )
            for evidence in self.invalidated_contracts
        }
        if expected_refs != actual_refs:
            raise ValueError("invalidatedContracts 必须与 atomicChanges 中的失效证据一致。")
        if any(
            change.contract_impact != ContractImpact.PRESERVES
            and change.code_scan.performed
            for change in self.atomic_changes
        ):
            raise ValueError("只有 preserves 原子变更可以携带 code.scan 结果。")
        if self.analysis_status == AnalysisStatus.COMPLETED and any(
            change.contract_impact == ContractImpact.UNKNOWN for change in self.atomic_changes
        ):
            raise ValueError("存在 unknown 原子变更时分析状态必须为 insufficient_evidence。")
        return self


def analysis_to_json(analysis: ChangeImpactAnalysis) -> dict[str, Any]:
    """将领域结果序列化为 AG-UI 和日志共用的驼峰 JSON。"""

    return analysis.model_dump(mode="json", by_alias=True)
