from __future__ import annotations

import re


_TAG_START_RE = re.compile(r"<(/?)([A-Za-z][\w.]*)\b")
_BINDING_RE = re.compile(r"\bdata-(?:action-id|information-item-id)\s*=")
_LITERAL_BINDING_RE = re.compile(
    r"\bdata-(action-id|information-item-id)\s*=\s*['\"]([^'\"]+)['\"]"
)
_HIDDEN_STYLE_RE = re.compile(
    r"\b(?:display\s*:\s*['\"]none['\"]|visibility\s*:\s*['\"]hidden['\"])",
    re.IGNORECASE,
)
_HIDDEN_ATTRIBUTE_RE = re.compile(
    r"(?:^|\s)hidden(?:\s|=|/|$)|\baria-hidden\s*=\s*['\"]true['\"]",
    re.IGNORECASE,
)
_CLASS_RE = re.compile(r"\bclassName\s*=\s*['\"]([^'\"]*)['\"]")
_RESPONSIVE_SHOW_RE = re.compile(r"^(?:sm|md|lg|xl|2xl):(?:block|flex|grid|inline|inline-block)$")


def _tag_end(code: str, start: int) -> int:
    """扫描 JSX 标签终点，跳过引号和表达式里的比较符。"""

    quote = ""
    braces = 0
    escaped = False
    for index in range(start, len(code)):
        char = code[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "{":
            braces += 1
        elif char == "}" and braces:
            braces -= 1
        elif char == ">" and not braces:
            return index
    return -1


def _is_hidden(attrs: str) -> bool:
    """识别无条件隐藏的 JSX 元素，不把响应式局部隐藏误判为全局隐藏。"""

    classes = _CLASS_RE.search(attrs)
    tokens = set(classes.group(1).split()) if classes else set()
    hidden_class = "hidden" in tokens and not any(
        _RESPONSIVE_SHOW_RE.match(token) for token in tokens
    )
    return bool(
        _HIDDEN_STYLE_RE.search(attrs)
        or _HIDDEN_ATTRIBUTE_RE.search(attrs)
        or hidden_class
    )


def validate_visible_bindings(code: str) -> list[str]:
    """拒绝在无条件隐藏的节点或祖先内伪造产品操作及信息展示。"""

    ancestors: list[tuple[str, bool]] = []
    hidden_bindings: list[str] = []
    cursor = 0
    while match := _TAG_START_RE.search(code, cursor):
        end = _tag_end(code, match.start())
        if end < 0:
            break
        cursor = end + 1
        tag = match.group(2)
        if match.group(1):
            for index in range(len(ancestors) - 1, -1, -1):
                if ancestors[index][0] == tag:
                    del ancestors[index:]
                    break
            continue
        attrs = code[match.end() : end]
        hidden = _is_hidden(attrs) or any(item[1] for item in ancestors)
        if hidden and _BINDING_RE.search(attrs):
            identities = [
                f"{kind}={item_id}"
                for kind, item_id in _LITERAL_BINDING_RE.findall(attrs)
            ]
            hidden_bindings.append(f"<{tag}> {', '.join(identities) or '动态绑定'}")
        if not attrs.rstrip().endswith("/"):
            ancestors.append((tag, hidden))
    if not hidden_bindings:
        return []
    return [
        "产品操作或信息项绑定在无条件隐藏的节点中："
        + "；".join(hidden_bindings[:12])
        + "。请把绑定、data-control-id 和界面行为的 data-ui-effect 一起移到默认可见的真实控件上。"
    ]
