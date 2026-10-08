from __future__ import annotations

import base64
import copy
import json
import logging
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.agents.screenshot_model_compat import (
    screenshot_http_timeout,
    screenshot_request_options,
    screenshot_response_format,
)
from app.config import Settings
from app.utils.model_output import extract_json_root_object_with_repair

from .images import UiReferenceImage


TModel = TypeVar("TModel", bound=BaseModel)
logger = logging.getLogger(__name__)


class NonRetryableScreenshotModelError(RuntimeError):
    """表示认证、额度或请求契约错误，重复同一请求无法恢复。"""


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """把 Pydantic Schema 收敛为兼容严格结构化输出的 JSON Schema。"""

    schema.pop("default", None)
    if schema.get("type") == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        schema["additionalProperties"] = False
        schema["required"] = list(properties)
    for value in schema.values():
        if isinstance(value, dict):
            _strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strict_schema(item)
    return schema


def _message_text(value: Any) -> str:
    """兼容字符串与内容块数组形式的模型消息正文。"""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    fragments: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text") or block.get("content")
        if isinstance(text, str):
            fragments.append(text)
    return "".join(fragments)


def _upstream_error(response: httpx.Response) -> str:
    """提取不包含认证信息的视觉模型错误消息。"""

    message = ""
    try:
        payload = response.json()
        error = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or "")
        elif isinstance(error, str):
            message = error
    except (ValueError, TypeError):
        message = response.text[:500]
    return (
        f"截图 UI 视觉模型返回 HTTP {response.status_code}"
        + (f": {message}" if message else "")
    )


def _content_blocks(prompt: str, images: list[UiReferenceImage]) -> list[dict[str, Any]]:
    """把提示词和保色图片编码为 OpenAI 兼容多模态内容块。"""

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {
                "type": "text",
                "text": (
                    f"reference={image.source_name}/{image.label}; "
                    f"source_sha256={image.source_sha256}; "
                    f"viewport={image.width}x{image.height}; "
                    f"preprocess={','.join(image.operations)}"
                ),
            }
        )
        encoded = base64.b64encode(image.content).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image.mime_type};base64,{encoded}",
                    "detail": "high",
                },
            }
        )
    return content


def _post_chat(
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    images: list[UiReferenceImage],
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
    response_name: str = "",
) -> str:
    """调用 OpenAI 兼容视觉接口并对瞬时失败做有限重试。"""

    selected_format = screenshot_response_format(settings) if response_schema else ""
    effective_prompt = user_prompt
    if response_schema and selected_format == "json_object":
        effective_prompt += (
            "\n\n严格按照以下 JSON Schema 返回，不得省略或增加字段：\n"
            + json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
        )
    request_body: dict[str, Any] = {
        "model": settings.screenshot_model_api_name,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": _content_blocks(effective_prompt, images),
            },
        ],
    }
    request_body.update(screenshot_request_options(settings))
    if response_schema:
        request_body["response_format"] = (
            {"type": "json_object"}
            if selected_format == "json_object"
            else {
                "type": "json_schema",
                "json_schema": {
                    "name": response_name or "xcodeagent_screenshot_ui",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        )
    timeout = screenshot_http_timeout(settings)
    attempts = max(1, settings.model_max_retries + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(
                timeout=timeout,
                trust_env=settings.model_trust_env,
            ) as client:
                response = client.post(
                    f"{settings.screenshot_model_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.screenshot_model_api_key}"
                    },
                    json=request_body,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                error_message = _upstream_error(response)
                if response.status_code in {400, 401, 402, 403, 404, 422}:
                    raise NonRetryableScreenshotModelError(error_message) from exc
                raise RuntimeError(error_message) from exc
            payload = response.json()
            choice = payload["choices"][0]
            raw = _message_text(choice["message"]["content"])
            finish_reason = str(choice.get("finish_reason") or "")
            if finish_reason == "length" or not raw:
                logger.warning(
                    "screenshot_ui_model_output_incomplete response_name=%s "
                    "finish_reason=%s output_chars=%s max_tokens=%s",
                    response_name or "text",
                    finish_reason,
                    len(raw),
                    max_tokens,
                )
            if not raw:
                raise RuntimeError(
                    "截图 UI 视觉模型没有返回可解析的文本内容"
                    + (f"（finish_reason={finish_reason}）" if finish_reason else "")
                )
            return raw
        except NonRetryableScreenshotModelError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            time.sleep(min(0.5 * attempt, 2.0))
    raise RuntimeError("截图 UI 视觉模型调用失败") from last_error


def invoke_vision_text(
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    images: list[UiReferenceImage],
    max_tokens: int,
) -> str:
    """调用视觉模型并返回自由文本，供 TSX 生成和修复使用。"""

    return _post_chat(
        settings=settings,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        images=images,
        max_tokens=max_tokens,
    )


def invoke_vision_json(
    model_type: type[TModel],
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    images: list[UiReferenceImage],
    max_tokens: int,
    response_name: str,
) -> TModel:
    """调用视觉模型并按指定 Pydantic 类型校验严格结构化输出。"""

    schema = _strict_schema(copy.deepcopy(model_type.model_json_schema()))
    diagnostics = ""
    for attempt in range(2):
        prompt = user_prompt
        if diagnostics:
            prompt += (
                "\n\nThe previous response failed validation. Return the COMPLETE root JSON "
                "object, not one nested item. Fix these errors: " + diagnostics
            )
        raw = _post_chat(
            settings=settings,
            system_prompt=system_prompt,
            user_prompt=prompt,
            images=images,
            max_tokens=max_tokens,
            response_schema=schema,
            response_name=response_name,
        )
        data = extract_json_root_object_with_repair(raw)
        if data is None:
            diagnostics = "响应不是完整闭合的根 JSON 对象，可能被输出上限截断。"
            continue
        try:
            return model_type.model_validate(data)
        except ValueError as exc:
            diagnostics = str(exc)[:600]
    raise RuntimeError("截图 UI 视觉模型两次返回不符合结构契约：" + diagnostics)
