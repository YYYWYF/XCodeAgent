"""字段取值模型共用的严格基类与 Endpoint 快照。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ApiDesignModel(BaseModel):
    """为 API 字段映射模型提供严格字段和驼峰别名规则。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class EndpointField(ApiDesignModel):
    """描述正式字段映射内嵌的 Endpoint 字段快照。"""

    side: Literal["request", "response"]
    location: Literal["path", "query", "header", "request_body", "response_body"]
    path: str = Field(min_length=1, max_length=1024)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    required: bool = False
    description: str = Field(default="", max_length=2048)


