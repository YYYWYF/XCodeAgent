from __future__ import annotations

import html
import re
from typing import Any

from .contract_scaffold import _opening_tag_end
from .dynamic_bindings import (
    _balanced_end,
    _expression_attribute,
    _literal_map_rows,
    _normalized_label,
    _rows_from_array_body,
    _unique_item_bindings,
)


_MAP_CALL = re.compile(
    r"(?P<source>[A-Za-z_$][\w$]*(?:\.slice\(\s*\d+\s*(?:,\s*\d+\s*)?\))?|\])"
    r"\s*\.map\s*\(\s*(?:\(\s*(?P<parenthesized>[A-Za-z_$][\w$]*)"
    r"(?:\s*,\s*[A-Za-z_$][\w$]*)?\s*\)|(?P<bare>[A-Za-z_$][\w$]*))\s*=>"
)
_OPENING = re.compile(r"<([A-Za-z][\w.]*)\b")


def _map_rows(code: str, match: re.Match[str]) -> list[dict[str, str]]:
    """只读取当前 map 对应的有限字面量数组，支持 slice 与内联数组。"""

    source = match.group("source")
    if source == "]":
        closing = match.start("source")
        for opening in range(closing - 1, max(-1, closing - 12000), -1):
            if code[opening] == "[" and _balanced_end(code, opening, "[", "]") == closing:
                return _rows_from_array_body(code[opening + 1 : closing])
        return []
    sliced = re.fullmatch(
        r"([A-Za-z_$][\w$]*)\.slice\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", source
    )
    source_name = sliced.group(1) if sliced else source
    rows = _literal_map_rows(code, source_name)
    if not rows:
        # 仅追溯不会改变记录标识的 filter 别名；运行时过滤不影响每行的身份。
        aliases = list(re.finditer(
            rf"\b(?:const|let)\s+{re.escape(source_name)}\s*=\s*"
            r"([A-Za-z_$][\w$]*)\.filter\s*\(",
            code,
        ))
        if len(aliases) == 1:
            rows = _literal_map_rows(code, aliases[0].group(1))
    if sliced:
        start = int(sliced.group(2))
        end = int(sliced.group(3)) if sliced.group(3) is not None else None
        return rows[start:end]
    return rows


def _map_contexts(code: str) -> list[tuple[int, int, str, list[dict[str, str]]]]:
    """每页只解析一次有限 map 来源和范围，避免多卡片页面重复全量扫描。"""

    contexts: list[tuple[int, int, str, list[dict[str, str]]]] = []
    for match in _MAP_CALL.finditer(code):
        call_start = code.find("(", code.find(".map", match.start(), match.end()))
        if call_start < 0:
            continue
        call_end = _balanced_end(code, call_start, "(", ")")
        if call_end < 0:
            continue
        rows = _map_rows(code, match)
        variable = match.group("parenthesized") or match.group("bare") or ""
        if rows and variable:
            contexts.append((match.start(), call_end, variable, rows))
    return contexts


def _map_context(
    contexts: list[tuple[int, int, str, list[dict[str, str]]]], position: int
) -> tuple[str, list[dict[str, str]]] | None:
    """定位包住当前 JSX 节点的最近一层 map，避免同名回调跨数组串线。"""

    candidates = [item for item in contexts if item[0] < position < item[1]]
    if not candidates:
        return None
    _, _, variable, rows = max(candidates, key=lambda item: item[0])
    return variable, rows


def _action_bindings(
    rows: list[dict[str, str]], page: dict[str, Any],
    expression: str = "", variable: str = "",
) -> dict[str, dict[str, Any]]:
    """按来源 actionId 或可见名称唯一匹配产品操作，不用页面或行号猜测。"""

    actions = [
        action for action in (page.get("actions") or [])
        if isinstance(action, dict) and str(action.get("actionId") or "").strip()
    ]
    if len({row.get("id") for row in rows}) != len(rows):
        return {}
    actions_by_id = {str(action["actionId"]): action for action in actions}
    selected: dict[str, dict[str, Any]] = {}
    if expression and variable:
        # 直接读取模型已经写出的有限三元分支；只接受来源行与产品 actionId 双重命中。
        pairs = re.findall(
            rf"\b{re.escape(variable)}\.id\s*===\s*(['\"])([^'\"]+)\1"
            r"\s*\?\s*(['\"])([^'\"]+)\3",
            expression,
        )
        row_ids = {str(row.get("id")) for row in rows}
        for _, source_id, _, action_id in pairs:
            if source_id in row_ids and action_id in actions_by_id:
                selected[source_id] = actions_by_id[action_id]
        unassigned = row_ids - set(selected)
        fallback = re.search(r":\s*['\"]([^'\"]+)['\"]\s*$", expression)
        if len(unassigned) == 1 and fallback and fallback.group(1) in actions_by_id:
            selected[next(iter(unassigned))] = actions_by_id[fallback.group(1)]
    for row in rows:
        if str(row["id"]) in selected:
            continue
        names = {
            _normalized_label(row.get(key)) for key in ("name", "title", "label", "text")
            if row.get(key)
        }
        row_id = _normalized_label(row.get("id"))
        ranked = sorted(
            (
                (
                    100 if row.get("actionId") == action.get("actionId")
                    else 95 if _normalized_label(action.get("name")) in names
                    else 90 if (
                        (behavior := action.get("behavior"))
                        and isinstance(behavior, dict)
                        and behavior.get("type") == "navigation"
                        and row_id
                        and re.sub(r"(?:page|页面|页)$", "", _normalized_label(behavior.get("targetPageId"))) == row_id
                    )
                    else 85 if any(
                        re.sub(r"(?:菜单项|按钮|图标|入口|页面|页)$", "", _normalized_label(action.get("name"))) == name
                        for name in names
                    )
                    else 0,
                    str(action.get("actionId")),
                    action,
                )
                for action in actions
            ),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        if not ranked or ranked[0][0] == 0:
            continue
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return {}
        selected[str(row["id"])] = ranked[0][2]
    ids = [str(action.get("actionId")) for action in selected.values()]
    return selected if len(ids) == len(set(ids)) else {}


def _closing_tag(code: str, tag: str, opening_end: int) -> tuple[int, int] | None:
    """按同名 JSX 标签深度找到闭合位置，允许卡片内部嵌套相同标签。"""

    token = re.compile(rf"<(/?){re.escape(tag)}\b")
    depth = 1
    for match in token.finditer(code, opening_end + 1):
        end = _opening_tag_end(code, match.start())
        if end < 0:
            return None
        if match.group(1):
            depth -= 1
            if depth == 0:
                return match.start(), end + 1
        elif not code[match.start() : end + 1].rstrip().endswith("/>"):
            depth += 1
    return None


def _remove_expression_attribute(opening: str, name: str) -> str:
    """删除需要由有限分支重写的动态属性，保留其余视觉与事件属性。"""

    attribute = _expression_attribute(opening, name)
    if attribute is None:
        return opening
    return opening[: attribute[0]] + opening[attribute[1] :]


def _bound_branch(
    tag: str,
    source_id: str,
    item_id: str,
    action: dict[str, Any] | None,
) -> str:
    """生成只会在真实数据行渲染的可见 JSX 分支和静态契约标记。"""

    attributes = []
    bound_id = item_id
    if item_id:
        attributes.append(
            f'data-information-item-id="{html.escape(item_id, quote=True)}"'
        )
    if action is not None:
        action_id = str(action.get("actionId") or "")
        bound_id = action_id
        attributes.append(f'data-action-id="{html.escape(action_id, quote=True)}"')
        behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
        if behavior.get("type") == "interface":
            effect = str(behavior.get("expectedResult") or action.get("name") or action_id)
            attributes.append(f'data-ui-effect="{html.escape(effect, quote=True)}"')
    attributes.append(f'data-control-id="screenshot-bound-{html.escape(bound_id, quote=True)}"')
    return (
        f'  if (String(sourceId) === {source_id!r}) return <{tag} {{...props}} '
        + " ".join(attributes) + f">{{children}}</{tag}>;"
    )


def repair_finite_collection_bindings(
    page: dict[str, Any], code: str
) -> tuple[str, int]:
    """把有限字面量列表的动态业务标记转为可见、可校验的静态 JSX 分支。"""

    replacements: list[tuple[int, int, str]] = []
    definitions: list[str] = []
    contexts = _map_contexts(code)
    cursor = 0
    while (match := _OPENING.search(code, cursor)) is not None:
        tag = match.group(1)
        opening_end = _opening_tag_end(code, match.start())
        if opening_end < 0:
            break
        opening = code[match.start() : opening_end + 1]
        cursor = opening_end + 1
        dynamic_item = _expression_attribute(opening, "data-information-item-id")
        dynamic_action = _expression_attribute(opening, "data-action-id")
        if dynamic_item is None and dynamic_action is None:
            continue
        if "ScreenshotBoundCollection" in opening:
            continue
        context = _map_context(contexts, match.start())
        if context is None:
            continue
        variable, rows = context
        item_bindings = _unique_item_bindings(rows, page) if dynamic_item else {}
        action_bindings = _action_bindings(
            rows, page, dynamic_action[2], variable
        ) if dynamic_action else {}
        if (dynamic_item and not item_bindings) or (dynamic_action and not action_bindings):
            continue
        closing = _closing_tag(code, tag, opening_end)
        if closing is None or opening.rstrip().endswith("/>"):
            continue
        suffix = len(definitions) + 1
        while re.search(rf"\bScreenshotBoundCollection{suffix}\b", code):
            suffix += 1
        component = f"ScreenshotBoundCollection{suffix}"
        rewritten = opening.replace(f"<{tag}", f"<{component} sourceId={{{variable}.id}}", 1)
        for attribute in ("data-information-item-id", "data-action-id", "data-control-id", "data-ui-effect"):
            if attribute in {"data-control-id", "data-ui-effect"} and not dynamic_action:
                # 原静态操作属性的控件 ID 和界面效果继续留在调用位置。
                continue
            rewritten = _remove_expression_attribute(rewritten, attribute)
        branches = [
            _bound_branch(tag, row_id, item_bindings.get(row_id, ""), action_bindings.get(row_id))
            for row_id in dict.fromkeys([*item_bindings, *action_bindings])
        ]
        definitions.append(
            f"\nconst {component} = ({{ sourceId, children, ...props }}: any) => {{\n"
            + "\n".join(branches)
            + f"\n  return <{tag} {{...props}}"
            + (' data-preview-only="true"' if dynamic_action else "")
            + f">{{children}}</{tag}>;\n}};\n"
        )
        replacements.extend([
            (match.start(), opening_end + 1, rewritten),
            (closing[0], closing[1], f"</{component}>"),
        ])
    if not replacements:
        return code, 0
    fixed = code
    for start, end, replacement in sorted(replacements, reverse=True):
        fixed = fixed[:start] + replacement + fixed[end:]
    return fixed + "".join(definitions), len(definitions)
