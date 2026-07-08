from __future__ import annotations

import httpx
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol

from openai import AsyncOpenAI

from app.config import Settings
from app.services.model_output_logger import log_model_output


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


class OpenAIProvider:
    def __init__(self, settings: Settings) -> None:
        self.output_log_enabled = settings.model_output_log_enabled
        self.client = AsyncOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            http_client=httpx.AsyncClient(trust_env=settings.model_trust_env),
        )

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
        openai_messages = [
            {"role": "system", "content": system},
            *_openai_messages(messages),
        ]
        request: Dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            request["tools"] = [_openai_tool(tool) for tool in tools]
        response = await self.client.chat.completions.create(**request)
        message = response.choices[0].message
        tool_calls = [
            ModelToolCall(
                id=str(tool_call.id),
                name=str(tool_call.function.name),
                input=_json_object(tool_call.function.arguments),
            )
            for tool_call in (message.tool_calls or [])
        ]
        if self.output_log_enabled:
            log_model_output(
                content=message.content or "",
                tool_calls=[
                    {"id": tool_call.id, "name": tool_call.function.name}
                    for tool_call in (message.tool_calls or [])
                ],
            )
        return ModelResponse(text=message.content or "", tool_calls=tool_calls)


def create_model_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "openai":
        return OpenAIProvider(settings)
    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider}")


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
