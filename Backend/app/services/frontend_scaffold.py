"""工作区前端工程页面脚手架 — 在 detail_confirmation 完成后创建。

根据已确认的 ProjectPlan.frontend_pages，在 frontend/ 下生成：

1. src/constants/menus.ts — 完整的 BIZ_MENUS（包含所有项目页面菜单项）
2. src/pages/<PageKey>/index.tsx — 每个页面的 hello agent! 占位文件

后续 build 阶段的任务规划不再需要追加菜单项和创建页面目录。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.frontend_page_tree import (
    flatten_frontend_pages,
)

logger = logging.getLogger(__name__)

_MENUS_TS_HEADER = """\
import { Route } from '@/typings/workbench';

// TODO 菜单类型跟随antd
export const BIZ_MENUS: Route[] = [
"""


def _placeholder_page_source(page_name: str, page_key: str) -> str:
    """生成合法的 TSX 占位组件源码，避免 Vite/Babel 解析失败导致预览报错。"""

    component_name = "".join(
        part.capitalize() for part in page_key.replace("-", "_").split("_") if part
    ) or "PlaceholderPage"
    return (
        f"// {page_name or page_key} 页面（临时占位，待 Agent 生成真实内容）\n"
        f"export default function {component_name}() {{\n"
        f"  return <div>hello agent!</div>;\n"
        f"}}\n"
    )


def scaffold_frontend_pages(workspace_root: str, project_plan: dict[str, Any]) -> dict[str, Any]:
    """在 frontend/ 下生成页面菜单和占位文件。"""

    frontend_dir = Path(workspace_root) / "frontend"
    if not frontend_dir.is_dir():
        return {"status": "skipped", "reason": "frontend_dir_missing"}

    pages = _collect_project_pages(project_plan)
    if not pages:
        return {"status": "skipped", "reason": "no_pages_in_project_plan"}

    created_dirs = _create_page_directories(frontend_dir, pages)
    menu_path = _write_menus_ts(frontend_dir, pages)

    return {
        "status": "completed",
        "pages": pages,
        "created_directories": created_dirs,
        "menus_path": str(menu_path),
    }


def _collect_project_pages(project_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """从 ProjectPlan.frontend_pages 拍平并提取需要脚手架化的页面。"""

    raw = flatten_frontend_pages(project_plan.get("frontend_pages", []))
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in raw:
        pid = str(page.get("pageId") or page.get("id") or "").strip()
        name = str(page.get("name") or pid or f"Page{len(pages) + 1}")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        pages.append({
            "pageId": pid,
            "name": name,
            "path": str(page.get("path") or "/"),
            "key": _derive_page_key(pid),
        })
    return pages


def _derive_page_key(page_id: str) -> str:
    """从 pageId 推导 PascalCase 的页面组件名/目录名。"""

    # 把 snake_case → PascalCase
    parts = page_id.split("_")
    return "".join(part.capitalize() for part in parts if part)


def _create_page_directories(
    frontend_dir: Path,
    pages: list[dict[str, Any]],
) -> list[str]:
    """为每个页面创建 src/pages/<key>/index.tsx 并写入 hello agent! 占位内容。"""

    pages_dir = frontend_dir / "src" / "pages"
    created: list[str] = []
    for page in pages:
        key = page["key"]
        page_dir = pages_dir / key
        try:
            page_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("scaffold_page_mkdir_failed dir=%s", page_dir)
            continue
        tsx_path = page_dir / "index.tsx"
        try:
            if tsx_path.exists():
                # 已有 index.tsx 不覆盖（可能是 build 阶段生成的代码）
                created.append(str(tsx_path.relative_to(frontend_dir)))
                continue
            tsx_path.write_text(
                _placeholder_page_source(page.get("name", ""), key),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("scaffold_page_write_failed path=%s", tsx_path)
            continue
        created.append(str(tsx_path.relative_to(frontend_dir)))
    return created


def _write_menus_ts(
    frontend_dir: Path,
    pages: list[dict[str, Any]],
) -> Path:
    """重写 src/constants/menus.ts，只包含项目页面的 BIZ_MENUS。"""

    lines: list[str] = [_MENUS_TS_HEADER]

    for page in pages:
        _write_menu_item(lines, page, indent=1)

    lines.append("];\n")

    menus_path = frontend_dir / "src" / "constants" / "menus.ts"
    try:
        menus_path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        logger.exception("scaffold_menus_write_failed path=%s", menus_path)
    return menus_path


def _write_menu_item(
    lines: list[str],
    item: dict[str, Any],
    *,
    indent: int = 0,
) -> None:
    """将单个菜单项格式化为 JS 对象文本追加到 lines。"""

    pad = "  " * indent
    inner_pad = "  " * (indent + 1)

    # 使用 ensure_ascii=False，使中文保持可读且与 build_task_menu 的 regex 兼容
    # （_typescript_string_property 从文件读取后无法还原 \uXXXX 转义序列）
    lines.append(f"{pad}{{\n")
    lines.append(f'{inner_pad}path: {json.dumps(item.get("path", ""), ensure_ascii=False)},\n')
    lines.append(f'{inner_pad}name: {json.dumps(item.get("name", ""), ensure_ascii=False)},\n')

    icon = item.get("icon")
    if icon:
        lines.append(f"{inner_pad}icon: {json.dumps(icon, ensure_ascii=False)},\n")

    target = item.get("target")
    if target:
        lines.append(f"{inner_pad}target: {json.dumps(target, ensure_ascii=False)},\n")

    key = item.get("key")
    if key:
        lines.append(f"{inner_pad}key: {json.dumps(key, ensure_ascii=False)},\n")

    children = item.get("children")
    if isinstance(children, list) and children:
        lines.append(f"{inner_pad}children: [\n")
        for child in children:
            _write_menu_item(lines, child, indent=indent + 2)
        lines.append(f"{inner_pad}],\n")

    lines.append(f"{pad}}},\n")
