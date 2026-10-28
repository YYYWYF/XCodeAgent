"""控制台模型埋点合同；全部字段允许省略或为 null。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OptionalRecord(BaseModel):
    """限制字段名和类型，不为缺失的观测事实填造默认值。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class Identity(OptionalRecord):
    """保留请求身份，缺失的业务关联保持未知。"""

    user_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    operation_id: str | None = None
    workflow_scope: str | None = None
    current_operation: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=80)


class ModelUsage(OptionalRecord):
    """描述实际请求的模型、上游用量和本地接收时间。"""

    provider: Literal["direct", "gateway"] | None = None
    name: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_source: None = None
    usage_status: None = None
    ttft_ms: int | None = Field(default=None, ge=0)
    output_duration_ms: int | None = Field(default=None, ge=0)


class ExecutionError(OptionalRecord):
    """只保存稳定错误码和不含上游原始正文的安全摘要。"""

    layer: Literal["model", "tool", "validation", "transport", "system"] | None = None
    code: str | None = None
    message: str | None = None


class ActiveInterval(OptionalRecord):
    """预留工作流有效执行区间，模型埋点不填写。"""

    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class ExecutionData(OptionalRecord):
    """统一执行片段结构；本服务只记录模型调用。"""

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    span_kind: Literal["workflow", "node", "model", "tool"] | None = None
    run_name: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    status: Literal["completed", "failed", "cancelled", "interrupted", "awaiting_user"] | None = None
    active_intervals: list[ActiveInterval] | None = None
    model: ModelUsage | None = None
    error: ExecutionError | None = None


class ExecutionRecord(OptionalRecord):
    """独立于 AG-UI 模型结果的控制台和本地历史摘要。"""

    identity: Identity | None = None
    data: ExecutionData | None = None
