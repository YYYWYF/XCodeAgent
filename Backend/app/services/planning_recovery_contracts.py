"""PlanningRun 失败后的 Recovery Snapshot 领域合同与纯校验逻辑。"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, PlainSerializer, StringConstraints, model_validator

from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel, freeze_json, plain_json
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import PlanningRun
from app.services.unit_generation_contracts import CandidateAttempt


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_CandidateMap = Annotated[
    Mapping[_Identifier, CandidateAttempt],
    BeforeValidator(plain_json),
    AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict[str, CandidateAttempt]),
]


def _canonical_payload(value: Mapping) -> bytes:
    """把 Snapshot 内容按稳定 JSON 规则编码，供完整性摘要复用。"""

    return json.dumps(
        plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def planning_recovery_snapshot_digest(value: Mapping | PlanningRecoverySnapshot) -> str:
    """计算排除 snapshot_digest 自身后的 Recovery Snapshot SHA-256。"""

    payload = (
        value.model_dump(mode="json")
        if isinstance(value, PlanningRecoverySnapshot)
        else plain_json(value)
    )
    if not isinstance(payload, Mapping):
        raise TypeError("PlanningRecoverySnapshot 摘要输入必须是 JSON object。")
    unsigned = dict(payload)
    unsigned.pop("snapshot_digest", None)
    return hashlib.sha256(_canonical_payload(unsigned)).hexdigest()


class PlanningRecoverySnapshot(FrozenPlanningModel):
    """failed PlanningRun 的独立 retry optimization state，不承担任何规划权威。"""

    schema_version: Literal["planning-recovery.v1"]
    source_workflow_run_id: _Identifier
    source_planning_run_id: _Identifier
    owner_session_id: _Identifier
    thread_id: _Identifier
    input_fingerprint: _Identifier
    base_confirmed_plan_digest: _Identifier | None
    build_execution_scope: FrozenJsonObject
    failure: ValidationIssue
    candidates_by_unit: _CandidateMap
    created_at: _Identifier
    source_failed_at: _Identifier
    snapshot_digest: _Digest

    @model_validator(mode="after")
    def validate_recovery_boundary(self) -> "PlanningRecoverySnapshot":
        """只允许基础设施失败及 source Run 当前有效 Candidate 进入 Snapshot。"""

        if (
            self.failure.code != "UNIT_GENERATION_INFRASTRUCTURE_FAILURE"
            or self.failure.level != "system"
            or self.failure.category != "infrastructure"
            or self.failure.retryable
        ):
            raise ValueError(
                "planning-recovery.v1 只接受不可重试的 UNIT_GENERATION_INFRASTRUCTURE_FAILURE。"
            )
        if not self.candidates_by_unit:
            raise ValueError("Recovery Snapshot 至少必须包含一个当前 candidate_ready Candidate。")
        for unit_id, candidate in self.candidates_by_unit.items():
            if unit_id != candidate.identity.unit_id:
                raise ValueError("candidates_by_unit 的 key 必须匹配 Candidate identity.unit_id。")
            if candidate.identity.planning_run_id != self.source_planning_run_id:
                raise ValueError("Recovery Candidate 必须属于 source PlanningRun。")
            if candidate.input_fingerprint != self.input_fingerprint:
                raise ValueError("Recovery Candidate 必须绑定 source Run 的 input_fingerprint。")
            if candidate.status != "valid" or candidate.validation_issues or not candidate.tasks:
                raise ValueError("Recovery Snapshot 只能保存 valid、非空且无 Issue 的当前 Candidate。")
        if not hmac.compare_digest(
            self.snapshot_digest,
            planning_recovery_snapshot_digest(self),
        ):
            raise ValueError("Recovery Snapshot 的 snapshot_digest 与内容不匹配。")
        return self


def build_planning_recovery_snapshot(
    failed_run: PlanningRun,
    owner_session_id: str,
    *,
    created_at: str | None = None,
) -> PlanningRecoverySnapshot | None:
    """从最终 failed Run 的 latest candidate pointers 构造自包含 Recovery Snapshot。"""

    run = PlanningRun.model_validate(failed_run)
    if run.status != "failed":
        raise ValueError("Recovery Snapshot 只能从 failed PlanningRun 构造。")
    if (
        run.failure is None
        or run.failure.code != "UNIT_GENERATION_INFRASTRUCTURE_FAILURE"
        or run.failure.level != "system"
        or run.failure.category != "infrastructure"
        or run.failure.retryable
    ):
        raise ValueError(
            "当前 failed PlanningRun 不是支持 Recovery 的 UNIT_GENERATION_INFRASTRUCTURE_FAILURE。"
        )

    candidates: dict[str, CandidateAttempt] = {}
    for unit_id in run.planning_unit_ids:
        unit = run.unit_states[unit_id]
        if unit.generation_status != "candidate_ready":
            continue
        candidate_id = unit.latest_candidate_id
        if candidate_id is None:
            raise ValueError("candidate_ready Unit 缺少 latest_candidate_id。")
        candidate = run.candidates.get(candidate_id)
        if candidate is None:
            raise ValueError("latest_candidate_id 未能在 source PlanningRun Candidate registry 中解析。")
        candidates[unit_id] = candidate

    if not candidates:
        return None

    source_failed_at = run.updated_at
    payload = {
        "schema_version": "planning-recovery.v1",
        "source_workflow_run_id": run.workflow_run_id,
        "source_planning_run_id": run.planning_run_id,
        "owner_session_id": owner_session_id,
        "thread_id": run.thread_id,
        "input_fingerprint": run.input_fingerprint,
        "base_confirmed_plan_digest": run.base_confirmed_plan_digest,
        "build_execution_scope": plain_json(run.build_execution_scope),
        "failure": run.failure.model_dump(mode="json"),
        "candidates_by_unit": {
            unit_id: candidate.model_dump(mode="json")
            for unit_id, candidate in candidates.items()
        },
        "created_at": source_failed_at if created_at is None else created_at,
        "source_failed_at": source_failed_at,
    }
    payload["snapshot_digest"] = planning_recovery_snapshot_digest(payload)
    return PlanningRecoverySnapshot.model_validate(payload)
