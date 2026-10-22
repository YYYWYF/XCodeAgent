"""数据库字段映射 AI 辅助的当前请求与受限建议契约。"""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.api_design import DatabaseBinding
from app.domain.api_design_fields import ApiDesignModel

AssistArea = Literal["query", "write", "request", "return", "description"]
AssistDatabaseOperation = Literal["create", "read", "update", "delete"]


class AssistBaseline(ApiDesignModel):
    """绑定生成时的草稿与真实表结构，应用前必须重新对账。"""

    draft_hash: str = Field(alias="draftHash", pattern=r"^[0-9a-f]{64}$")
    metadata_hash: str = Field(alias="metadataHash", pattern=r"^[0-9a-f]{64}$")


class MappingCandidate(ApiDesignModel):
    """仅允许整体查询、单列写入、单个返回目标或接口说明变更。"""

    area: AssistArea
    key: str = Field(min_length=1, max_length=1024)
    value: Any
    reason: str = Field(min_length=1, max_length=2000)


class MappingModelResult(ApiDesignModel):
    """单步骤转换后的内部候选与问题，不作为模型提示中的输出格式。"""

    candidates: list[MappingCandidate] = Field(default_factory=list, max_length=300)
    questions: list[str] = Field(default_factory=list, max_length=50)


class GeneratedMapping(ApiDesignModel):
    """完整生成结果包含识别后的操作与配置，不能携带来源或契约身份。"""

    database_operation: AssistDatabaseOperation = Field(alias="databaseOperation")
    database_query: dict[str, Any] | None = Field(default=None, alias="databaseQuery")
    database_writes: list[dict[str, Any]] = Field(default_factory=list, alias="databaseWrites", max_length=300)
    field_mappings: list[dict[str, Any]] = Field(default_factory=list, alias="fieldMappings", max_length=300)
    implementation_description: str = Field(default="", alias="implementationDescription", max_length=4000)


class AssistRequest(ApiDesignModel):
    """辅助请求携带正在编辑的数据库草稿，不提供写入授权。"""

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    draft: dict[str, Any]
    selection: DatabaseBinding
    base_revision: str | None = Field(default=None, alias="baseRevision", pattern=r"^[0-9a-f]{32}$")
    technical_plan_hash: str = Field(alias="technicalPlanHash", pattern=r"^[0-9a-f]{64}$")


class GenerateMappingRequest(AssistRequest):
    """必填需求说明与后续补充分开传递，基于当前契约生成全部映射。"""

    instruction: str = Field(min_length=1, max_length=4000)
    clarification: str = Field(default="", max_length=4000)
    retry_token: str | None = Field(default=None, alias="retryToken", pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def validate_requirements(self):
        """补充内容不能代替主需求，两类输入共同遵守总长度上限。"""
        self.instruction = self.instruction.strip()
        self.clarification = self.clarification.strip()
        if not self.instruction:
            raise ValueError("请先填写需求说明。")
        if len(self.instruction) + len(self.clarification) > 4000:
            raise ValueError("需求说明与补充内容合计不能超过 4000 字。")
        return self


class ReplaceMappingRequest(AssistRequest):
    """整体替换前重新验证配置与基线，仅返回内存草稿。"""

    baseline: AssistBaseline
    mapping: GeneratedMapping


class RuleDependency(ApiDesignModel):
    """规则的只读输入来自当前接口、当前表或已支持的可信上下文。"""

    kind: Literal["endpoint", "source", "builtin"]
    field: dict[str, Any]


class RuleTarget(ApiDesignModel):
    """用配置区及真实目标身份定位一个业务规则。"""

    area: Literal["query", "write", "return"]
    key: str = Field(min_length=1, max_length=1024)


class RefineRuleRequest(AssistRequest):
    """仅完善已有依赖对应的说明，不替换字段或缺值策略。"""

    target: RuleTarget
    dependencies: list[RuleDependency] = Field(default_factory=list, max_length=100)
    description: str = Field(min_length=1, max_length=2000)


class RefinedRule(ApiDesignModel):
    """规则候选只有说明和问题，不能携带依赖或策略修改。"""

    description: str = Field(default="", max_length=2000)
    questions: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_result(self):
        """缺少可采用规则时必须说明需要用户明确的问题。"""
        if not self.description.strip() and not self.questions:
            raise ValueError("AI 未返回规则或待明确问题。")
        return self
