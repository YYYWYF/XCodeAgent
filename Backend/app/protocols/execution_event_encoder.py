"""为后端执行管理器保留官方 AG-UI 事件，不反向解析 SSE 文本。"""

from typing import Any
from ag_ui.encoder import EventEncoder as OfficialEventEncoder


class ExecutionFrame(str):
    """携带原始类型事件的标准编码文本，仍可直接交给 StreamingResponse。"""

    event: Any


class EventEncoder(OfficialEventEncoder):
    """复用官方编码器，仅附加后台结果保存需要的类型化事件。"""

    def encode(self, event: Any) -> str:
        """保留编码前事件，避免后台执行管理引入另一套 SSE 协议解析器。"""
        frame = ExecutionFrame(super().encode(event))
        frame.event = event
        return frame
