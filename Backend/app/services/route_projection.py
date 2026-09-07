"""将确认页面事实投影到所有模板共享的 routes.tsx 托管区。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.services.ui_design_generator import derive_page_key

ROUTES_RELATIVE_PATH = Path("frontend/src/constants/routes.tsx")
IMPORT_START = "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START"
IMPORT_END = "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END"
ROUTES_START = "// XCODEAGENT_BUSINESS_ROUTES_START"
ROUTES_END = "// XCODEAGENT_BUSINESS_ROUTES_END"


class RouteProjectionError(ValueError):
    """表示共享业务路由托管区或确认页面事实不满足投影契约。"""


def compile_route_projection(project_plan: dict[str, Any]) -> dict[str, list[dict[str, str | bool]]]:
    """从确认的 TechnicalPlan 编译全部业务页面，完全不依赖权限能力。"""

    pages = project_plan.get("pages") if isinstance(project_plan, dict) else None
    if not isinstance(pages, list):
        raise RouteProjectionError("TechnicalPlan 缺少 pages 数组，不能生成业务路由。")
    used_keys: set[str] = {"DefaultPage"}
    result: list[dict[str, str | bool]] = []
    for page in pages:
        if not isinstance(page, dict):
            raise RouteProjectionError("TechnicalPlan.pages 包含非法页面对象。")
        page_id = str(page.get("pageId") or page.get("id") or "").strip()
        route = str(page.get("path") or "").strip()
        if not page_id or not route.startswith("/"):
            raise RouteProjectionError("TechnicalPlan 页面缺少合法 pageId 或 path。")
        page_key = derive_page_key(page, used_keys)
        used_keys.add(page_key)
        result.append(
            {
                "pageId": page_id,
                "path": route,
                "pageKey": page_key,
                "name": str(page.get("name") or page_id),
                "menu": not any(part.startswith(":") for part in route.split("/")),
            }
        )
    return {"pages": sorted(result, key=lambda item: (str(item["path"]), str(item["pageId"])))}


def apply_route_projection(
    workspace: str | Path,
    projection: Any,
    *,
    authorization_decorations: Any = None,
) -> dict[str, Any]:
    """幂等写入全部业务页面路由，并按可选 decoration 标注受控页面。"""

    value = _projection_value(projection)
    decorations = _decoration_map(authorization_decorations, value["pages"])
    root = Path(workspace).expanduser().resolve()
    _validate_page_entries(root, value["pages"])
    routes_path = root / ROUTES_RELATIVE_PATH
    if not routes_path.is_file() or routes_path.is_symlink():
        raise RouteProjectionError("模板缺少 frontend/src/constants/routes.tsx。")
    source = routes_path.read_text(encoding="utf-8")
    _managed_bounds(source, IMPORT_START, IMPORT_END)
    _managed_bounds(source, ROUTES_START, ROUTES_END)
    updated = _replace_managed(source, IMPORT_START, IMPORT_END, _render_imports(value["pages"]))
    updated = _replace_managed(
        updated,
        ROUTES_START,
        ROUTES_END,
        _render_routes(value["pages"], decorations),
    )
    if updated != source:
        _write_text_atomically(routes_path, updated)
    return {
        "applied": True,
        "routesPath": str(ROUTES_RELATIVE_PATH),
        "pageCount": len(value["pages"]),
        "protectedPageCount": len(decorations),
    }


def verify_route_projection(
    workspace: str | Path,
    projection: Any,
    *,
    authorization_decorations: Any = None,
) -> dict[str, Any]:
    """只读核对共享路由托管区，不以重写掩盖 marker 或内容漂移。"""

    value = _projection_value(projection)
    decorations = _decoration_map(authorization_decorations, value["pages"])
    routes_path = Path(workspace).expanduser().resolve() / ROUTES_RELATIVE_PATH
    if not routes_path.is_file() or routes_path.is_symlink():
        raise RouteProjectionError("模板缺少 frontend/src/constants/routes.tsx。")
    source = routes_path.read_text(encoding="utf-8")
    imports_start, imports_end = _managed_bounds(source, IMPORT_START, IMPORT_END)
    routes_start, routes_end = _managed_bounds(source, ROUTES_START, ROUTES_END)
    if source[imports_start + len(IMPORT_START):imports_end] != "\n" + _render_imports(value["pages"]):
        raise RouteProjectionError("业务路由 import 与确认页面事实不一致。")
    if source[routes_start + len(ROUTES_START):routes_end] != "\n" + _render_routes(value["pages"], decorations):
        raise RouteProjectionError("业务路由与确认页面或权限 decoration 不一致。")
    return {"verified": True, "pageCount": len(value["pages"]), "protectedPageCount": len(decorations)}


def _projection_value(value: Any) -> dict[str, list[dict[str, str | bool]]]:
    """严格校验持久化 Route Projection 的页面集合。"""

    pages = value.get("pages") if isinstance(value, dict) else None
    if not isinstance(pages, list) or not pages:
        raise RouteProjectionError("Build DAG 的 route_projection 必须包含非空 pages 数组。")
    result: list[dict[str, str | bool]] = []
    page_ids: set[str] = set()
    paths: set[str] = set()
    for item in pages:
        if not isinstance(item, dict):
            raise RouteProjectionError("route_projection.pages 包含非法页面对象。")
        page_id = str(item.get("pageId") or "").strip()
        path = str(item.get("path") or "").strip()
        page_key = str(item.get("pageKey") or "").strip()
        if not page_id or not path.startswith("/") or not page_key or page_id in page_ids or path in paths:
            raise RouteProjectionError("route_projection.pages 包含缺失、重复或非法页面标识。")
        page_ids.add(page_id)
        paths.add(path)
        result.append({"pageId": page_id, "path": path, "pageKey": page_key, "name": str(item.get("name") or page_id), "menu": item.get("menu") is True})
    return {"pages": sorted(result, key=lambda item: (str(item["path"]), str(item["pageId"]))) }


def _decoration_map(value: Any, pages: list[dict[str, str | bool]]) -> dict[str, str]:
    """验证 authorization 仅能为已知页面添加 resourceKey decoration。"""

    if value is None:
        return {}
    if not isinstance(value, list):
        raise RouteProjectionError("authorization route decorations 必须是数组。")
    known = {str(item["pageId"]) for item in pages}
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            raise RouteProjectionError("authorization route decoration 包含非法对象。")
        page_id = str(item.get("pageId") or "").strip()
        resource_key = str(item.get("resourceKey") or "").strip()
        if not page_id or not resource_key or page_id not in known or page_id in result:
            raise RouteProjectionError("authorization route decoration 包含未知、重复或不完整页面。")
        result[page_id] = resource_key
    return result


def _validate_page_entries(workspace: Path, pages: list[dict[str, str | bool]]) -> None:
    """要求页面任务已生成真实入口，Route Projection 绝不创建 placeholder。"""

    for item in pages:
        page_key = str(item["pageKey"])
        entry = workspace / "frontend" / "src" / "pages" / page_key / "index.tsx"
        if not entry.is_file() or entry.is_symlink():
            raise RouteProjectionError(f"Route Projection 缺少真实页面入口：{entry.relative_to(workspace)}。")


def _render_imports(pages: list[dict[str, str | bool]]) -> str:
    """渲染全部业务页面组件 import。"""

    return "\n".join(f"import {item['pageKey']} from '@/pages/{item['pageKey']}';" for item in pages) + "\n"


def _render_routes(pages: list[dict[str, str | bool]], decorations: dict[str, str]) -> str:
    """渲染业务路由；仅受控页携带模板消费的 resourceKey。"""

    lines: list[str] = []
    for item in pages:
        lines.extend(["{", f"  path: {json.dumps(item['path'], ensure_ascii=False)},"])
        resource_key = decorations.get(str(item["pageId"]))
        if resource_key:
            lines.append(f"  resourceKey: {json.dumps(resource_key, ensure_ascii=False)},")
        if item["menu"]:
            lines.append(f"  menu: {{ key: {json.dumps(item['pageId'], ensure_ascii=False)}, label: {json.dumps(item['name'], ensure_ascii=False)} }},")
        lines.extend([f"  element: <{item['pageKey']} />", "},"])
    return "\n".join(lines) + "\n"


def _managed_bounds(content: str, start_marker: str, end_marker: str) -> tuple[int, int]:
    """定位唯一且顺序正确的 routes.tsx 托管区，检测缺失、重复和错序。"""

    if content.count(start_marker) != 1 or content.count(end_marker) != 1:
        raise RouteProjectionError("模板 routes.tsx 托管标记必须各出现一次。")
    start = content.find(start_marker)
    end = content.find(end_marker)
    if end <= start:
        raise RouteProjectionError("模板 routes.tsx 托管标记顺序错误。")
    return start, end


def _replace_managed(content: str, start_marker: str, end_marker: str, rendered: str) -> str:
    """仅替换固定 marker 间正文，保留模板拥有的其他内容。"""

    start, end = _managed_bounds(content, start_marker, end_marker)
    return content[:start + len(start_marker)] + "\n" + rendered + content[end:]


def _write_text_atomically(path: Path, content: str) -> None:
    """原子写入平台拥有的共享路由文件。"""

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
