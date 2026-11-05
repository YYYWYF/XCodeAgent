"""任务与会话共用的正式 Agent 能力，以及独立的公开运行入口合同。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, Field, StrictBool, field_validator, model_validator
from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel, tuple_input, plain_json
from app.domain.agent_execution_schema import validate_execution_schema


class RuntimeContractModel(FrozenPlanningModel):
    """严格校验当前合同字段和类型，不补齐缺失的正式能力声明。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True, strict=True)


class AgentExecutionContract(RuntimeContractModel):
    """独立声明任务或会话能力，结构化结果与页面及接口传输无关。"""

    mode: Literal["task", "conversation"]
    input_schema: FrozenJsonObject = Field(alias="inputSchema")
    result_schema: FrozenJsonObject | None = Field(default=None, alias="resultSchema")
    text_output: StrictBool = Field(alias="textOutput")
    public_state_schema: None = Field(alias="publicStateSchema")
    outcomes: Annotated[tuple[dict[str, Any], ...], BeforeValidator(tuple_input)] = Field(max_length=0)
    interaction_kinds: Annotated[tuple[Literal["clarification", "confirmation", "approval_required"], ...], BeforeValidator(tuple_input)] = Field(alias="interactionKinds", max_length=3)

    @field_validator("interaction_kinds")
    @classmethod
    def unique_interactions(cls, value: tuple) -> tuple:
        """交互能力显式且唯一；文本输出不禁止内部工具或用户确认。"""
        if len(value) != len(set(value)):
            raise ValueError("interactionKinds 不能重复。")
        return value

    @field_validator("input_schema")
    @classmethod
    def validate_input(cls, value: Any) -> Any:
        """输入 Schema 必须是自包含的业务对象合同。"""
        validate_execution_schema(plain_json(value), object_input=True)
        return value

    @field_validator("result_schema")
    @classmethod
    def validate_result(cls, value: Any) -> Any:
        """结果 Schema 独立校验，文本说明不能冒充结构化结果。"""
        if value is not None:
            validate_execution_schema(plain_json(value))
        return value

    @model_validator(mode="after")
    def validate_output(self) -> "AgentExecutionContract":
        """任务与会话至少交付一种输出；声明结果时仍严格校验。"""
        if self.result_schema is None and not self.text_output:
            raise ValueError("关闭文本输出时必须提供 resultSchema。")
        return self


class RuntimeInvocation(RuntimeContractModel):
    """公开入口采用 AG-UI；内部能力只保留确定性身份，不因此发布 HTTP 接口。"""

    protocol: Literal["ag-ui"]
    transport: Literal["sse"]
    service_id: Literal["agent-runtime"] = Field(alias="serviceId")
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    method: Literal["POST"]
    path: str = Field(min_length=1, max_length=512)
    exposure: Literal["public", "internal"]
    request_type: Literal["RunAgentInput"] = Field(alias="requestType")
    response_type: Literal["BaseEvent"] = Field(alias="responseType")
    principal_source: Literal["runtime_anonymous_session"] = Field(alias="principalSource")
