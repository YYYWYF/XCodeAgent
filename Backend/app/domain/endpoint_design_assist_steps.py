"""模型专用的简洁分步输出；正式映射身份和字段快照由平台补齐。"""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.api_design_database import DatabaseConditionOperator
from app.domain.api_design_fields import ApiDesignModel
from app.domain.endpoint_design_assist import AssistDatabaseOperation, RefinedRule

AssistStep = Literal["operation", "query", "write", "return", "description", "rule"]


class StepValue(ApiDesignModel):
    """仅描述输入引用、固定值或自然语言处理，不接受正式配置属性。"""

    inputs: list[str] = Field(default_factory=list, max_length=100)
    value: Any = None
    rule: str = Field(default="", max_length=2000)
    basis: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_value_shape(self):
        """固定值与依赖处理互斥，多输入必须明确处理规则。"""
        if "value" in self.model_fields_set and (self.inputs or self.rule.strip()):
            raise ValueError("固定值不能同时携带输入或处理规则。")
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError("AI 生成的输入引用不能重复。")
        if len(self.inputs) > 1 and not self.rule.strip():
            raise ValueError("多个输入必须说明处理规则。")
        return self


class StepCondition(StepValue):
    """一条查询仅填写真实列名、运算符和简洁取值。"""

    column: str = Field(min_length=1, max_length=256)
    op: DatabaseConditionOperator

    @model_validator(mode="after")
    def validate_condition_value(self):
        """空值判断不带取值，其他查询必须提供取值方式。"""
        has_value = "value" in self.model_fields_set or bool(self.inputs) or bool(self.rule.strip())
        if self.op in {"is_null", "is_not_null"}:
            if has_value:
                raise ValueError("空值判断不应携带取值。")
        elif not has_value:
            raise ValueError("查询条件缺少取值。")
        return self


class StepQueryGroup(ApiDesignModel):
    """保留最多一层括号子组，不要求模型输出 kind 判别字段。"""

    join: Literal["and", "or"] = "and"
    items: list[StepCondition] = Field(min_length=1, max_length=300)


class StepQuery(ApiDesignModel):
    """查询树作为一个步骤整体生成和校验。"""

    join: Literal["and", "or"] = "and"
    items: list[StepCondition | StepQueryGroup] = Field(min_length=1, max_length=300)


class StepWrite(StepValue):
    """一个写入目标只需真实列名。"""

    column: str = Field(min_length=1, max_length=256)


class StepReturn(StepValue):
    """一个返回目标只需契约字段引用。"""

    field: str = Field(min_length=1, max_length=1024)


class StepQuestions(ApiDesignModel):
    """每个步骤独立收集未明确事实，不臆造配置。"""

    questions: list[str] = Field(default_factory=list, max_length=50)


class QueryStepResult(StepQuestions):
    """查询步骤只允许返回查询和问题。"""

    query: StepQuery | None = None


class OperationStepResult(StepQuestions):
    """先识别单表 CRUD；有歧义时必须停止并提出具体问题。"""

    operation: AssistDatabaseOperation | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_operation(self):
        """未知操作必须提问，避免默认沿用草稿或仅按 HTTP 方法猜测。"""
        self.reason = self.reason.strip()
        self.questions = [question.strip() for question in self.questions if question.strip()]
        if not self.reason or (self.operation is None and not self.questions):
            raise ValueError("无法确定数据库操作时必须提供原因和待明确问题。")
        # 有待明确问题的判断不能进入后续映射生成。
        if self.questions:
            self.operation = None
        return self


class WriteStepResult(StepQuestions):
    """写入步骤只允许返回写入和问题。"""

    writes: list[StepWrite] = Field(default_factory=list, max_length=300)


class ReturnStepResult(StepQuestions):
    """返回步骤只允许返回已明确的目标和问题。"""

    returns: list[StepReturn] = Field(default_factory=list, max_length=300)


class DescriptionStepResult(StepQuestions):
    """说明步骤只输出可读实现说明。"""

    description: str = Field(default="", max_length=4000)


ASSIST_STEP_MODELS = {
    "operation": OperationStepResult,
    "query": QueryStepResult, "write": WriteStepResult, "return": ReturnStepResult,
    "description": DescriptionStepResult, "rule": RefinedRule,
}
