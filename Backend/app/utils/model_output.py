from __future__ import annotations

from hashlib import sha256
import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """提取首个可解析对象，并记录最外层解析失败后回退到嵌套对象的诊断信息。"""

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    first_error: json.JSONDecodeError | None = None
    first_error_index: int | None = None
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError as exc:
            if first_error is None:
                first_error = exc
                first_error_index = index
            continue
        if isinstance(parsed, dict):
            if first_error is not None and first_error_index is not None:
                logger.warning(
                    "model_json_nested_object_fallback response_sha256=%s "
                    "first_object_start=%s root_error_position=%s root_error=%s "
                    "root_error_context=%s fallback_object_start=%s fallback_keys=%s",
                    _response_fingerprint(stripped),
                    first_error_index,
                    first_error_index + first_error.pos,
                    first_error.msg,
                    _redacted_error_context(
                        stripped,
                        first_error_index + first_error.pos,
                    ),
                    index,
                    sorted(str(key) for key in parsed),
                )
            return parsed
    if first_error is not None and first_error_index is not None:
        logger.warning(
            "model_json_object_decode_failed response_sha256=%s first_object_start=%s "
            "root_error_position=%s root_error=%s root_error_context=%s",
            _response_fingerprint(stripped),
            first_error_index,
            first_error_index + first_error.pos,
            first_error.msg,
            _redacted_error_context(stripped, first_error_index + first_error.pos),
        )
    return None


def repair_unescaped_json_string_quotes(text: str) -> str:
    """仅转义 JSON 字符串内部明显未转义的双引号，供严格协议做一次受控恢复。"""

    repaired: list[str] = []
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if not in_string:
            repaired.append(character)
            if character == '"':
                in_string = True
            continue
        if escaped:
            repaired.append(character)
            escaped = False
            continue
        if character == "\\":
            repaired.append(character)
            escaped = True
            continue
        if character != '"':
            repaired.append(character)
            continue

        next_character = _next_non_whitespace_character(text, index + 1)
        if next_character is None or next_character in {":", ",", "}", "]"}:
            repaired.append(character)
            in_string = False
            continue
        # JSON 字符串闭合引号后只能跟结构字符；其他字符说明模型把正文引号直接写进了字符串。
        repaired.append('\\"')
    return "".join(repaired)


def _next_non_whitespace_character(text: str, start: int) -> str | None:
    """返回指定位置后的首个非空白字符，文本结束时返回 None。"""

    for character in text[start:]:
        if not character.isspace():
            return character
    return None


def _response_fingerprint(text: str) -> str:
    """生成模型响应短哈希，以便关联日志且不记录可能包含敏感信息的正文。"""

    return sha256(text.encode("utf-8")).hexdigest()[:16]


def _redacted_error_context(text: str, position: int) -> str:
    """截取 JSON 错误位置附近的结构上下文，并遮蔽引号内的模型内容。"""

    start = max(0, position - 120)
    end = min(len(text), position + 120)
    fragment = text[start:end]
    masked: list[str] = []
    in_string = False
    escaped = False
    for character in fragment:
        if in_string:
            if escaped:
                escaped = False
                masked.append("·")
                continue
            if character == "\\":
                escaped = True
                masked.append("·")
                continue
            if character == '"':
                in_string = False
                masked.append(character)
                continue
            masked.append("·")
            continue
        masked.append(character)
        if character == '"':
            in_string = True
    return f"chars[{start}:{end}]=" + "".join(masked).replace("\n", "\\n")
