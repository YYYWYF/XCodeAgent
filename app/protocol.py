"""使用官方 AG-UI 模型和编码器生成生命周期与增量事件。"""

from collections.abc import Iterator
from typing import Any

from ag_ui.core import (CustomEvent, RunFinishedEvent, RunStartedEvent, StateSnapshotEvent,
                        TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent,
                        ToolCallArgsEvent, ToolCallEndEvent, ToolCallStartEvent)

from .aggregation import CompletionAccumulator


def start_events(thread_id: str, run_id: str) -> Iterator[Any]:
    """每个模型动作先建立运行身份与唯一助手消息。"""
    yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
    yield TextMessageStartEvent(message_id=f"{run_id}:assistant", role="assistant")


def end_events(thread_id: str, run_id: str, status: str) -> Iterator[Any]:
    """正常及已处理失败统一关闭消息并发出最终状态与结束事件。"""
    yield TextMessageEndEvent(message_id=f"{run_id}:assistant")
    yield StateSnapshotEvent(snapshot={"status": status, "runId": run_id, "threadId": thread_id})
    yield RunFinishedEvent(thread_id=thread_id, run_id=run_id)


class ResponseProjection:
    """映射首个 choice 的可视文本和工具事件，原始 custom 保留全部 choices。"""

    def __init__(self, run_id: str):
        """记录已经发送的工具参数长度，避免分片重复输出。"""
        self.message_id = f"{run_id}:assistant"
        self.started: set[tuple[int, int]] = set()
        self.argument_lengths: dict[tuple[int, int], int] = {}

    def chunk_events(self, chunk: dict[str, Any], accumulator: CompletionAccumulator) -> Iterator[Any]:
        """原样发送 SDK 增量，并为 AG-UI 订阅者附加标准可视事件。"""
        yield CustomEvent(name="model-inference.chunk", value=chunk)
        for choice in chunk.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if isinstance(delta.get("content"), str) and delta["content"]:
                yield TextMessageContentEvent(message_id=self.message_id, delta=delta["content"])
        yield from self.tool_events(accumulator)

    def tool_events(self, accumulator: CompletionAccumulator, final: bool = False) -> Iterator[Any]:
        """工具名称就绪且开始参数时发出启动事件，完整保留 JSON 字符串分片。"""
        for key, tool in accumulator.tools.items():
            if key[0] != 0:
                continue
            function = tool.get("function", {})
            arguments = function.get("arguments", "")
            if key not in self.started:
                if not tool.get("id") or not function.get("name") or not (arguments or final):
                    continue
                self.started.add(key)
                yield ToolCallStartEvent(tool_call_id=tool["id"], tool_call_name=function["name"], parent_message_id=self.message_id)
            emitted = self.argument_lengths.get(key, 0)
            if len(arguments) > emitted:
                yield ToolCallArgsEvent(tool_call_id=tool["id"], delta=arguments[emitted:])
                self.argument_lengths[key] = len(arguments)

    def result_events(self, accumulator: CompletionAccumulator, streaming: bool) -> Iterator[Any]:
        """结束工具并发布完整响应，非流式同样产生标准文本与工具事件。"""
        if not streaming:
            for choice in accumulator.output.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                message = choice.get("message", {})
                if isinstance(message.get("content"), str) and message["content"]:
                    yield TextMessageContentEvent(message_id=self.message_id, delta=message["content"])
                for index, tool in enumerate(message.get("tool_calls", [])):
                    accumulator.tools[(0, index)] = tool
        yield from self.tool_events(accumulator, final=True)
        yield from self.close_tools(accumulator)
        yield CustomEvent(name="model-inference.result", value=accumulator.output)

    def close_tools(self, accumulator: CompletionAccumulator) -> Iterator[Any]:
        """在成功或已处理失败时关闭已启动的工具事件。"""
        for key in self.started:
            yield ToolCallEndEvent(tool_call_id=accumulator.tools[key]["id"])
        self.started.clear()
