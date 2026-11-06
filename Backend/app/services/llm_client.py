from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol

from app.agents.model_factory import create_chat_model
from app.agents.messages import _coerce_content_text
from app.config import Settings


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    input: Dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: List[ModelToolCall]


class ModelProvider(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        system: str,
        max_tokens: int,
        temperature: float,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ModelResponse: ...


class ChatModelProvider:
    def __init__(self, settings: Settings) -> None:
        """内部消息辅助接口复用统一工厂，由配置选择直连或转发。"""
        self.client = create_chat_model(settings)
        self.model_transport = settings.model_transport

    async def complete(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        system: str,
        max_tokens: int,
        temperature: float,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """经统一模型生成结果，工具格式转换不改变上下文保存逻辑。"""
        openai_messages = [
            {"role": "system", "content": system},
            *_openai_messages(messages),
        ]
        request: Dict[str, Any] = {
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # 直连保持调用级模型选择；转发的真实模型始终由服务配置决定。
        if self.model_transport == "direct":
            request["model"] = model
        runnable = self.client.bind_tools([_openai_tool(tool) for tool in tools]) if tools else self.client
        message = await runnable.ainvoke(openai_messages, **request)
        tool_calls = [
            ModelToolCall(
                id=str(tool_call["id"]),
                name=str(tool_call["name"]),
                input=tool_call["args"],
            )
            for tool_call in (message.tool_calls or [])
        ]
        # 模型工厂已配置日志回调，这里只归一化正文，避免重复记录输出。
        return ModelResponse(text=_coerce_content_text(message.content) or "", tool_calls=tool_calls)


def create_model_provider(settings: Settings) -> ModelProvider:
    """创建共用模型提供方，使所有辅助调用遵守同一接入方式。"""
    return ChatModelProvider(settings)


def _openai_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "parameters": tool.get("input_schema")
            or {"type": "object", "properties": {}},
        },
    }


def _openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            output.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tool_call["id"],
                            "type": "function",
                            "function": {
                                "name": tool_call["name"],
                                "arguments": json.dumps(
                                    tool_call.get("input") or {},
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                        for tool_call in message["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            output.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": message.get("content") or "",
                }
            )
        else:
            output.append({"role": role, "content": message.get("content") or ""})
    return output


def _json_object(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
