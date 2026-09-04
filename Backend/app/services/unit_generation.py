"""执行一次单 Unit Candidate Generation Session，不负责重试、调度或校验编排。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Literal

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.frozen_contract_reader import FrozenContractReader
from app.services.frozen_contract_store import FrozenContractStore
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
from app.services.unit_generation_tool_session import (
    ContractToolSession,
    UnitGenerationPlatformError,
    model_turn_limit_issue,
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
    model_turns: int,
    reader: FrozenContractReader | None,
) -> dict[str, object]:
    """记录非判定性调用元数据，不生成 Candidate status 或校验结论。"""

    metadata: dict[str, object] = {
        "model": settings.model_api_name,
        "model_max_tokens": job.policy.model_max_tokens,
        "model_max_retries": job.policy.model_max_retries,
        "model_turns": model_turns,
    }
    if reader is not None:
        metadata["contract_reads"] = reader.read_count
        metadata["contract_bytes"] = reader.accumulated_bytes
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, dict):
        finish_reason = response_metadata.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            metadata["finish_reason"] = finish_reason
    return metadata


def _truncated_output_issue(unit_id: str) -> ValidationIssue:
    """把 provider 明示的长度截断归因成当前 Unit 的可重试内容失败。"""

    return ValidationIssue(
        code="UNIT_CANDIDATE_OUTPUT_TRUNCATED",
        level="unit",
        category="generation",
        unit_ids=(unit_id,),
        task_ids=(),
        retry_unit_ids=(unit_id,),
        retryable=True,
        message="模型输出因长度限制被截断，不能作为完整 Unit Candidate。",
        details={"finish_reason": "length"},
    )


async def generate_unit_candidate_once(
    job: UnitAttemptJob,
    *,
    global_feedback: Sequence[ValidationIssue] = (),
    local_feedback: Sequence[ValidationIssue] = (),
    unit_kind_rules: Sequence[str] = (),
    settings: Settings | None = None,
    frozen_contract_store: FrozenContractStore | None = None,
) -> UnitGenerationAttemptResult:
    """为一个已分配 Attempt 执行有界 Model/Reader 多轮并只返回一个最终结果。

    Store 由 PlanningRun 调用链显式绑定；每个调用创建独立 Reader。模型可在
    ``model_turn_limit`` 内零次或多次调用唯一的冻结合同读取工具，所有 turn 仍共享
    当前 Attempt 身份和 Session timeout。预算耗尽、无效 ToolCall、截断或最终解析失败
    均是 Unit 内容失败；Store/catalog 损坏是 platform fatal；成功结果仍交给 Local Validator。
    未传 Store 仅保留既有直接调用的单轮兼容行为，不开放任何工具。
    """

    frozen_job = UnitAttemptJob.model_validate(job)
    active_settings = settings or Settings.from_env()
    prompt = build_unit_generation_prompt(
        frozen_job.context,
        global_feedback=global_feedback,
        latest_local_feedback=local_feedback,
        unit_kind_rules=unit_kind_rules,
        contract_tool_enabled=frozen_contract_store is not None,
    )
    tool_session = (
        ContractToolSession(
            frozen_job,
            frozen_contract_store=frozen_contract_store,
            prompt=prompt,
        )
        if frozen_contract_store is not None
        else None
    )

    try:
        model = create_chat_model(
            active_settings,
            max_tokens_override=frozen_job.policy.model_max_tokens,
            max_retries_override=frozen_job.policy.model_max_retries,
            timeout_seconds_override=frozen_job.policy.request_timeout,
        )
        runnable = (
            model.bind_tools(list(tool_session.tools))
            if tool_session is not None
            else model
        )
    except Exception as exc:
        raise UnitGenerationInfrastructureError(
            identity=frozen_job.identity,
            stage="model_setup",
            cause=exc,
        ) from exc

    model_turns = 0
    response: object
    raw_response = ""
    session_issue: ValidationIssue | None = None
    try:
        async with asyncio.timeout(frozen_job.policy.unit_session_timeout):
            while model_turns < frozen_job.policy.model_turn_limit:
                model_input = tool_session.messages if tool_session is not None else prompt
                try:
                    response = await runnable.ainvoke(model_input)
                except Exception as exc:
                    raise UnitGenerationInfrastructureError(
                        identity=frozen_job.identity,
                        stage="model_invoke",
                        cause=exc,
                    ) from exc
                model_turns += 1
                raw_response = _coerce_content_text(getattr(response, "content", "")) or ""
                metadata = _generation_metadata(
                    response,
                    settings=active_settings,
                    job=frozen_job,
                    model_turns=model_turns,
                    reader=(tool_session.reader if tool_session is not None else None),
                )
                if metadata.get("finish_reason") == "length":
                    session_issue = _truncated_output_issue(frozen_job.context.unit_id)
                    break
                if tool_session is None:
                    break
                tool_turn = tool_session.accept(response)
                if tool_turn.issue is not None:
                    session_issue = tool_turn.issue
                    break
                if not tool_turn.requested:
                    break
                if model_turns == frozen_job.policy.model_turn_limit:
                    session_issue = model_turn_limit_issue(
                        unit_id=frozen_job.context.unit_id,
                        model_turn_limit=frozen_job.policy.model_turn_limit,
                    )
                    break
    except UnitGenerationInfrastructureError:
        raise
    except TimeoutError as exc:
        raise UnitGenerationInfrastructureError(
            identity=frozen_job.identity,
            stage="model_invoke",
            cause=exc,
        ) from exc

    generation_metadata = _generation_metadata(
        response,
        settings=active_settings,
        job=frozen_job,
        model_turns=model_turns,
        reader=(tool_session.reader if tool_session is not None else None),
    )
    if session_issue is not None:
        tasks = []
        validation_issues: Sequence[ValidationIssue] = (session_issue,)
    else:
        try:
            tasks = parse_raw_unit_candidate(
                raw_response,
                unit_id=frozen_job.context.unit_id,
            )
            validation_issues = ()
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
        generation_metadata=generation_metadata,
    )
