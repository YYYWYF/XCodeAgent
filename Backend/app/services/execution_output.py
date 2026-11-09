"""压缩保存用户可见的 AG-UI 输出，不把展示副本当作执行或确认 authority。"""

from typing import Any
import jsonpatch


class ExecutionOutput:
    """保留最后展示快照、回复及过程步骤，不累积完整原始事件日志。"""

    def __init__(self, value: dict[str, Any] | None = None):
        """从当前任务的紧凑输出初始化恢复视图。"""
        self.value = value or {"messages": {}, "custom": {}, "state": {}, "steps": {}, "tools": {}}

    def apply(self, event: Any) -> None:
        """使用官方事件模型字段合并展示内容，业务确认仍由原节点及凭据校验。"""
        data = event.model_dump(mode="json", by_alias=True, exclude_none=True)
        kind = data["type"]
        if kind == "RUN_STARTED":
            self.value["start"] = data
        elif kind in {"RUN_FINISHED", "RUN_ERROR"}:
            self.value["end"] = data
        elif kind.startswith("TEXT_MESSAGE_"):
            message = self.value["messages"].setdefault(data["messageId"], {"content": ""})
            if kind == "TEXT_MESSAGE_START":
                message["start"] = data
            elif kind == "TEXT_MESSAGE_CONTENT":
                message["content"] += data["delta"]
            elif kind == "TEXT_MESSAGE_END":
                message["end"] = data
        elif kind == "STATE_SNAPSHOT":
            self.value["state"] = data["snapshot"]
        elif kind == "STATE_DELTA":
            self.value["state"] = jsonpatch.apply_patch(self.value["state"], data["delta"], in_place=False)
        elif kind == "CUSTOM":
            name, value = data["name"], data.get("value")
            if name == "llm.token":
                return
            if name == "agent-process" and isinstance(value, dict) and value.get("id"):
                previous = self.value["steps"].get(value["id"], {})
                if value.get("appendDetail"):
                    value = {**value, "detail": (previous.get("detail", "") + value.get("detail", ""))[-24_000:], "appendDetail": False}
                self.value["steps"][value["id"]] = value
            else:
                self.value["custom"][name] = data
        elif kind.startswith("TOOL_CALL_"):
            tool = self.value["tools"].setdefault(data["toolCallId"], {"id": data["toolCallId"], "name": "", "args": "", "status": "running"})
            if kind == "TOOL_CALL_START":
                tool["name"] = data["toolCallName"]
            elif kind == "TOOL_CALL_ARGS":
                tool["args"] += data["delta"]
            elif kind == "TOOL_CALL_RESULT":
                tool["result"] = data["content"]
                tool["status"] = "completed"
            elif kind == "TOOL_CALL_END":
                tool["status"] = "completed"

    def presentation(self, record: dict[str, Any]) -> dict[str, Any]:
        """只公开当前会话展示字段，排除已接受请求正文及后台存储身份。"""
        start = self.value.get("start") or {}
        return {"requestId": record["request_id"], "runId": start.get("runId", record["request_id"]),
            "submissionOrder": record["submission_order"],
            "threadId": record["thread_id"], "ownerSessionId": record["session_id"],
            "startedAt": record["created_at"], "sequence": record["sequence"], "finished": bool(record["finished"]),
            "content": "".join(item["content"] for item in self.value["messages"].values()),
            "processSteps": list(self.value["steps"].values()), "toolCalls": list(self.value["tools"].values())}

    def replay(self, encoder: Any) -> list[str]:
        """重连只回放已经保存的标准事件，不再次调用原业务生成器。"""
        from ag_ui.core import RunStartedEvent, RunFinishedEvent, RunErrorEvent, CustomEvent, StateSnapshotEvent
        from ag_ui.core import TextMessageStartEvent, TextMessageContentEvent, TextMessageEndEvent
        from ag_ui.core import ToolCallStartEvent, ToolCallArgsEvent, ToolCallEndEvent, ToolCallResultEvent
        frames = []
        if self.value.get("start"):
            frames.append(encoder.encode(RunStartedEvent.model_validate(self.value["start"])))
        for message_id, message in self.value["messages"].items():
            frames.append(encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant")))
            if message["content"]:
                frames.append(encoder.encode(TextMessageContentEvent(messageId=message_id, delta=message["content"])))
            if message.get("end"):
                frames.append(encoder.encode(TextMessageEndEvent(messageId=message_id)))
        for event in self.value["custom"].values():
            frames.append(encoder.encode(CustomEvent.model_validate(event)))
        for step in self.value["steps"].values():
            frames.append(encoder.encode(CustomEvent(name="agent-process", value=step)))
        for tool in self.value["tools"].values():
            frames.append(encoder.encode(ToolCallStartEvent(toolCallId=tool["id"], toolCallName=tool["name"])))
            if tool["args"]:
                frames.append(encoder.encode(ToolCallArgsEvent(toolCallId=tool["id"], delta=tool["args"])))
            if tool["status"] == "completed":
                frames.append(encoder.encode(ToolCallEndEvent(toolCallId=tool["id"])))
            if "result" in tool:
                frames.append(encoder.encode(ToolCallResultEvent(toolCallId=tool["id"], messageId=f"tool:{tool['id']}", content=tool["result"], role="tool")))
        frames.append(encoder.encode(StateSnapshotEvent(snapshot=self.value["state"])))
        end = self.value.get("end")
        if end:
            frames.append(encoder.encode((RunFinishedEvent if end["type"] == "RUN_FINISHED" else RunErrorEvent).model_validate(end)))
        return frames
