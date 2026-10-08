from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from app.agents.screenshot_model_compat import (
    screenshot_http_timeout,
    screenshot_request_options,
    screenshot_response_format,
)
from app.config import Settings
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload, clear_clarification

from .models import (
    MAX_SCREENSHOT_BYTES,
    SCREENSHOT_INPUT_ROOT,
    RequirementInput,
    ScreenshotRequirementOutput,
)
from .output_repair import (
    build_output_repair_prompt,
    concise_output_error,
    parse_requirement_output,
)
from .preprocess import PreparedImage, prepare_image
from .prompts import SYSTEM_PROMPT, user_prompt


MAX_PREPARED_IMAGES = 24


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """把 Pydantic Schema 收敛为严格结构化输出支持的字段集合。"""

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


def _resolve_screenshot_path(workspace: str, relative_path: str) -> Path:
    """将截图相对路径限制在当前工作区的专用输入目录内。"""

    root = Path(workspace).expanduser().resolve()
    input_root = (root / SCREENSHOT_INPUT_ROOT).resolve()
    try:
        input_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("工作区截图输入目录不能指向工作区外部") from exc
    unresolved_candidate = root / relative_path
    if unresolved_candidate.is_symlink():
        raise ValueError(f"截图文件不能是符号链接：{relative_path}")
    candidate = unresolved_candidate.resolve()
    try:
        candidate.relative_to(input_root)
    except ValueError as exc:
        raise ValueError(f"截图路径超出允许目录：{relative_path}") from exc
    if not candidate.is_file():
        raise ValueError(f"截图文件不存在或不是普通文件：{relative_path}")
    return candidate


def _load_prepared_images(
    workspace: str,
    requirement_input: RequirementInput,
) -> list[PreparedImage]:
    """校验工作区截图的大小和摘要，并生成受总量限制的模型输入图片。"""

    overview_images: list[PreparedImage] = []
    orientation_images: list[PreparedImage] = []
    tile_images: list[PreparedImage] = []
    for reference in requirement_input.screenshots:
        path = _resolve_screenshot_path(workspace, reference.relative_path)
        raw = path.read_bytes()
        if len(raw) > MAX_SCREENSHOT_BYTES:
            raise ValueError(f"图片 {reference.name} 超过 15 MB")
        if len(raw) != reference.size:
            raise ValueError(f"图片 {reference.name} 的大小与上传清单不一致")
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise ValueError(f"图片 {reference.name} 的内容摘要与上传清单不一致")
        for item in prepare_image(raw, reference.name):
            if item.label == "overview":
                overview_images.append(item)
            elif item.label == "orientation-guide":
                orientation_images.append(item)
            else:
                tile_images.append(item)
    prepared: list[PreparedImage] = []
    seen: set[str] = set()
    # 先保证每张原图至少有全局视图，再用方向图和切片补充文字细节，避免首张长图耗尽配额。
    for item in [*overview_images, *orientation_images, *tile_images]:
        if item.sha256 in seen:
            continue
        prepared.append(item)
        seen.add(item.sha256)
        if len(prepared) >= MAX_PREPARED_IMAGES:
            break
    if not prepared:
        raise ValueError("没有可供分析的有效截图")
    return prepared


def _upstream_error(response: httpx.Response) -> str:
    """从模型错误响应中提取不包含凭据的安全错误信息。"""

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
    suffix = f": {message}" if message else ""
    return f"截图视觉模型返回 HTTP {response.status_code}{suffix}"


def _message_content_text(value: Any) -> str:
    """兼容纯字符串和文本块数组形式的模型消息正文。"""

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


def _invoke_vision_model(
    images: list[PreparedImage],
    context: str,
    settings: Settings,
    on_token: Callable[[str], None] | None,
) -> ScreenshotRequirementOutput:
    """调用 XCodeAgent 配置的 OpenAI 兼容视觉模型并校验结构化输出。"""

    manifest = [
        f"{item.source_name} / {item.label} ({item.width}x{item.height}); "
        f"preprocess={','.join(item.operations) or 'none'}"
        for item in images
    ]
    schema = _strict_schema(ScreenshotRequirementOutput.model_json_schema())
    selected_format = screenshot_response_format(settings)
    prompt = user_prompt(context, manifest)
    if selected_format == "json_object":
        prompt += (
            "\n\n必须严格遵循以下 JSON Schema，不得省略字段或增加字段：\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        encoded = base64.b64encode(image.jpeg_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "high",
                },
            }
        )
    request_body: dict[str, Any] = {
        "model": settings.screenshot_model_api_name,
        "temperature": 0.1,
        "max_tokens": settings.screenshot_max_output_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }
    request_body.update(screenshot_request_options(settings))
    request_body["response_format"] = (
        {"type": "json_object"}
        if selected_format == "json_object"
        else {
            "type": "json_schema",
            "json_schema": {
                "name": "xcodeagent_screenshot_requirement",
                "strict": True,
                "schema": schema,
            },
        }
    )
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    timeout = screenshot_http_timeout(settings)
    for attempt in range(2):
        with httpx.Client(timeout=timeout, trust_env=settings.model_trust_env) as client:
            response = client.post(
                f"{settings.screenshot_model_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.screenshot_model_api_key}"},
                json=request_body,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_upstream_error(response)) from exc
        payload = response.json()
        raw = _message_content_text(payload["choices"][0]["message"]["content"])
        if not raw:
            raise RuntimeError("截图视觉模型没有返回可解析的文本内容")
        try:
            output = parse_requirement_output(raw)
        except (ValidationError, ValueError) as exc:
            if attempt:
                raise RuntimeError(
                    "截图视觉模型自动修复后仍未满足需求文档结构："
                    + concise_output_error(exc)
                ) from exc
            # 仅对结构错误追加一次纯文本修复，不再重复上传图片和视觉编码。
            request_body = {
                **request_body,
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT + "\n本轮只修复上轮输出的 JSON 结构。",
                    },
                    {
                        "role": "user",
                        "content": build_output_repair_prompt(raw, exc, schema_text),
                    },
                ],
            }
            continue
        if on_token is not None:
            on_token(raw)
        return output
    raise RuntimeError("截图视觉模型未返回有效的需求文档候选")


def _clarification_payload(
    questions: list[str],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """将视觉模型的文本问题转换为 XCodeAgent 已有的澄清问题协议。"""

    normalized = [question.strip() for question in questions if question.strip()][:8]
    if not normalized:
        return clear_clarification(spec)
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                question=question,
                header=f"截图问题 {index}",
                type="text",
                placeholder="请补充截图中无法确认的业务信息",
            )
            for index, question in enumerate(normalized, start=1)
        ]
    )
    payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
    return payload


def analyze_requirements_from_screenshots(
    request: str,
    requirement_input: dict[str, Any],
    *,
    workspace: str,
    existing_spec: dict[str, Any] | None = None,
    datasource_type: str = "database",
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """从工作区截图生成与现有需求分析器返回值一致的 RequirementSpec 结果。"""

    settings = Settings.from_env()
    parsed_input = RequirementInput.model_validate(requirement_input)
    if parsed_input.mode != "screenshot":
        raise ValueError("截图需求分析器只能处理 screenshot 模式")
    images = _load_prepared_images(workspace, parsed_input)
    output = _invoke_vision_model(images, request, settings, on_token)
    agent_spec = output.model_dump(
        exclude={
            "clarification_questions",
            "unresolved_requirement_dimensions",
            "agent_note",
        }
    )
    _expose_page_evidence(agent_spec["pages"])
    spec = create_requirement_spec(
        request,
        agent_note=output.agent_note or "screenshot requirements analysis",
        agent_spec=agent_spec,
        existing_spec=existing_spec,
        authoritative_agent_spec=True,
        datasource_type=datasource_type,
        allow_inferred_defaults=False,
    )
    unresolved_questions = [
        f"请补充确认：{dimension}"
        for dimension in output.unresolved_requirement_dimensions
        if dimension.strip()
    ]
    clarification = _clarification_payload(
        [*output.clarification_questions, *unresolved_questions],
        spec,
    )
    spec["clarification_questions"] = clarification["questions"]
    spec["clarification_status"] = clarification["status"]
    spec["unresolved_requirement_dimensions"] = output.unresolved_requirement_dimensions
    spec["analyzed_by"] = {
        "agent": "recognize-screenshot-agent",
        "mode": "multimodal",
        "model": settings.screenshot_model_api_name,
        "source": "embedded-openai-compatible-api",
    }
    spec["analysis_source"] = "screenshot_vision_model"
    spec["agent_spec_used"] = True
    clarification["requested_by"] = "recognize-screenshot-agent"
    clarification["analysis_source"] = "screenshot_vision_model"
    return {
        "requirement_spec": spec,
        "clarification": clarification,
        "authorization_config_conflict": None,
    }
def _expose_page_evidence(pages: list[dict[str, Any]]) -> None:
    """把截图可见字段和控件写入页面描述，避免需求确认及产品规划时丢失证据。"""

    for page in pages:
        information = [
            str(value).strip()
            for value in page.get("visible_information", [])
            if str(value).strip()
        ]
        controls = [
            str(value).strip()
            for value in page.get("visible_controls", [])
            if str(value).strip()
        ]
        evidence = []
        if information:
            evidence.append("截图可见信息：" + "、".join(information))
        if controls:
            evidence.append("截图可交互控件：" + "、".join(controls))
        if evidence:
            page["description"] = "；".join(
                [str(page.get("description") or "").strip(), *evidence]
            )

