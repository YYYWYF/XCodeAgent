"""业务 Endpoint 调用 Agent 的正式映射，与页面载体无关。"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentCallBinding(BaseModel):
    """以目标 JSON Pointer 到来源 JSON Pointer 的字典描述参数及结果映射。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    agent_id: str = Field(alias="agentId", pattern=r"^[a-z][a-z0-9_]{0,127}$")
    input_bindings: dict[str, str] = Field(alias="inputBindings", min_length=1, max_length=300)
    result_bindings: dict[str, str] = Field(alias="resultBindings", min_length=1, max_length=300)
    response_mode: Literal["json", "ag-ui"] = Field(alias="responseMode")

    @field_validator("input_bindings", "result_bindings")
    @classmethod
    def validate_pointers(cls, value: dict[str, str]) -> dict[str, str]:
        """拒绝非法转义和重叠写目标，禁止映射结果取决于字典遍历顺序。"""
        for pointer in (*value, *value.values()):
            if len(pointer) > 1024 or (pointer and not pointer.startswith("/")) or re.search(r"~(?![01])", pointer):
                raise ValueError("Agent 映射必须使用合法 JSON Pointer，空字符串代表整个对象。")
        targets = sorted(value)
        for index, target in enumerate(targets):
            if any(other.startswith(target + "/") for other in targets[index + 1:]):
                raise ValueError("Agent 映射不能同时写入父对象和子字段。")
        return value
