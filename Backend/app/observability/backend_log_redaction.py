"""平台日志的凭据清理与跨写入边界保护。"""

from __future__ import annotations

import os
import re

_KEY = r"(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie)"
_FIELD = re.compile(
    rf"(?i)([\"']?{_KEY}[\"']?\s*[:=]\s*)(\"[^\"]*(?:\"|$)|'[^']*(?:'|$)|[^\s,;}}]+)"
)
_OPEN_FIELD = re.compile(rf"(?i)[\"']?{_KEY}[\"']?\s*[:=]\s*[^\n]*$")


class LogRedactor:
    """在完整记录和未结束的流式输出中隐藏凭据。"""

    def __init__(self) -> None:
        """只在内存保存已知敏感环境值，不输出配置。"""
        self.secrets = sorted(
            {value for key, value in os.environ.items()
             if value and re.search(_KEY, key, re.I)}, key=len, reverse=True,
        )
        self.guard_size = max(256, max(map(len, self.secrets), default=0))

    def redact(self, text: str, *, partial: bool = False) -> str:
        """清理完整秘密、常见认证字段和可能尚未写完的秘密前缀。"""
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?im)^(\s*(?:authorization|cookie)\s*:\s*)[^\r\n]*", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"']*", r"\1 [REDACTED]", text)
        text = _FIELD.sub(r"\1[REDACTED]", text)
        text = re.sub(r"(://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)
        if partial:
            # 隐藏写入边界处的密钥前缀，下一次写入后再对完整值脱敏。
            for secret in self.secrets:
                for length in range(min(len(secret) - 1, len(text)), 0, -1):
                    if text.endswith(secret[:length]):
                        text = text[:-length] + "[REDACTED]"
                        break
        return text

    def safe_prefix(self, text: str) -> int:
        """保留足够的尾部与未结束认证字段，防止分片落盘泄漏凭据。"""
        end = max(0, len(text) - self.guard_size)
        for secret in self.secrets:
            start = text.rfind(secret, 0, min(len(text), end + len(secret)))
            if start >= 0 and start < end < start + len(secret):
                end = start
        match = _OPEN_FIELD.search(text)
        if match:
            end = min(end, match.start())
        # 连接串和 Bearer 认证也必须等到分隔符出现后再持久化。
        for pattern in (r"(?i)\b(?:Bearer|Basic)\s+\S*$", r"\S*://\S*$"):
            match = re.search(pattern, text)
            if match:
                end = min(end, match.start())
        return end
