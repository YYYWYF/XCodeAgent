"""T29 只记录显式人工核验，不接受命令、正文或任意业务数据。"""

from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AcceptanceReport(BaseModel):
    """当前用例、审阅摘要、来源和逐项观察的严格输入。"""

    model_config = ConfigDict(extra='forbid', strict=True)
    caseId: str = Field(pattern=r'^[a-f0-9]{64}$')
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: Literal['code', 'official-mock', 'replay', 'real']
    outcome: Literal['passed', 'failed', 'unknown']
    checks: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(max_length=16)


class RecordedAcceptance(AcceptanceReport):
    """当前持久化核验记录的完整合同，不接受只保留通过状态的残缺文件。"""

    evidenceKey: str = Field(pattern=r'^[a-f0-9]{64}$')
    method: Literal['user_observed']
    threadId: str = Field(min_length=1, max_length=128)
    runId: str = Field(min_length=1, max_length=128)
    checkedAt: str = Field(max_length=64)
    sessionId: str | None = Field(max_length=128)
    launchId: str | None = Field(max_length=128)
    runtimeSource: Literal['official-mock', 'replay', 'real-text', 'formal-text', 'formal-business', 'unknown'] | None

    @field_validator('checkedAt')
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        """记录时间必须为带时区的实际时间，拒绝无法解释的审计字段。"""
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError('核验时间缺少时区。')
        return value


class AcceptanceReviewAction(BaseModel):
    """审阅或保存已观察的事实，不触发模型、Tool 或最终确认。"""

    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['get', 'record']
    workspace: str = Field(min_length=1, max_length=4096)
    report: AcceptanceReport | None = None
