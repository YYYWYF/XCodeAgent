from __future__ import annotations

import re


_CLASS_ATTRIBUTE = re.compile(r"\bclassName\s*=\s*(['\"])(.*?)\1", re.DOTALL)
_UTILITY_CLASS = re.compile(
    r"^(?:"
    r"(?:sm|md|lg|xl|2xl|hover|focus|disabled):"
    r"|(?:bg|text|font|border|rounded|shadow|w|h|min-w|min-h|max-w|max-h|"
    r"p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|space|leading|tracking|"
    r"items|justify|grid|grid-cols|col-span|flex|shrink|grow|overflow|opacity)-"
    r"|(?:flex|grid|block|inline-flex|hidden|antialiased)$"
    r")"
)


def validate_screenshot_runtime_styles(code: str) -> str:
    """拒绝依赖预览 iframe 未加载的 Tailwind 实用类的截图设计稿。"""

    # 截图专用设计稿可用内联 style 或自带 <style>；外部 Tailwind 构建链并不存在。
    if re.search(r"<style\b", code, re.IGNORECASE):
        return ""
    utility_classes = [
        token
        for match in _CLASS_ATTRIBUTE.finditer(code)
        for token in match.group(2).split()
        if _UTILITY_CLASS.match(token)
    ]
    if len(utility_classes) < 3:
        return ""
    sample = ", ".join(dict.fromkeys(utility_classes[:5]))
    return (
        "截图设计稿使用了未加载的 Tailwind/原子 CSS 类名"
        f"（例如 {sample}），隔离预览中不会生效。"
        "请改为完整的内联 style 或在 TSX 内提供实际 CSS 的 <style> 标签。"
    )
