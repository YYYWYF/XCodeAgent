"""首批纯文本 Agent 的公开能力与确定性运行 Endpoint，禁止扩权字段。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, Field, StrictBool, field_validator
from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel, tuple_input


class RuntimeContractModel(FrozenPlanningModel):
    """严格校验当前合同字段和类型，不补齐缺失的正式能力声明。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True, strict=True)


class EmptyAgentInputSchema(RuntimeContractModel):
    """首批没有结构化业务输入，用户提问只通过 AG-UI messages 传递。"""

    type: Literal["object"]
    properties: FrozenJsonObject = Field(max_length=0)
    required: Annotated[tuple[str, ...], BeforeValidator(tuple_input)] = Field(max_length=0)
    additional_properties: StrictBool = Field(alias="additionalProperties")

    @field_validator("additional_properties")
    @classmethod
    def forbid_additional_properties(cls, value: bool) -> bool:
        """禁止通过额外业务输入字段扩大首批能力。"""

        if value is not False:
            raise ValueError("首批 Agent inputSchema.additionalProperties 必须为 false。")
        return value


class TextConversationContract(RuntimeContractModel):
    """只声明文本会话，不以默认值悄悄开放审批、结构化状态或业务结果。"""

    mode: Literal["conversation"]
    input_schema: EmptyAgentInputSchema = Field(alias="inputSchema")
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

    @field_validator("text_output")
    @classmethod
    def require_text_output(cls, value: bool) -> bool:
        """首批必须提供文本输出，不能把空能力声明当成支持。"""

        if value is not True:
            raise ValueError("首批 Agent textOutput 必须为 true。")
        return value


class RuntimeInvocation(RuntimeContractModel):
    """复用既有确定性 AG-UI 调用描述，正式身份由编译器复核。"""

    protocol: Literal["ag-ui"]
    transport: Literal["sse"]
    service_id: Literal["agent-runtime"] = Field(alias="serviceId")
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    method: Literal["POST"]
    path: str = Field(min_length=1, max_length=512)
    exposure: Literal["public"]
    request_type: Literal["RunAgentInput"] = Field(alias="requestType")
    response_type: Literal["BaseEvent"] = Field(alias="responseType")
    principal_source: Literal["runtime_anonymous_session"] = Field(alias="principalSource")
