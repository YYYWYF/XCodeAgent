"""主Workflow对话中的Agent UI准备确认；只接收当前审阅摘要。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentUiPreparationAction(BaseModel):
    """不接受源码、命令、Graph节点或未经审阅的安装参数。"""

    model_config = ConfigDict(extra='forbid')
    action: Literal['confirm', 'refresh']
    planId: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    reviewId: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')
    installationPlanId: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    targetSourceRoot: str = Field(default='', max_length=512)

    @model_validator(mode='after')
    def validate_identity(self) -> 'AgentUiPreparationAction':
        """刷新只读，执行必须明确绑定依赖审阅或目标转入审阅之一。"""
        if self.action == 'refresh':
            if any((self.planId, self.reviewId, self.installationPlanId, self.targetSourceRoot)):
                raise ValueError('刷新不接受写入计划身份。')
            return self
        dependency = bool(self.reviewId) and not any((self.installationPlanId, self.targetSourceRoot))
        transfer = bool(self.installationPlanId and self.targetSourceRoot) and not self.reviewId
        if not self.planId or not (dependency or transfer):
            raise ValueError('确认必须绑定唯一依赖或转入审阅身份。')
        return self
