"""技术规划的公开正文与执行活动沿当前 Graph custom stream 实时发送。"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from langgraph.config import get_stream_writer
from pydantic import BaseModel, ConfigDict, Field


OUTPUT_EVENT = "technical-planning.output"


class TechnicalPlanningOutput(BaseModel):
    """只允许展示正文和有界活动摘要，不发送模型推理或工具原始参数。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "activity", "retry"]
    text: str = Field(min_length=1)
    id: str | None = None
    status: Literal["running", "completed", "failed"] | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def emit_technical_planning_activity(
    text: str, *, call_id: str | None = None,
    status: Literal["running", "completed", "failed"] = "running",
    retry: bool = False,
) -> str:
    """在真实操作边界发送轻量进度；纯函数或离线测试中没有 Graph 时不发送。"""

    identity = call_id or uuid4().hex
    payload = TechnicalPlanningOutput(
        kind="retry" if retry else "activity", text=text[:800], id=identity, status=status,
    )
    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return identity
    writer({"type": OUTPUT_EVENT, **payload.model_dump(exclude_none=True)})
    return identity
