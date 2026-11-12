"""单任务计划修复的请求与模型输出；正式身份和验收合同由平台保留。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RepairModel(BaseModel):
    """拒绝额外字段和隐式类型转换，防止模型提交平台运行字段。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class BuildTaskRepairInput(RepairModel):
    """只接受原执行身份、目标任务和显式的草稿确认身份。"""

    action: Literal["get", "generate", "regenerate", "discard", "confirm"]
    workspaceRoot: str = Field(min_length=1)
    sourceRunId: str = Field(min_length=1)
    sourceThreadId: str = Field(min_length=1)
    sessionId: str = Field(min_length=1)
    taskId: str = Field(min_length=1)
    feedback: str = Field(default="", max_length=4000)
    draftDigest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_action(self):
        """确认、撤销和重新生成必须绑定用户实际看过的那份草稿。"""
        if (self.action in {"confirm", "discard", "regenerate"}) != (self.draftDigest is not None):
            raise ValueError("确认、放弃或重新生成必须提供当前草稿摘要，其他动作不得提供。")
        if self.feedback and self.action not in {"generate", "regenerate"}:
            raise ValueError("只有生成修复草稿可以携带补充要求。")
        return self


class TaskRepairFile(RepairModel):
    """模型仅规划精确文件和原交付物的对应关系，不提供代码。"""

    path: str = Field(min_length=1)
    operation: Literal["add", "modify"]
    description: str = Field(min_length=1, max_length=6000)
    deliverableIds: list[str] = Field(min_length=1)


class TaskRepairProposal(RepairModel):
    """模型可以请求一次定向阅读，或返回待平台校验的单任务文件计划。"""

    decision: Literal["propose", "inspect", "blocked"]
    reason: str = Field(min_length=1, max_length=4000)
    repairInstructions: str = Field(default="", max_length=2000)
    files: list[TaskRepairFile] = Field(default_factory=list, max_length=40)
    inspectPaths: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_decision(self):
        """禁止把诊断、读取申请和可执行计划混成不明确的半成品。"""
        if self.decision == "propose":
            if not self.repairInstructions or self.inspectPaths:
                raise ValueError("修复方案必须提供增量修复说明，不能继续申请读取。")
        elif self.files or self.repairInstructions:
            raise ValueError("非方案结果不得携带任务改动。")
        if (self.decision == "inspect") != bool(self.inspectPaths):
            raise ValueError("只有定向阅读结果必须携带精确文件路径。")
        return self
