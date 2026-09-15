"""页面正式身份的 canonical 转换规则。"""

from __future__ import annotations

import re


_PAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:[-_ ]+[A-Za-z0-9]+)*$")


def _validated_page_id(page_id: str) -> str:
    """校验页面正式身份，拒绝隐式转换、空值和无法安全分段的字符。"""

    if (
        not isinstance(page_id, str)
        or not page_id
        or page_id != page_id.strip()
        or _PAGE_ID_PATTERN.fullmatch(page_id) is None
    ):
        raise ValueError(
            "page_id 必须是由字母、数字及空格、短横线或下划线分隔的非空字符串。"
        )
    return page_id


def page_id_to_page_key(page_id: str) -> str:
    """将正式 pageId 确定性转换为 PascalCase PageKey。"""

    value = _validated_page_id(page_id)
    segments = [segment for segment in re.split(r"[-_ ]+", value) if segment]
    page_key = "".join(segment[:1].upper() + segment[1:].lower() for segment in segments)
    if not page_key or not page_key[:1].isalpha():
        page_key = f"Page{page_key}"
    return page_key


def canonical_page_entry_path(page_id: str) -> str:
    """根据正式 pageId 返回唯一的前端页面入口路径。"""

    return f"frontend/src/pages/{page_id_to_page_key(page_id)}/index.tsx"
