"""仅依据已收到的请求识别动作，不修改提示词或新增模型调用。"""

import re
import unicodedata
from typing import Any

from .operation_catalog import CHINESE_ALIASES, PREFIX_OPERATIONS


def normalized_text(value: str) -> str:
    """统一大小写、全半角与空白，允许模板换行变化但不扩大到正文关键词搜索。"""
    return " ".join(unicodedata.normalize("NFKC", value).lstrip("\ufeff").split()).casefold()


NORMALIZED_PREFIXES = tuple((operation, normalized_text(prefix)) for operation, prefix in PREFIX_OPERATIONS)
NORMALIZED_ALIASES = {key.casefold(): value for key, value in CHINESE_ALIASES.items()}


def english_operation(value: Any) -> str | None:
    """把明确说明归一为英文 snake_case，拒绝地址、任意中文和超长正文。"""
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        return None
    value = unicodedata.normalize("NFKC", value).strip()
    alias = NORMALIZED_ALIASES.get(re.sub(r"\s+", "", value).casefold())
    if alias is not None:
        return alias
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:[ _-][A-Za-z0-9]+)*", value):
        return None
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[ _-]+", "_", value).lower()
    return value if len(value) <= 80 else None


def instruction_text(content: Any) -> str:
    """提取纯文本指令，跳过图像、工具结果及其他非文本载荷。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item["text"] for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
    return ""


def detect_operation(request: dict[str, Any], extensions: dict[str, Any]) -> str:
    """明确英文/已知中文元数据优先，否则匹配当前可信模板入口，未知或冲突不猜测。"""
    explicit = english_operation(extensions.get("current_operation"))
    if explicit and explicit != "unknown":
        return explicit
    messages = request.get("messages", [])
    if not isinstance(messages, list):
        return "unknown"
    instructions = [item for item in messages if isinstance(item, dict) and item.get("role") in {"system", "developer"}]
    first_user = next((item for item in messages if isinstance(item, dict) and item.get("role") == "user"), None)
    # 角色指令优先；用户模板只取首条，不搜索历史回答、工具内容和引用代码。
    for candidates in (instructions, [first_user] if first_user else []):
        heads = [normalized_text(instruction_text(item.get("content")).lstrip("\ufeff \t\r\n")[:4096])
                 for item in candidates]
        matches = {
            operation for head in heads for operation, signature in NORMALIZED_PREFIXES if head.startswith(signature)
        }
        if matches:
            return next(iter(matches)) if len(matches) == 1 else "unknown"
    return "unknown"
