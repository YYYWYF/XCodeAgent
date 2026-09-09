"""自然语言中的业务访问控制变更识别。"""

from __future__ import annotations

import re
from typing import Any


def resolve_capability_intents(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """从自然语言中识别当前 V1 模板能力的显式启用意图。"""

    text = str(request or "").strip()
    if not text:
        return []
    normalized = text.casefold()
    existing = requirement_spec if isinstance(requirement_spec, dict) else {}
    authentication = existing.get("authentication_requirements")
    authorization = existing.get("authorization_requirements")
    changes: list[dict[str, Any]] = []
    # 登录能力只接受明确的能力变更请求，避免把普通流程中的“登录”误判为变更。
    if (
        re.search(r"(?:增加|添加|启用|开启|需要|支持).{0,8}(?:登录|登陆|login|authentication)", normalized)
        and not (isinstance(authentication, dict) and authentication.get("enabled") is True)
    ):
        changes.append({"capability": "login", "action": "enable", "confidence": 0.99, "evidence": text})
    # 权限基础设施与具体页面/操作规则独立，明确要求权限管理即可启用能力。
    if (
        re.search(r"(?:增加|添加|启用|开启|需要|支持).{0,10}(?:权限管理|权限控制|rbac|authorization)", normalized)
        and not (isinstance(authorization, dict) and authorization.get("enabled") is True)
    ):
        changes.append({"capability": "authorization", "action": "enable", "confidence": 0.99, "evidence": text})
    return changes


def has_explicit_capability_change(request: str) -> bool:
    """判断自然语言是否明确要求变更当前支持的模板能力。"""

    return bool(resolve_capability_intents(request))


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
