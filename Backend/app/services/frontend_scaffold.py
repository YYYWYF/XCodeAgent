"""根据正式 ProductPlan 和 UiDesign 幂等补齐页面占位与菜单入口。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.services.ui_design_generator import derive_page_key


def collect_template_pages(
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any],
) -> list[dict[str, Any]]:
    """合并 ProductPlan 页面事实与已确认 UiDesign PageKey 映射。"""

    raw_pages = product_plan.get("pages")
    pages = [item for item in raw_pages if isinstance(item, dict)] if isinstance(raw_pages, list) else []
    ui_status = str(ui_designs.get("confirmation_status") or "")
    if ui_status not in {"confirmed", "skipped"}:
        raise ValueError("UiDesign 必须已确认或明确跳过，才能初始化模板页面。")

    ui_keys: dict[str, str] = {}
    if ui_status == "confirmed":
        raw_ui_pages = ui_designs.get("pages")
        for item in raw_ui_pages if isinstance(raw_ui_pages, list) else []:
            if not isinstance(item, dict):
                continue
            page_id = str(item.get("pageId") or item.get("id") or "").strip()
            page_key = str(item.get("page_key") or item.get("pageKey") or "").strip()
            if page_id and page_key:
                ui_keys[page_id] = page_key

    collected: list[dict[str, Any]] = []
    seen_page_ids: set[str] = set()
    # 与 UI 确认阶段共同保留模板自带 DefaultPage，碰撞时使用相同数字后缀。
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
            raise ValueError(
                f"UiDesign 页面 {page_id} 的 page_key 与共享派生规则不一致："
                f"{page_key} != {derived_key}"
            )
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", page_key):
            raise ValueError(f"非法页面 PageKey：{page_key or '<empty>'}")
        collected.append(
            {
                "pageId": page_id,
                "name": str(raw_page.get("name") or page_id),
                "path": str(raw_page.get("path") or "/"),
                "key": page_key,
            }
        )
    return collected


def ensure_frontend_page_placeholders(
    frontend_dir: Path,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """只创建缺失页面占位文件，并返回 manifest 所需的结构化结果。"""

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
            # 独占创建避免重复进入覆盖已生成的真实页面代码。
            with target.open("x", encoding="utf-8") as handle:
                handle.write(_placeholder_page_source(str(page.get("name") or ""), page_key))
        except FileExistsError:
            existing.append(relative_path)
        else:
            created.append(relative_path)

    missing = [
        relative_path
        for relative_path in expected
        if not (frontend_dir.parent / relative_path).is_file()
    ]
    return {
        "status": "succeeded" if not missing else "failed",
        "expectedFiles": expected,
        "existingFiles": existing,
        "createdFiles": created,
        "missingFiles": missing,
        "error": None if not missing else "部分页面占位文件写入后仍然缺失。",
    }


def ensure_frontend_menu_entries(
    frontend_dir: Path,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """只向 BIZ_MENUS 顶层追加缺失页面入口，保留已有菜单。"""

    menus_path = frontend_dir / "src" / "constants" / "menus.ts"
    if not menus_path.is_file():
        raise FileNotFoundError(f"模板菜单文件不存在：{menus_path}")
    content = menus_path.read_text(encoding="utf-8")
    opening, closing = _biz_menus_array_bounds(content)
    expected_keys = [str(page.get("key") or "").strip() for page in pages]
    existing_keys = _typescript_menu_keys(content[opening + 1 : closing])
    missing_pages = [page for page in pages if str(page.get("key") or "") not in existing_keys]
    injected_keys: list[str] = []
    if missing_pages:
        current_body = content[opening + 1 : closing]
        separator = "," if current_body.strip() and not current_body.rstrip().endswith(",") else ""
        insertion = "".join(_menu_item_source(page, indent=1) for page in missing_pages)
        new_body = f"{current_body.rstrip()}{separator}\n{insertion}"
        content = content[: opening + 1] + new_body + content[closing:]
        _write_text_atomically(menus_path, content)
        injected_keys = [str(page.get("key") or "") for page in missing_pages]

    final_opening, final_closing = _biz_menus_array_bounds(content)
    final_keys = _typescript_menu_keys(content[final_opening + 1 : final_closing])
    missing_keys = [key for key in expected_keys if key not in final_keys]
    return {
        "status": "succeeded" if not missing_keys else "failed",
        "path": menus_path.relative_to(frontend_dir.parent).as_posix(),
        "expectedKeys": expected_keys,
        "existingKeys": [key for key in expected_keys if key in existing_keys],
        "injectedKeys": injected_keys,
        "missingKeys": missing_keys,
        "error": None if not missing_keys else "部分 ProductPlan 菜单项注入后仍然缺失。",
    }


def inspect_frontend_menu_entries(
    frontend_dir: Path,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """只读检查最新 ProductPlan 所需菜单 key 是否已经存在。"""

    menus_path = frontend_dir / "src" / "constants" / "menus.ts"
    expected = [str(page.get("key") or "") for page in pages]
    if not menus_path.is_file():
        return {
            "path": menus_path.relative_to(frontend_dir.parent).as_posix(),
            "expectedKeys": expected,
            "missingKeys": expected,
            "error": "模板菜单文件不存在。",
        }
    try:
        content = menus_path.read_text(encoding="utf-8")
        opening, closing = _biz_menus_array_bounds(content)
        keys = _typescript_menu_keys(content[opening + 1 : closing])
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "path": menus_path.relative_to(frontend_dir.parent).as_posix(),
            "expectedKeys": expected,
            "missingKeys": expected,
            "error": str(exc),
        }
    return {
        "path": menus_path.relative_to(frontend_dir.parent).as_posix(),
        "expectedKeys": expected,
        "missingKeys": [key for key in expected if key not in keys],
        "error": None,
    }


def _placeholder_page_source(page_name: str, page_key: str) -> str:
    """生成合法的 TSX 占位组件源码。"""

    return (
        f"// {page_name or page_key} 页面（临时占位，待 Agent 生成真实内容）\n"
        f"export default function {page_key}() {{\n"
        f"  return <div>hello agent!</div>;\n"
        f"}}\n"
    )


def _typescript_menu_keys(content: str) -> set[str]:
    """从 TypeScript 菜单源码中提取单双引号 key 属性。"""

    return {
        match.group(2)
        for match in re.finditer(r"\bkey\s*:\s*(['\"])(.*?)\1", content)
        if match.group(2)
    }


def _biz_menus_array_bounds(content: str) -> tuple[int, int]:
    """定位 BIZ_MENUS 顶层数组边界，并跳过字符串与行注释。"""

    declaration = re.search(r"export\s+const\s+BIZ_MENUS\b", content)
    if declaration is None:
        raise ValueError("menus.ts 中找不到 BIZ_MENUS 声明。")
    assign_index = content.find("=", declaration.end())
    opening = content.find("[", assign_index)
    if assign_index < 0 or opening < 0:
        raise ValueError("menus.ts 中找不到 BIZ_MENUS 数组。")
    depth = 1
    index = opening + 1
    quote = ""
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

    pad = "  " * indent
    inner = "  " * (indent + 1)
    # 保留 ProductPlan 的完整确认路径，与 Build 阶段确定性菜单登记检查一致。
    route_path = str(page.get("path") or "").strip()
    lines = [f"{pad}{{\n"]
    lines.append(f"{inner}path: {json.dumps(route_path, ensure_ascii=False)},\n")
    lines.append(f"{inner}name: {json.dumps(str(page.get('name') or ''), ensure_ascii=False)},\n")
    lines.append(f"{inner}key: {json.dumps(str(page.get('key') or ''), ensure_ascii=False)},\n")
    if any(part.startswith(":") for part in route_path.split("/")):
        lines.append(f"{inner}hideInMenu: true,\n")
    lines.append(f"{pad}}},\n")
    return "".join(lines)


def _write_text_atomically(path: Path, content: str) -> None:
    """通过同目录临时文件原子替换菜单，避免留下部分写入。"""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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
