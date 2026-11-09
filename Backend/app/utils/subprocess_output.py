from __future__ import annotations

from codecs import getincrementaldecoder
from typing import Any, BinaryIO


def subprocess_output_text(value: Any, *, fallback_encoding: str | None = None) -> str:
    """把子进程输出转为文本，按需使用调用方指定的原生编码解码非 UTF-8 行。"""

    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        if fallback_encoding is None:
            return value.decode("utf-8", errors="replace")
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            # 同一输出可同时含有 JVM 的 UTF-8 日志和系统命令的本机编码提示。
            lines: list[str] = []
            for line in value.splitlines(keepends=True):
                try:
                    lines.append(line.decode("utf-8"))
                except UnicodeDecodeError:
                    lines.append(line.decode(fallback_encoding, errors="replace"))
            return "".join(lines)
    return "" if value is None else str(value)


def read_utf8_log_tail(stream: BinaryIO, limit: int) -> tuple[str, bool]:
    """读取有界 UTF-8 日志尾部，跳过读取边界截断的字符。"""

    size = stream.seek(0, 2)
    truncated = size > limit
    stream.seek(max(0, size - limit))
    data = stream.read(limit)
    start = 0
    if truncated:
        # 只跳过窗口开头被切断的 UTF-8 字符，保留日志正文中的解码错误。
        while start < len(data) and 0x80 <= data[start] <= 0xBF:
            start += 1
    # 进程可能还在写入末尾字符，等下一次读取再显示该字符。
    decoder = getincrementaldecoder("utf-8")(errors="replace")
    return decoder.decode(data[start:], final=False), truncated
