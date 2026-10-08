from __future__ import annotations

import html
import re
from typing import Any

from .contract_scaffold import _append_attributes, _replace_opening_tags
from .dynamic_bindings import _normalized_label


def repair_equivalent_visible_information(
    page: dict[str, Any], code: str
) -> tuple[str, int]:
    """把与操作名称完全同义的信息项绑定到唯一已有的可见操作。"""

    items = [
        item for item in (page.get("information_items") or [])
        if isinstance(item, dict) and str(item.get("itemId") or "").strip()
    ]
    actions = [
        action for action in (page.get("actions") or [])
        if isinstance(action, dict) and str(action.get("actionId") or "").strip()
    ]
    static_items = set(re.findall(
        r'\bdata-information-item-id\s*=\s*[\'"]([^\'"]+)[\'"]', code
    ))
    additions: dict[str, str] = {}
    ambiguous_actions: set[str] = set()
    for item in items:
        item_id = str(item["itemId"])
        if item_id in static_items:
            continue
        label = _normalized_label(item.get("label"))
        if not label:
            continue
        matches = [
            action for action in actions
            if _normalized_label(action.get("name")) == label
        ]
        if len(matches) == 1:
            action_id = str(matches[0]["actionId"])
            if action_id in additions:
                ambiguous_actions.add(action_id)
            else:
                additions[action_id] = item_id
    for action_id in ambiguous_actions:
        additions.pop(action_id, None)
    if not additions:
        return code, 0
    occurrences = {
        action_id: len(re.findall(
            rf'\bdata-action-id\s*=\s*[\'"]{re.escape(action_id)}[\'"]', code
        ))
        for action_id in additions
    }
    changed = 0

    def normalize(_tag: str, opening: str) -> str:
        """只标注唯一命中的原可见控件，不复制或虚构额外 DOM。"""

        nonlocal changed
        action = re.search(r'\bdata-action-id\s*=\s*[\'"]([^\'"]+)[\'"]', opening)
        if action is None or occurrences.get(action.group(1)) != 1:
            return opening
        item_id = additions.get(action.group(1))
        if not item_id or "data-information-item-id=" in opening:
            return opening
        if re.search(r"\bdisplay\s*:\s*['\"]none['\"]|\bhidden\b", opening):
            return opening
        fixed = _append_attributes(
            opening,
            f'data-information-item-id="{html.escape(item_id, quote=True)}"',
        )
        changed += 1
        return fixed

    return _replace_opening_tags(code, normalize), changed
