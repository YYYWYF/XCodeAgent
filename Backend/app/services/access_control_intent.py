"""自然语言中的业务访问控制变更识别。"""

from __future__ import annotations

import re


def has_explicit_business_access_control_change(request: str) -> bool:
    """识别明确改变角色、页面或操作可访问性的自然语言需求。"""

    text = str(request or "").strip().casefold()
    if not text:
        return False
    permission_terms = ("权限", "授权", "访问控制", "无权限")
    access_patterns = (
        r"才能看到",
        r"才能查看",
        r"仅.*可见",
        r"只有.*可见",
        r"禁止访问",
        r"无法访问",
        r"取消.*权限",
        r"所有人.*(?:可见|访问)",
        r"403",
    )
    return any(term in text for term in permission_terms) and any(
        re.search(pattern, text) is not None for pattern in access_patterns
    )
