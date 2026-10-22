"""当前单一外部 Operation 的 AI 辅助请求和受限输出。"""

from typing import Any, Literal

from pydantic import Field

from app.domain.api_design import ExternalBinding
from app.domain.api_design_fields import ApiDesignModel
from app.domain.endpoint_design_assist import (
    AssistBaseline, AssistRequest, GenerateMappingRequest, RefineRuleRequest,
)
from app.domain.endpoint_design_assist_steps import StepQuestions, StepReturn


class ExternalGenerateRequest(GenerateMappingRequest):
    """使用同一需求与版本边界，来源必须为当前外部 Operation。"""

    selection: ExternalBinding


class ExternalGeneratedMapping(ApiDesignModel):
    """仅允许外部入参、应用返回与说明，不允许生成来源或数据库操作。"""

    external_api_bindings: list[dict[str, Any]] = Field(default_factory=list, alias="externalApiBindings", max_length=300)
    field_mappings: list[dict[str, Any]] = Field(alias="fieldMappings", max_length=300)
    implementation_description: str = Field(default="", alias="implementationDescription", max_length=4000)


class ExternalAssistIdentity(AssistRequest):
    """外部辅助动作共用的工作区、正式版本和当前草稿身份。"""

    selection: ExternalBinding


class ExternalReplaceMappingRequest(ExternalAssistIdentity):
    """整套替换前重新读取契约与 Operation，只返回内存草稿。"""

    baseline: AssistBaseline
    mapping: ExternalGeneratedMapping


class ExternalRuleTarget(ApiDesignModel):
    """定位外部请求目标或当前应用返回目标。"""

    area: Literal["request", "return"]
    key: str = Field(min_length=1, max_length=1024)


class ExternalRefineRequest(RefineRuleRequest):
    """仅完善当前已选依赖对应的规则说明。"""

    selection: ExternalBinding
    target: ExternalRuleTarget


class ExternalRequestStepResult(StepQuestions):
    """外部请求阶段只生成请求目标，不允许输出返回或操作。"""

    requests: list[StepReturn] = Field(default_factory=list, max_length=300)
