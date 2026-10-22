"""一期埋点上传对象；字段存在性由调用方决定。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class OptionalTelemetryModel(BaseModel):
    """禁止拼错字段，同时允许合同中的每个字段省略。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class TelemetryProducer(OptionalTelemetryModel):
    """描述事实发生时的采集进程。"""

    source: Literal["frontend", "backend", "worker"] | None = None
    environment: Literal["development", "test", "production"] | None = None
    platform: Literal["macos", "windows", "linux"] | None = None
    xcodeagent_version: str | None = None


class TelemetryIdentity(OptionalTelemetryModel):
    """关联应用、对话与执行身份。"""

    user_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    operation_id: str | None = None
    workflow_scope: str | None = None


class TelemetryTarget(OptionalTelemetryModel):
    """描述当前开发目标。"""

    type: Literal["application", "page", "endpoint", "entity"] | None = None
    id: str | None = None
    api_contract_id: str | None = None


class TelemetryContext(OptionalTelemetryModel):
    """描述业务阶段、节点、模式和目标。"""

    business_stage: str | None = None
    node_name: str | None = None
    mode: Literal["ultra", "pro", "fast"] | None = None
    target: TelemetryTarget | None = None


class TelemetryError(OptionalTelemetryModel):
    """只承载调用方已脱敏的错误摘要。"""

    layer: Literal["model", "tool", "validation", "transport", "system"] | None = None
    code: str | None = None
    message: str | None = None


class TelemetryActiveInterval(OptionalTelemetryModel):
    """描述一段实际有效执行时间。"""

    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None


class TelemetryModelUsage(OptionalTelemetryModel):
    """描述一次模型调用的用量和流式耗时。"""

    provider: str | None = None
    name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_source: Literal["provider", "estimated", "unavailable"] | None = None
    usage_status: Literal["complete", "partial", "unavailable"] | None = None
    ttft_ms: int | None = None
    output_duration_ms: int | None = None


class ExecutionTraceData(OptionalTelemetryModel):
    """描述 workflow、node、model 或 tool 的执行片段。"""

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    span_kind: Literal["workflow", "node", "model", "tool"] | None = None
    run_name: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    status: Literal["completed", "failed", "cancelled", "interrupted", "awaiting_user"] | None = None
    active_intervals: list[TelemetryActiveInterval] | None = None
    model: TelemetryModelUsage | None = None
    error: TelemetryError | None = None


class TelemetryEventPayload(OptionalTelemetryModel):
    """容纳一期业务事件的可选 payload 字段并集。"""

    creation_source: str | None = None
    turn_kind: Literal["request", "clarification_answer", "confirmation", "feedback"] | None = None
    repo_id: str | None = None
    commit_sha: str | None = None
    generated_lines: int | None = None
    written_lines: int | None = None
    committed_lines: int | None = None
    measurement_method: str | None = None
    code_inclusion_rate: float | None = None
    scope: Literal["operation", "workflow"] | None = None
    failed_span_id: str | None = None
    error: TelemetryError | None = None


class TelemetryEventData(OptionalTelemetryModel):
    """描述业务事实事件及其适用的 payload。"""

    event_name: str | None = None
    payload: TelemetryEventPayload | None = None


class TelemetryRecordBase(OptionalTelemetryModel):
    """定义 Trace 与业务事件共享的记录外层。"""

    record_id: str | None = None
    occurred_at: str | None = None
    producer: TelemetryProducer | None = None
    identity: TelemetryIdentity | None = None
    context: TelemetryContext | None = None


class ExecutionTraceRecord(TelemetryRecordBase):
    """一期执行 Trace；类型由发送入口补齐。"""

    type: Literal["execution_trace"] | None = None
    data: ExecutionTraceData | None = None


class TelemetryEventRecord(TelemetryRecordBase):
    """一期业务事实事件；类型由发送入口补齐。"""

    type: Literal["telemetry_event"] | None = None
    data: TelemetryEventData | None = None


class TelemetryBatch(OptionalTelemetryModel):
    """一期批次外层；传输元信息由发送器补齐。"""

    schema_version: Literal[1] | None = None
    batch_id: str | None = None
    sent_at: str | None = None
    records: list[ExecutionTraceRecord | TelemetryEventRecord] | None = None
