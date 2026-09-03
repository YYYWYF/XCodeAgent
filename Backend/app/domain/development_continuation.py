"""实体绑定完成后恢复原开发 execution 的公开请求合同。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DevelopmentContinuationRequestModel(BaseModel):
    """为 continuation 请求提供严格的驼峰字段和额外字段拒绝策略。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class DevelopmentContinuationReference(DevelopmentContinuationRequestModel):
    """引用门禁签发的服务端 continuation，不允许客户端补写目标。"""

    id: str = Field(min_length=1, max_length=256)


class DevelopmentContinuationConsumeRequest(DevelopmentContinuationReference):
    """提交服务端签发的一次性 token，恢复原开发 execution。"""

    token: str = Field(min_length=32, max_length=1024)
