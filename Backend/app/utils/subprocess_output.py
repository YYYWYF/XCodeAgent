from __future__ import annotations

from typing import Any


def subprocess_output_text(value: Any) -> str:
    """把 subprocess 的文本或字节输出安全转换为 UTF-8 字符串。"""

    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value)
