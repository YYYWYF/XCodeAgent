"""执行一次单 Unit Candidate Generation Session，不负责重试、调度或校验编排。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Literal

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.planning_issues import ValidationIssue
from app.services.unit_candidate_parser import (
    RawUnitCandidateParseError,
    parse_raw_unit_candidate,
)
from app.services.unit_generation_contracts import (
    AttemptIdentity,
    UnitAttemptJob,
    UnitGenerationAttemptResult,
)


InfrastructureStage = Literal["model_setup", "model_invoke"]


class UnitGenerationInfrastructureError(RuntimeError):
    """标记 Unit generation 的模型配置或传输层失败，供外层终止 PlanningRun。"""

    def __init__(
        self,
        *,
        identity: AttemptIdentity,
        stage: InfrastructureStage,
        cause: Exception,
    ) -> None:
        """保留 Attempt 和失败阶段，同时通过异常链保留原始基础设施异常。"""

        self.category = "infrastructure"
        self.identity = identity
        self.stage = stage
        self.cause_type = type(cause).__name__
        super().__init__(
            f"Unit generation infrastructure failure at {stage}: "
            f"attempt_id={identity.attempt_id}, cause={self.cause_type}"
        )


def _generation_metadata(
    response: object,
    *,
    settings: Settings,
    job: UnitAttemptJob,
) -> dict[str, object]:
    """记录非判定性调用元数据，不生成 Candidate status 或校验结论。"""

    metadata: dict[str, object] = {
        "model": settings.model_api_name,
        "model_max_tokens": job.policy.model_max_tokens,
        "model_max_retries": job.policy.model_max_retries,
        "model_turns": 1,
    }
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, dict):
        finish_reason = response_metadata.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            metadata["finish_reason"] = finish_reason
    return metadata


async def generate_unit_candidate_once(
    job: UnitAttemptJob,
    *,
    global_feedback: Sequence[ValidationIssue] = (),
    local_feedback: Sequence[ValidationIssue] = (),
    unit_kind_rules: Sequence[str] = (),
    settings: Settings | None = None,
) -> UnitGenerationAttemptResult:
    """为一个已分配 Attempt 执行一次 inline-context Unit generation。

    本函数只构建一个 Unit Prompt、创建一个禁用 SDK retry 的模型并执行一次
    ``ainvoke``。Raw Candidate 只经过严格结构解析；解析失败作为 Unit-scoped
    ``ValidationIssue`` 返回，成功结果也不带 valid status，留给后续 Local Validator。
    """

    frozen_job = UnitAttemptJob.model_validate(job)
    active_settings = settings or Settings.from_env()
    prompt = build_unit_generation_prompt(
        frozen_job.context,
        global_feedback=global_feedback,
        latest_local_feedback=local_feedback,
        unit_kind_rules=unit_kind_rules,
    )

    try:
        model = create_chat_model(
            active_settings,
            max_tokens_override=frozen_job.policy.model_max_tokens,
            max_retries_override=frozen_job.policy.model_max_retries,
            timeout_seconds_override=frozen_job.policy.request_timeout,
        )
    except Exception as exc:
        raise UnitGenerationInfrastructureError(
            identity=frozen_job.identity,
            stage="model_setup",
            cause=exc,
        ) from exc

    try:
        # 第一版只有一个模型 turn；session timeout 仍作为独立外层保护预算生效。
        async with asyncio.timeout(frozen_job.policy.unit_session_timeout):
            response = await model.ainvoke(prompt)
    except Exception as exc:
        raise UnitGenerationInfrastructureError(
            identity=frozen_job.identity,
            stage="model_invoke",
            cause=exc,
        ) from exc

    raw_response = _coerce_content_text(getattr(response, "content", "")) or ""
    try:
        tasks = parse_raw_unit_candidate(
            raw_response,
            unit_id=frozen_job.context.unit_id,
        )
        validation_issues: Sequence[ValidationIssue] = ()
    except RawUnitCandidateParseError as exc:
        # Parser 保证失败时不返回部分任务；外层 Scheduler 决定是否安排下一 Local attempt。
        tasks = []
        validation_issues = exc.issues

    return UnitGenerationAttemptResult(
        identity=frozen_job.identity,
        input_fingerprint=frozen_job.context.input_fingerprint,
        raw_response=raw_response,
        tasks=tasks,
        validation_issues=validation_issues,
        generation_metadata=_generation_metadata(
            response,
            settings=active_settings,
            job=frozen_job,
        ),
    )
