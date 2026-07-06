from __future__ import annotations

import httpx
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

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


class AnthropicProvider:
    def __init__(self, settings: Settings) -> None:
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_auth_token,
            base_url=settings.anthropic_base_url,
            http_client=httpx.AsyncClient(trust_env=settings.anthropic_trust_env),
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
        response = await self.client.messages.create(
            model=model,
            messages=_anthropic_messages(messages),
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools or [],
        )
        text_parts: List[str] = []
        tool_calls: List[ModelToolCall] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(str(getattr(block, "text", "")))
            elif block_type == "tool_use":
                tool_calls.append(
                    ModelToolCall(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        input=getattr(block, "input", {}) or {},
                    )
                )
        return ModelResponse(text="\n".join(text_parts).strip(), tool_calls=tool_calls)


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings) -> None:
        self.client = AsyncOpenAI(
            api_key=settings.anthropic_auth_token,
            base_url=settings.anthropic_base_url,
            http_client=httpx.AsyncClient(trust_env=settings.anthropic_trust_env),
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
        openai_messages = [{"role": "system", "content": system}, *_openai_messages(messages)]
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
        return ModelResponse(text=message.content or "", tool_calls=tool_calls)


def create_model_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(settings)
    if settings.model_provider == "anthropic":
        return AnthropicProvider(settings)
    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider}")


def _openai_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
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


def _anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    pending_tool_results: List[Dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id"),
                    "content": message.get("content") or "",
                    "is_error": bool(message.get("is_error")),
                }
            )
            continue
        if pending_tool_results:
            output.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []
        if message.get("role") == "assistant" and message.get("tool_calls"):
            content: List[Dict[str, Any]] = []
            if message.get("content"):
                content.append({"type": "text", "text": message["content"]})
            content.extend(
                {
                    "type": "tool_use",
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call.get("input") or {},
                }
                for tool_call in message["tool_calls"]
            )
            output.append({"role": "assistant", "content": content})
        else:
            output.append({"role": message.get("role"), "content": message.get("content") or ""})
    if pending_tool_results:
        output.append({"role": "user", "content": pending_tool_results})
    return output


def _json_object(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
