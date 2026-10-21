"""聚合 SDK 模型响应，同时保留文本、工具分片及扩展字段。"""

import copy
from typing import Any


class CompletionAccumulator:
    """把流式增量折叠为可保存的完整或部分 Chat Completion。"""

    def __init__(self, model: str):
        """初始化空结果与按 choice/tool index 定位的增量缓存。"""
        self.output: dict[str, Any] = {"object": "chat.completion", "model": model, "choices": []}
        self.choices: dict[int, dict[str, Any]] = {}
        self.tools: dict[tuple[int, int], dict[str, Any]] = {}
        self.complete = False

    def add(self, chunk: dict[str, Any]) -> None:
        """保留非流式原文，或按官方 delta 语义拼接字符串和工具参数。"""
        if chunk.get("object") != "chat.completion.chunk":
            self.output = copy.deepcopy(chunk)
            self.complete = bool(chunk.get("choices")) and all(
                isinstance(choice.get("message"), dict) and choice.get("finish_reason") is not None
                for choice in chunk["choices"])
            return
        for key, value in chunk.items():
            if key not in {"object", "choices"}:
                self.output[key] = copy.deepcopy(value)
        for item in chunk.get("choices", []):
            index = item.get("index", 0)
            choice = self.choices.setdefault(index, {"index": index, "message": {"role": "assistant", "content": None}, "finish_reason": None})
            delta = item.get("delta", {})
            message = choice["message"]
            for key, value in delta.items():
                if key == "tool_calls":
                    for tool in value:
                        self._add_tool(index, tool, message)
                elif key == "function_call" and isinstance(value, dict):
                    target = message.setdefault(key, {})
                    for field, fragment in value.items():
                        target[field] = target.get(field, "") + fragment if isinstance(fragment, str) else fragment
                elif isinstance(value, str) and key != "role":
                    message[key] = (message.get(key) or "") + value
                elif value is not None:
                    message[key] = copy.deepcopy(value)
            for key in ("finish_reason", "logprobs"):
                if item.get(key) is not None:
                    choice[key] = copy.deepcopy(item[key])
        self.output["choices"] = [self.choices[index] for index in sorted(self.choices)]
        self.complete = bool(self.choices) and all(choice["finish_reason"] is not None for choice in self.choices.values())

    def _add_tool(self, choice_index: int, item: dict[str, Any], message: dict[str, Any]) -> None:
        """依据工具索引拼接名称与参数，保留并行调用的稳定身份。"""
        key = (choice_index, item.get("index", 0))
        tool = self.tools.get(key)
        if tool is None:
            tool = {"type": "function", "function": {"name": "", "arguments": ""}}
            self.tools[key] = tool
            message.setdefault("tool_calls", []).append(tool)
        for field in ("id", "type"):
            if item.get(field):
                tool[field] = item[field]
        for field, fragment in item.get("function", {}).items():
            tool["function"][field] = tool["function"].get(field, "") + fragment if isinstance(fragment, str) else copy.deepcopy(fragment)
