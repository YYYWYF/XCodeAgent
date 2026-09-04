"""根据正式 ProductPlan 和 UiDesign 幂等补齐 main 模板的页面占位与菜单入口。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.services.ui_design_generator import derive_page_key


# 平台保留的菜单 key，即使 ProductPlan 不再包含对应页面也不得从 BIZ_MENUS 删除。
# DefaultPage 是模板内置首页；System/Role 属于独立的 SYSTEM_MENUS 数组，不会出现在
# BIZ_MENUS 顶层，这里列出仅作防御性兜底。
_SYSTEM_RESERVED_MENU_KEYS = frozenset({"DefaultPage", "System", "Role"})


def collect_template_pages(product_plan: dict[str, Any], ui_designs: dict[str, Any]) -> list[dict[str, Any]]:
    """合并 ProductPlan 页面事实与已确认 UiDesign PageKey 映射。"""

    raw_pages = product_plan.get("pages")
    pages = [item for item in raw_pages if isinstance(item, dict)] if isinstance(raw_pages, list) else []
    ui_status = str(ui_designs.get("confirmation_status") or "")
    if ui_status not in {"confirmed", "skipped"}:
        raise ValueError("UiDesign 必须已确认或明确跳过，才能初始化模板页面。")
    ui_keys: dict[str, str] = {}
    if ui_status == "confirmed":
        for item in ui_designs.get("pages") if isinstance(ui_designs.get("pages"), list) else []:
            if not isinstance(item, dict):
                continue
            page_id = str(item.get("pageId") or item.get("id") or "").strip()
            page_key = str(item.get("page_key") or item.get("pageKey") or "").strip()
            if page_id and page_key:
                ui_keys[page_id] = page_key
    collected: list[dict[str, Any]] = []
    seen_page_ids: set[str] = set()
    used_keys: set[str] = {"DefaultPage"}
    for raw_page in pages:
        page_id = str(raw_page.get("pageId") or raw_page.get("id") or "").strip()
        if not page_id:
            raise ValueError("ProductPlan 页面缺少 pageId。")
        if page_id in seen_page_ids:
            raise ValueError(f"ProductPlan 存在重复 pageId：{page_id}")
        seen_page_ids.add(page_id)
        derived_key = derive_page_key(raw_page, used_keys)
        page_key = ui_keys.get(page_id, derived_key) if ui_status == "confirmed" else derived_key
        if ui_status == "confirmed" and page_id not in ui_keys:
            raise ValueError(f"UiDesign 缺少页面 {page_id} 的 page_key 映射。")
        if page_key != derived_key:
            raise ValueError(f"UiDesign 页面 {page_id} 的 page_key 与共享派生规则不一致：{page_key} != {derived_key}")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", page_key):
            raise ValueError(f"非法页面 PageKey：{page_key or '<empty>'}")
        collected.append({"pageId": page_id, "name": str(raw_page.get("name") or page_id), "path": str(raw_page.get("path") or "/"), "key": page_key})
    return collected


def ensure_frontend_page_placeholders(frontend_dir: Path, pages: list[dict[str, Any]]) -> dict[str, Any]:
    """只创建 main 模板缺失页面占位文件，并返回 manifest 所需结果。"""

    expected: list[str] = []
    existing: list[str] = []
    created: list[str] = []
    pages_dir = frontend_dir / "src" / "pages"
    for page in pages:
        page_key = str(page.get("key") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", page_key):
            raise ValueError(f"非法页面 PageKey：{page_key or '<empty>'}")
        target = pages_dir / page_key / "index.tsx"
        relative_path = target.relative_to(frontend_dir.parent).as_posix()
        expected.append(relative_path)
        if target.is_file():
            existing.append(relative_path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(_placeholder_page_source(str(page.get("name") or ""), page_key))
        except FileExistsError:
            existing.append(relative_path)
        else:
            created.append(relative_path)
    missing = [path for path in expected if not (frontend_dir.parent / path).is_file()]
    return {"status": "succeeded" if not missing else "failed", "expectedFiles": expected, "existingFiles": existing, "createdFiles": created, "missingFiles": missing, "error": None if not missing else "部分页面占位文件写入后仍然缺失。"}


def ensure_frontend_menu_entries(frontend_dir: Path, pages: list[dict[str, Any]]) -> dict[str, Any]:
    """同步 main 模板 BIZ_MENUS 顶层页面入口：删除已移除页面，追加缺失页面。

    只管理 BIZ_MENUS 顶层的扁平页面项（有 ``key``、无 ``children``）：
    - ProductPlan 已不再包含的页面 key 从顶层移除（保留 ``DefaultPage`` 等系统项、
      无 ``key`` 的外部链接项以及带 ``children`` 的目录项）。
    - ProductPlan 新增的页面 key 追加到顶层末尾。
    全部幂等，不覆盖用户手改的目录结构与外部链接。
    """

    menus_path = frontend_dir / "src" / "constants" / "menus.ts"
    if not menus_path.is_file():
        raise FileNotFoundError(f"模板菜单文件不存在：{menus_path}")
    original_content = menus_path.read_text(encoding="utf-8")
    content = original_content
    opening, closing = _biz_menus_array_bounds(content)
    expected_keys = [str(page.get("key") or "").strip() for page in pages]
    expected_key_set = set(expected_keys)
    existing_keys = _typescript_menu_keys(content[opening + 1:closing])
    removed_keys: list[str] = []
    content, removed_keys = _drop_orphaned_top_level_menu_items(
        content, opening, closing, expected_key_set
    )
    # 删除后边界可能变化，重新定位再追加。
    opening, closing = _biz_menus_array_bounds(content)
    existing_keys = _typescript_menu_keys(content[opening + 1:closing])
    missing_pages = [page for page in pages if str(page.get("key") or "") not in existing_keys]
    injected_keys: list[str] = []
    if missing_pages:
        current_body = content[opening + 1:closing]
        separator = "," if current_body.strip() and not current_body.rstrip().endswith(",") else ""
        insertion = "".join(_menu_item_source(page, indent=1) for page in missing_pages)
        content = content[:opening + 1] + f"{current_body.rstrip()}{separator}\n{insertion}" + content[closing:]
        injected_keys = [str(page.get("key") or "") for page in missing_pages]
    # 删除或追加任一发生时才落盘，纯读跳过写入。
    if content != original_content:
        _write_text_atomically(menus_path, content)
    final_opening, final_closing = _biz_menus_array_bounds(content)
    final_keys = _typescript_menu_keys(content[final_opening + 1:final_closing])
    missing_keys = [key for key in expected_keys if key not in final_keys]
    status = "succeeded" if not missing_keys else "failed"
    return {
        "status": status,
        "path": menus_path.relative_to(frontend_dir.parent).as_posix(),
        "expectedKeys": expected_keys,
        "existingKeys": [key for key in expected_keys if key in existing_keys],
        "injectedKeys": injected_keys,
        "removedKeys": removed_keys,
        "missingKeys": missing_keys,
        "error": None if not missing_keys else "部分 ProductPlan 菜单项注入后仍然缺失。",
    }


def inspect_frontend_menu_entries(frontend_dir: Path, pages: list[dict[str, Any]]) -> dict[str, Any]:
    """只读检查 main 模板所需菜单 key 是否已经存在。"""

    menus_path = frontend_dir / "src" / "constants" / "menus.ts"
    expected = [str(page.get("key") or "") for page in pages]
    if not menus_path.is_file():
        return {"path": menus_path.relative_to(frontend_dir.parent).as_posix(), "expectedKeys": expected, "missingKeys": expected, "error": "模板菜单文件不存在。"}
    try:
        content = menus_path.read_text(encoding="utf-8")
        opening, closing = _biz_menus_array_bounds(content)
        keys = _typescript_menu_keys(content[opening + 1:closing])
    except (OSError, UnicodeError, ValueError) as exc:
        return {"path": menus_path.relative_to(frontend_dir.parent).as_posix(), "expectedKeys": expected, "missingKeys": expected, "error": str(exc)}
    return {"path": menus_path.relative_to(frontend_dir.parent).as_posix(), "expectedKeys": expected, "missingKeys": [key for key in expected if key not in keys], "error": None}


def _placeholder_page_source(page_name: str, page_key: str) -> str:
    """生成合法的 TSX 占位组件源码。"""

    return f"// {page_name or page_key} 页面（临时占位，待 Agent 生成真实内容）\nexport default function {page_key}() {{\n  return <div>hello agent!</div>;\n}}\n"


def _typescript_menu_keys(content: str) -> set[str]:
    """从 TypeScript 菜单源码中提取单双引号 key 属性。"""

    return {match.group(2) for match in re.finditer(r"\bkey\s*:\s*(['\"])(.*?)\1", content) if match.group(2)}


def _drop_orphaned_top_level_menu_items(
    content: str,
    opening: int,
    closing: int,
    expected_key_set: set[str],
) -> tuple[str, list[str]]:
    """删除 BIZ_MENUS 顶层中 ProductPlan 已不再包含的扁平页面菜单项。

    只删除同时满足以下条件的顶层项：有 ``key`` 属性、无 ``children`` 字段、
    且 ``key`` 不在 ``expected_key_set`` 与系统保留集合中。带 ``children`` 的
    目录项、无 ``key`` 的外部链接项以及 ``DefaultPage`` 等系统项一律保留，
    避免误删用户手改或平台内置结构。

    返回新内容和被删除的 key 列表；无可删除项时原样返回。
    """

    body = content[opening + 1:closing]
    items = _split_top_level_menu_items(body)
    if not items:
        return content, []
    keep_keys = expected_key_set | _SYSTEM_RESERVED_MENU_KEYS
    to_remove_spans: list[tuple[int, int]] = []
    removed_keys: list[str] = []
    for item in items:
        key = _top_level_menu_item_key(item.text)
        if key is None or key in keep_keys:
            continue
        if "children" in item.text:
            # 目录项由用户或平台维护，即使其 key 不在 ProductPlan 中也不删除。
            continue
        to_remove_spans.append((item.start, item.end))
        removed_keys.append(key)
    if not to_remove_spans:
        return content, []
    # 从后往前删除，避免偏移失效；每个项连同其尾随逗号与换行一起移除。
    new_body = body
    for start, end in sorted(to_remove_spans, reverse=True):
        span_start, span_end = _expand_menu_item_removal_span(new_body, start, end)
        new_body = new_body[:span_start] + new_body[span_end:]
    return content[:opening + 1] + new_body + content[closing:], removed_keys


class _TopLevelMenuItem:
    """BIZ_MENUS 数组体中一个顶层项的文本与相对边界。"""

    __slots__ = ("text", "start", "end")

    def __init__(self, text: str, start: int, end: int) -> None:
        self.text = text
        self.start = start
        self.end = end





def _split_top_level_menu_items(body: str) -> list[_TopLevelMenuItem]:
    """把 BIZ_MENUS 数组体拆成顶层项，跳过字符串与行注释。

    顶层项以 ``{`` 起始、深度回到 0 的 ``}`` 结束；项之间允许任意空白与逗号。
    """

    items: list[_TopLevelMenuItem] = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char.isspace() or char == ",":
            index += 1
            continue
        if char == "/" and index + 1 < length and body[index + 1] == "/":
            newline = body.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if char != "{":
            # 顶层非对象字面量（如 spread、字符串等）不参与菜单项管理，跳过到下一个逗号。
            index = _skip_to_top_level_comma(body, index)
            continue
        start = index
        depth, quote = 0, ""
        while index < length:
            char = body[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = ""
                index += 1
                continue
            if char == "/" and index + 1 < length and body[index + 1] == "/":
                newline = body.find("\n", index)
                index = length if newline < 0 else newline + 1
                continue
            if char in {"'", '"', "`"}:
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    index += 1
                    items.append(_TopLevelMenuItem(text=body[start:index], start=start, end=index))
                    break
            index += 1
        else:
            break
    return items


def _skip_to_top_level_comma(body: str, index: int) -> int:
    """从非对象起点跳到下一个顶层逗号或数组体末尾。"""

    length = len(body)
    depth, quote = 0, ""
    while index < length:
        char = body[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and index + 1 < length and body[index + 1] == "/":
            newline = body.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "]}":
            if depth == 0:
                return index
            depth -= 1
        elif char == "," and depth == 0:
            return index
        index += 1
    return index


def _top_level_menu_item_key(item_text: str) -> str | None:
    """提取顶层菜单项的 key 属性值，无 key 时返回 None。"""

    match = re.search(r"\bkey\s*:\s*(['\"])(.*?)\1", item_text)
    return match.group(2) if match else None


def _expand_menu_item_removal_span(body: str, start: int, end: int) -> tuple[int, int]:
    """把删除区间向前扩展吞掉前导空白，向后扩展吞掉尾随逗号与换行。"""

    span_start = start
    while span_start > 0 and body[span_start - 1] in " \t":
        span_start -= 1
    # 前一个非空白字符若是逗号或数组起始/换行边界，保留换行结构整洁。
    span_end = end
    while span_end < len(body) and body[span_end] in " \t":
        span_end += 1
    if span_end < len(body) and body[span_end] == ",":
        span_end += 1
    # 吞掉该项独占行的尾随换行，避免留下空行。
    while span_end < len(body) and body[span_end] in "\r\n":
        span_end += 1
    return span_start, span_end


def _biz_menus_array_bounds(content: str) -> tuple[int, int]:
    """定位 BIZ_MENUS 顶层数组边界，并跳过字符串与行注释。"""

    declaration = re.search(r"export\s+const\s+BIZ_MENUS\b", content)
    if declaration is None:
        raise ValueError("menus.ts 中找不到 BIZ_MENUS 声明。")
    assign_index = content.find("=", declaration.end())
    opening = content.find("[", assign_index)
    if assign_index < 0 or opening < 0:
        raise ValueError("menus.ts 中找不到 BIZ_MENUS 数组。")
    depth, index, quote = 1, opening + 1, ""
    while index < len(content):
        char = content[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and index + 1 < len(content) and content[index + 1] == "/":
            newline = content.find("\n", index)
            index = len(content) if newline < 0 else newline + 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
            if depth == 0:
                return opening, index
        index += 1
    raise ValueError("menus.ts 中 BIZ_MENUS 数组没有匹配的闭合括号。")


def _menu_item_source(page: dict[str, Any], *, indent: int) -> str:
    """把页面序列化成可追加到 BIZ_MENUS 顶层的 TypeScript 对象。"""

    pad, inner = "  " * indent, "  " * (indent + 1)
    route_path = str(page.get("path") or "").strip()
    lines = [f"{pad}{{\n", f"{inner}path: {json.dumps(route_path, ensure_ascii=False)},\n", f"{inner}name: {json.dumps(str(page.get('name') or ''), ensure_ascii=False)},\n", f"{inner}key: {json.dumps(str(page.get('key') or ''), ensure_ascii=False)},\n"]
    if any(part.startswith(":") for part in route_path.split("/")):
        lines.append(f"{inner}hideInMenu: true,\n")
    lines.append(f"{pad}}},\n")
    return "".join(lines)


def _write_text_atomically(path: Path, content: str) -> None:
    """通过同目录临时文件原子替换菜单，避免留下部分写入。"""

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
