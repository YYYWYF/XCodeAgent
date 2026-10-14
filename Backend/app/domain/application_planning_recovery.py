"""Application Planning 业务事务恢复边界的当前领域合同。"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApplicationPlanningArtifact(StrEnum):
    """定义当前 Application Planning Recovery Contract 支持的正式产物。"""

    TECHNICAL_PLAN = "technical_plan"


class ApplicationPlanningOperation(StrEnum):
    """区分首次生成、正式修订以及失败候选或已有计划修复三类事务来源。"""

    INITIAL = "initial"
    REVISE = "revise"
    REPAIR = "repair"


class ApplicationPlanningRecoveryBoundary(StrEnum):
    """定义 TechnicalPlan 事务可以安全重放的 durable 业务边界。"""

    INPUT_COMMITTED = "input_committed"
    GENERATION_READY = "generation_ready"
    CANDIDATE_COMMITTED = "candidate_committed"
    ARTIFACT_COMMITTED = "artifact_committed"
    REVIEW_READY = "review_ready"


class ApplicationPlanningBoundary(BaseModel):
    """保存由 Backend 写入 Graph State 的业务恢复边界，不接受前端反向构造。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["application-planning-boundary.v1"] = Field(
        alias="schemaVersion",
    )
    operation_id: str = Field(alias="operationId", min_length=1, max_length=256)
    artifact: ApplicationPlanningArtifact
    operation: ApplicationPlanningOperation
    boundary: ApplicationPlanningRecoveryBoundary
    gate_id: str | None = Field(default=None, alias="gateId", max_length=256)
    artifact_revision: str | None = Field(
        default=None,
        alias="artifactRevision",
        max_length=128,
    )
    request_sha256: str = Field(
        alias="requestSha256",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    baseline_sha256: str | None = Field(
        default=None,
        alias="baselineSha256",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    candidate_sha256: str | None = Field(
        default=None,
        alias="candidateSha256",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


def canonical_application_planning_json(value: Any) -> str:
    """用稳定 JSON 表示业务请求或候选产物，确保同一输入得到同一摘要。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def application_planning_sha256(value: Any) -> str:
    """计算业务输入或 TechnicalPlan candidate 的完整 SHA-256。"""

    encoded = canonical_application_planning_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_application_planning_baseline(
    boundary: ApplicationPlanningBoundary,
    baseline: Any,
) -> None:
    """统一校验修订/修复基线，仅允许无已有计划的失败续修不携带摘要。"""

    if boundary.operation not in {
        ApplicationPlanningOperation.REVISE,
        ApplicationPlanningOperation.REPAIR,
    }:
        return
    # 首次生成失败只保留内部候选，尚无 TechnicalPlan；交回节点决定续修或重生。
    # 已有计划或已声明摘要时仍严格校验，禁止利用 repair 绕过基线一致性。
    if (
        boundary.operation is ApplicationPlanningOperation.REPAIR
        and (baseline is None or (isinstance(baseline, dict) and not baseline))
        and boundary.baseline_sha256 is None
    ):
        return
    if boundary.baseline_sha256 is None:
        raise ValueError("TechnicalPlan 修订/修复缺少 baseline 摘要。")
    if (
        not isinstance(baseline, dict)
        or not baseline
        or application_planning_sha256(baseline) != boundary.baseline_sha256
    ):
        raise ValueError("TechnicalPlan 修订/修复 baseline 与 boundary 不匹配。")


def application_planning_boundary_payload(
    *,
    operation_id: str,
    operation: ApplicationPlanningOperation,
    boundary: ApplicationPlanningRecoveryBoundary,
    request: str,
    gate_id: str | None = None,
    artifact_revision: str | None = None,
    baseline: Any | None = None,
    candidate: Any | None = None,
) -> dict[str, Any]:
    """由服务端事实构造可写入 Graph State 的 camelCase boundary payload。"""

    model = ApplicationPlanningBoundary(
        schemaVersion="application-planning-boundary.v1",
        operationId=operation_id,
        artifact=ApplicationPlanningArtifact.TECHNICAL_PLAN,
        operation=operation,
        boundary=boundary,
        gateId=gate_id,
        artifactRevision=artifact_revision,
        requestSha256=application_planning_sha256(str(request or "")),
        baselineSha256=(
            application_planning_sha256(baseline) if baseline is not None else None
        ),
        candidateSha256=(
            application_planning_sha256(candidate) if candidate is not None else None
        ),
    )
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def parse_application_planning_boundary(
    value: Any,
) -> ApplicationPlanningBoundary | None:
    """严格解析当前 checkpoint 中的 boundary，缺失或非法内容按未声明处理。"""

    if not isinstance(value, dict):
        return None
    try:
        return ApplicationPlanningBoundary.model_validate(value)
    except Exception:
        return None


__all__ = [
    "ApplicationPlanningArtifact",
    "ApplicationPlanningBoundary",
    "ApplicationPlanningOperation",
    "ApplicationPlanningRecoveryBoundary",
    "application_planning_boundary_payload",
    "application_planning_sha256",
    "canonical_application_planning_json",
    "parse_application_planning_boundary",
    "validate_application_planning_baseline",
]
