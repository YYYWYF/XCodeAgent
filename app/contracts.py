"""转发调用边界；不添加业务上下文或业务默认提示词。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CallMetadata(BaseModel):
    """接收调用身份与预留关联信息，不接收任意 HTTP 请求头。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    spaid: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    request_id: str | None = Field(default=None, alias="requestId")
    user_id: str | None = Field(default=None, alias="userId")
    project_id: str | None = Field(default=None, alias="projectId")
    studio_data_ref: str | None = Field(default=None, alias="studioDataRef")
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("spaid", "session_id", "request_id", "user_id", "project_id", "studio_data_ref")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        """限制身份长度并拒绝请求头换行注入。"""
        if value is not None and (not value.strip() or len(value) > 1024 or "\r" in value or "\n" in value):
            raise ValueError("调用身份必须是长度不超过 1024 的非空单行字符串")
        return value


class InferenceInput(BaseModel):
    """承载原始模型参数，超时与重试预算仅用于网络调用。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    request: dict[str, Any]
    timeout_seconds: float = Field(default=300, alias="timeoutSeconds", gt=0)
    max_retries: int = Field(default=0, alias="maxRetries", ge=0)
    metadata: CallMetadata = Field(default_factory=CallMetadata)

    @field_validator("request")
    @classmethod
    def validate_request(cls, value: dict[str, Any]) -> dict[str, Any]:
        """要求明确消息及标准流开关，禁止 SDK/传输参数越权。"""
        if not isinstance(value.get("messages"), list) or not value["messages"]:
            raise ValueError("request.messages 必须为非空消息列表")
        forbidden = {"extra_headers", "extra_query", "extra_body", "timeout", "api_key", "base_url", "http_client", "max_retries", "headers"}
        if forbidden.intersection(value):
            raise ValueError("request 不允许覆盖上游连接或认证参数")
        if "stream" in value and not isinstance(value["stream"], bool):
            raise ValueError("request.stream 必须为布尔值")
        return value
