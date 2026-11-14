"""主Workflow对话中的Agent UI准备确认；只接收当前审阅摘要。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentUiPreparationAction(BaseModel):
    """不接受源码、命令、Graph节点或未经审阅的安装参数。"""

    model_config = ConfigDict(extra='forbid')
    action: Literal['confirm', 'refresh']
    planId: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    reviewId: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')

    @model_validator(mode='after')
    def validate_identity(self) -> 'AgentUiPreparationAction':
        """重新检查可自动执行常规准备，确认仅绑定当前生产依赖审阅。"""
        if self.action == 'refresh':
            if any((self.planId, self.reviewId)):
                raise ValueError('刷新不接受写入计划身份。')
            return self
        if not self.planId or not self.reviewId:
            raise ValueError('确认必须绑定当前生产依赖审阅身份。')
        return self
