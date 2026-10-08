from __future__ import annotations

import html
import json
import re
from typing import Any

from .contract_scaffold import _append_attributes, _opening_tag_end, _replace_opening_tags


_DYNAMIC_ITEM = re.compile(
    r"\bdata-information-item-id\s*=\s*\{\s*([A-Za-z_$][\w$]*)\."
    r"(id|itemId|informationItemId)\s*\}"
)
_DYNAMIC_CONTROL = re.compile(r"\s+data-control-id\s*=\s*\{[^{}]*\}")
_ROW_TAG = re.compile(r"<(tr|li)\b")


def repair_literal_collection_bindings(
    page: dict[str, Any], code: str
) -> tuple[str, int]:
    """把字面量记录数组的动态行标记转成真实可见的静态 JSX 分支。"""

    information = page.get("information_items")
    expected = [
        str(item.get("itemId") or "").strip()
        for item in information
        if isinstance(item, dict) and str(item.get("itemId") or "").strip()
    ] if isinstance(information, list) else []
    replacements: list[tuple[int, int, str]] = []
    definitions: list[str] = []
    used_ids: set[str] = set()
    for match in _ROW_TAG.finditer(code):
        tag = match.group(1)
        opening_end = _opening_tag_end(code, match.start())
        if opening_end < 0:
            continue
        opening = code[match.start() : opening_end + 1]
        dynamic = _DYNAMIC_ITEM.search(opening)
        if dynamic is None:
            continue
        variable, field = dynamic.groups()
        applicable = [
            item_id
            for item_id in expected
            if item_id not in used_ids
            and not re.search(
                r'\bdata-information-item-id\s*=\s*[\'\"]'
                + re.escape(item_id)
                + r'[\'\"]',
                code,
            )
            and re.search(
                rf"\b{re.escape(field)}\s*:\s*(['\"])"
                + re.escape(item_id)
                + r"\1",
                code,
            )
        ]
        if not applicable:
            continue
        closing = re.search(rf"</{tag}\s*>", code[opening_end + 1 :])
        if closing is None:
            continue
        close_start = opening_end + 1 + closing.start()
        close_end = opening_end + 1 + closing.end()
        component = f"ScreenshotBoundRow{len(definitions) + 1}"
        without_dynamic = _DYNAMIC_ITEM.sub("", opening, count=1)
        without_dynamic = _DYNAMIC_CONTROL.sub("", without_dynamic, count=1)
        new_opening = without_dynamic.replace(
            f"<{tag}", f"<{component} sourceId={{{variable}.{field}}}", 1
        )
        replacements.extend(
            [(match.start(), opening_end + 1, new_opening), (close_start, close_end, f"</{component}>")]
        )
        branches = "\n".join(
            f'  if (sourceId === "{html.escape(item_id, quote=True)}") '
            f'return <{tag} {{...props}} '
            f'data-information-item-id="{html.escape(item_id, quote=True)}" '
            f'data-control-id="screenshot-row-{html.escape(item_id, quote=True)}">'
            f'{{children}}</{tag}>;'
            for item_id in applicable
        )
        definitions.append(
            f"\nconst {component} = ({{ sourceId, children, ...props }}: any) => {{\n"
            f"{branches}\n"
            f"  return <{tag} {{...props}}>{{children}}</{tag}>;\n"
            "};\n"
        )
        used_ids.update(applicable)
    if not replacements:
        return code, 0
    fixed = code
    for start, end, replacement in sorted(replacements, reverse=True):
        fixed = fixed[:start] + replacement + fixed[end:]
    return fixed + "".join(definitions), len(used_ids)


def _balanced_end(source: str, start: int, left: str, right: str) -> int:
    """跳过字符串并定位数组、对象或 JSX 属性表达式的闭合位置。"""

    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote in {"'", '"'}:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "\\" and quote == "`":
            escaped = not escaped
        elif char == "`" and not escaped:
            quote = "" if quote == "`" else "`"
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
        if char != "\\":
            escaped = False
    return -1


def _literal_map_rows(code: str, source_name: str) -> list[dict[str, str]]:
    """只解析可证明为平坦对象字面量的 map 数据源，不推断运行时数据。"""

    declarations = list(re.finditer(
        rf"\b(?:const|let)\s+{re.escape(source_name)}\b(?:\s*:\s*[^=;\n]+)?\s*=\s*\[",
        code,
    ))
    if len(declarations) != 1:
        return []
    array_start = declarations[0].end() - 1
    array_end = _balanced_end(code, array_start, "[", "]")
    if array_end < 0:
        return []
    return _rows_from_array_body(code[array_start + 1 : array_end])


def _rows_from_array_body(body: str) -> list[dict[str, str]]:
    """从常量数组体提取有限数量的平坦字面量记录。"""

    rows: list[dict[str, str]] = []
    cursor = 0
    while cursor < len(body):
        match = re.search(r"\{", body[cursor:])
        if match is None:
            break
        start = cursor + match.start()
        end = _balanced_end(body, start, "{", "}")
        if end < 0:
            return []
        item = body[start : end + 1]
        fields = {
            key: value
            for key, _, value in re.findall(
                r"\b(id|name|title|label|text|actionId)\s*:\s*(['\"])([^'\"\n]{1,300})\2",
                item,
            )
        }
        numeric_id = re.search(r"\bid\s*:\s*(-?\d+)\b", item)
        if "id" not in fields and numeric_id is not None:
            fields["id"] = numeric_id.group(1)
        if "id" not in fields and "actionId" not in fields:
            return []
        rows.append(fields)
        cursor = end + 1
        if len(rows) > 40:
            return []
    return rows


def _literal_rows_for_variable(
    code: str, variable: str
) -> tuple[str, list[dict[str, str]]]:
    """同时支持命名数组与 JSX 内联数组的 map，拒绝不唯一的数据来源。"""

    named = _map_source_for_variable(code, variable)
    if named:
        return named, _literal_map_rows(code, named)
    matches = list(
        re.finditer(
            rf"\]\s*\.map\s*\(\s*(?:\(\s*)?{re.escape(variable)}"
            r"(?:\s*,\s*[A-Za-z_$][\w$]*)?\s*\)?\s*=>",
            code,
        )
    )
    if len(matches) != 1:
        return "", []
    closing = matches[0].start()
    for opening in range(closing - 1, max(-1, closing - 12000), -1):
        if code[opening] == "[" and _balanced_end(code, opening, "[", "]") == closing:
            return f"inline-{opening}", _rows_from_array_body(code[opening + 1 : closing])
    return "", []


def _map_source_for_variable(code: str, variable: str) -> str:
    """仅在回调变量唯一对应一个字面量数组时确认其数据来源。"""

    sources = set(
        re.findall(
            rf"\b([A-Za-z_$][\w$]*)(?:\.slice\(\s*\d+\s*(?:,\s*\d+\s*)?\))?"
            rf"\s*\.map\s*\(\s*(?:\(\s*)?{re.escape(variable)}"
            r"(?:\s*,\s*[A-Za-z_$][\w$]*)?\s*\)?\s*=>",
            code,
        )
    )
    return next(iter(sources)) if len(sources) == 1 else ""


def _normalized_label(value: object) -> str:
    """忽略标点与空白比较字面量记录和产品项的可见名称。"""

    return re.sub(r"[\W_]+", "", str(value or "").casefold())


def _item_match_score(row: dict[str, str], item: dict[str, Any]) -> int:
    """只有行标题或稳定 ID 给出明确证据时才为产品项打分。"""

    item_id = _normalized_label(item.get("itemId"))
    source_id = _normalized_label(row.get("id"))
    label = str(item.get("label") or "").split("：", 1)[-1].split(":", 1)[-1]
    label_key = _normalized_label(label)
    names = [_normalized_label(row.get(key)) for key in ("name", "title", "label", "text")]
    if label_key and label_key in names:
        return 100
    if len(source_id) >= 4 and item_id.endswith(source_id):
        return 95
    source_parts = [part for part in re.split(r"[-_]", str(row.get("id") or "").casefold()) if part]
    item_parts = [part for part in re.split(r"[-_]", str(item.get("itemId") or "").casefold()) if part]
    if len(source_id) >= 4 and any(
        item_parts[index : index + len(source_parts)] == source_parts
        for index in range(len(item_parts) - len(source_parts) + 1)
    ):
        return 90
    if any(len(name) >= 4 and label_key.startswith(name) for name in names if name):
        return 85
    if len(label_key) >= 8 and any(name.startswith(label_key) for name in names if name):
        return 80
    return 0


def _unique_item_bindings(
    rows: list[dict[str, str]], page: dict[str, Any]
) -> dict[str, str]:
    """要求每一行唯一匹配不同的 ProductPlan 信息项，否则拒绝自动绑定。"""

    items = [
        item for item in page.get("information_items", [])
        if isinstance(item, dict) and str(item.get("itemId") or "").strip()
    ]
    if not rows or len({row.get("id") for row in rows}) != len(rows):
        return {}
    bindings: dict[str, str] = {}
    for row in rows:
        ranked = sorted(
            [(_item_match_score(row, item), str(item["itemId"])) for item in items],
            reverse=True,
        )
        if not ranked or ranked[0][0] < 80:
            continue
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return {}
        bindings[str(row["id"])] = ranked[0][1]
    return bindings if len(set(bindings.values())) == len(bindings) else {}


def _expression_attribute(opening: str, name: str) -> tuple[int, int, str] | None:
    """读取完整的 JSX 表达式属性，支持模板字符串里的嵌套插值。"""

    match = re.search(rf"\b{re.escape(name)}\s*=\s*\{{", opening)
    if match is None:
        return None
    start = opening.find("{", match.start())
    end = _balanced_end(opening, start, "{", "}")
    return (match.start(), end + 1, opening[start + 1 : end]) if end >= 0 else None


def repair_mapped_card_bindings(page: dict[str, Any], code: str) -> tuple[str, int]:
    """把有唯一可见行证据的动态卡片 ID 映射到静态产品契约。"""

    declarations: list[str] = []
    binding_names: dict[str, str] = {}
    changed = 0

    def normalize(_tag: str, opening: str) -> str:
        """只重写已确证的动态信息项属性，保留原卡片结构和内容。"""

        nonlocal changed
        attribute = _expression_attribute(opening, "data-information-item-id")
        if attribute is None:
            return opening
        if "ScreenshotItemBindings" in attribute[2]:
            return opening
        variables = set(re.findall(r"\b([A-Za-z_$][\w$]*)\.id\b", attribute[2]))
        if len(variables) != 1:
            return opening
        variable = next(iter(variables))
        source, rows = _literal_rows_for_variable(code, variable)
        bindings = _unique_item_bindings(rows, page)
        if not bindings:
            return opening
        name = binding_names.get(source)
        if name is None:
            name = f"ScreenshotItemBindings{len(binding_names) + 1}"
            if name in code:
                return opening
            binding_names[source] = name
            entries = ",\n".join(
                f"  {json.dumps(source_id, ensure_ascii=False)}: "
                "{" + f"'data-information-item-id': {json.dumps(item_id, ensure_ascii=False)}, "
                f"'data-control-id': {json.dumps(item_id + '-display', ensure_ascii=False)}" + "}"
                for source_id, item_id in bindings.items()
            )
            declarations.append(
                f"\nconst {name}: Record<string, Record<string, string>> = {{\n{entries}\n}};\n"
            )
        lookup = f"{name}[{variable}.id]"
        updated = opening[: attribute[0]] + (
            f"data-information-item-id={{{lookup}?.['data-information-item-id']}}"
        ) + opening[attribute[1] :]
        control = _expression_attribute(updated, "data-control-id")
        if control is not None:
            updated = updated[: control[0]] + (
                f"data-control-id={{{lookup}?.['data-control-id']}}"
            ) + updated[control[1] :]
        elif "data-control-id=" not in updated:
            updated = _append_attributes(
                updated, f"data-control-id={{{lookup}?.['data-control-id']}}"
            )
        changed += 1
        return updated

    fixed = _replace_opening_tags(code, normalize)
    return fixed + "".join(declarations), changed


def repair_mapped_interface_effects(page: dict[str, Any], code: str) -> tuple[str, int]:
    """为循环操作提供与真实 actionId 对应的静态界面效果证据。"""

    actions = {
        str(item.get("actionId") or ""): item
        for item in page.get("actions", [])
        if isinstance(item, dict) and str(item.get("actionId") or "").strip()
    }
    declarations: list[str] = []
    binding_names: dict[str, str] = {}
    changed = 0

    def normalize(_tag: str, opening: str) -> str:
        """保留现有循环行为，只对已声明操作补可静态核对的 effect 映射。"""

        nonlocal changed
        attribute = _expression_attribute(opening, "data-action-id")
        if attribute is None:
            return opening
        match = re.fullmatch(r"\s*([A-Za-z_$][\w$]*)\.actionId\s*", attribute[2])
        if match is None:
            return opening
        effect_attribute = _expression_attribute(opening, "data-ui-effect")
        if effect_attribute is not None and "ScreenshotActionEffects" in effect_attribute[2]:
            return opening
        if effect_attribute is None and "data-ui-effect=" in opening:
            return opening
        variable = match.group(1)
        source, rows = _literal_rows_for_variable(code, variable)
        action_ids = [row.get("actionId", "") for row in rows]
        if not action_ids or len(set(action_ids)) != len(action_ids) or any(
            action_id not in actions for action_id in action_ids
        ):
            return opening
        interface_ids = [
            action_id for action_id in action_ids
            if isinstance(actions[action_id].get("behavior"), dict)
            and actions[action_id]["behavior"].get("type") == "interface"
        ]
        if not interface_ids:
            return opening
        name = binding_names.get(source)
        if name is None:
            name = f"ScreenshotActionEffects{len(binding_names) + 1}"
            if name in code:
                return opening
            binding_names[source] = name
            entries = []
            for action_id in action_ids:
                behavior = actions[action_id].get("behavior") or {}
                effect = str(behavior.get("expectedResult") or actions[action_id].get("name") or "").strip()
                fields = (
                    f"'data-action-id': {json.dumps(action_id, ensure_ascii=False)}, "
                    f"'data-control-id': {json.dumps(action_id + '-control', ensure_ascii=False)}"
                )
                if action_id in interface_ids and effect:
                    fields += f", 'data-ui-effect': {json.dumps(effect, ensure_ascii=False)}"
                entries.append(f"  {json.dumps(action_id, ensure_ascii=False)}: {{{fields}}}")
            declarations.append(
                f"\nconst {name}: Record<string, Record<string, string>> = {{\n"
                + ",\n".join(entries) + "\n};\n"
            )
        lookup = f"{name}[{variable}.actionId]?.['data-ui-effect']"
        if effect_attribute is not None:
            original = effect_attribute[2].strip()
            updated = opening[: effect_attribute[0]] + (
                f"data-ui-effect={{{lookup} || ({original})}}"
            ) + opening[effect_attribute[1] :]
        elif "data-ui-effect=" not in opening:
            updated = _append_attributes(opening, f"data-ui-effect={{{lookup}}}")
        else:
            updated = opening
        if updated != opening:
            changed += 1
        return updated

    fixed = _replace_opening_tags(code, normalize)
    return fixed + "".join(declarations), changed
