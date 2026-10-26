"""静态返回映射与独立 Mock 数据生成的当前 AG-UI 请求合同。"""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.api_design_fields import ApiDesignModel
from app.domain.api_design_static import StaticBinding
from app.domain.endpoint_design_assist import AssistBaseline, AssistRequest, GenerateMappingRequest, RefineRuleRequest
from app.domain.endpoint_design_assist_steps import StepQuestions


class StaticAssistRequest(AssistRequest):
    """静态来源只有所属 Endpoint 的身份，不接受数据库或外部来源。"""

    selection: StaticBinding


class StaticGenerateRequest(GenerateMappingRequest):
    """基于已应用的静态结构和必填需求生成返回映射。"""

    selection: StaticBinding


class StaticGeneratedMapping(ApiDesignModel):
    """整体替换只携带当前静态内容及返回配置，不生成操作或映射说明。"""

    static_data: dict[str, Any] | list[dict[str, Any]] = Field(alias="staticData")
    field_mappings: list[dict[str, Any]] = Field(alias="fieldMappings", max_length=300)


class StaticReplaceRequest(StaticAssistRequest):
    """当前内容不可借替换映射被改写，应用前核对双摘要。"""

    baseline: AssistBaseline
    mapping: StaticGeneratedMapping


class StaticRuleTarget(ApiDesignModel):
    """静态来源仅对应用返回字段提供规则完善。"""

    area: Literal["return"]
    key: str = Field(min_length=1, max_length=1024)


class StaticRefineRequest(RefineRuleRequest):
    """保留原有依赖及策略，只完善已选静态返回规则的文字。"""

    selection: StaticBinding
    target: StaticRuleTarget


class GenerateMockRequest(StaticGenerateRequest):
    """Mock 以返回契约和用户需求为依据，每组列表生成有限条样例。"""

    row_count: int = Field(default=3, alias="rowCount", strict=True, ge=1, le=20)
    retry_token: None = Field(default=None, alias="retryToken")


class MockStepResult(StepQuestions):
    """模型只生成样例数据，不生成映射；无法构造时返回明确问题。"""

    data: dict[str, Any] | list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def validate_result(self):
        """无数据时必须解释所缺信息，不使用空结果冒充成功。"""
        self.questions = list(dict.fromkeys(question.strip() for question in self.questions if question.strip()))
        if self.data is None and not self.questions:
            raise ValueError("Mock 未返回数据或待明确问题。")
        return self


class ApplyMockRequest(StaticAssistRequest):
    """确定性校验后只替换内存静态内容，映射交给原有校验提示。"""

    baseline: AssistBaseline
    data: dict[str, Any] | list[dict[str, Any]]
