"""将确认权限事实直接编译为前端资源常量和显式业务路由。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.services.authorization_resource_catalog import (
    AuthorizationFrontendProjectionError,
    compile_frontend_resource_catalog,
    resource_constant_reference,
)
from app.services.ui_design_generator import derive_page_key


RESOURCES_RELATIVE_PATH = Path("frontend/src/constants/resources.ts")
ROUTES_RELATIVE_PATH = Path("frontend/src/constants/routes.tsx")
IMPORT_START = "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START"
IMPORT_END = "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END"
ROUTES_START = "// XCODEAGENT_BUSINESS_ROUTES_START"
ROUTES_END = "// XCODEAGENT_BUSINESS_ROUTES_END"


def compile_frontend_resources_projection(project_plan: dict[str, Any]) -> dict[str, Any] | None:
    """从完整 TechnicalPlan 纯编译前端资源目录，不读取或校验页面路由。"""

    manifest = project_plan.get("authorization_manifest")
    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return None
    catalog = compile_frontend_resource_catalog(manifest)
    if catalog is None:
        return None
    return {"resources": catalog.frontend_resources()}


def compile_frontend_routes_projection(project_plan: dict[str, Any]) -> dict[str, Any] | None:
    """从完整 TechnicalPlan 纯编译业务页面路由及其资源常量引用。"""

    manifest = project_plan.get("authorization_manifest")
    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return None
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    page_resource_keys = {
        str(item.get("pageId") or "").strip(): str(item.get("resourceKey") or "").strip()
        for item in _dict_items(bindings.get("pages"))
        if str(item.get("pageId") or "").strip() and str(item.get("resourceKey") or "").strip()
    }
    catalog = compile_frontend_resource_catalog(manifest)
    if catalog is None:
        return None
    resources = catalog.frontend_resources()
    pages = _page_projection_items(project_plan, page_resource_keys, resources)
    return {"pages": pages}


def compile_frontend_authorization_projection(project_plan: dict[str, Any]) -> dict[str, Any] | None:
    """兼容现有 Build Plan 结构，组合独立的资源与路由纯编译结果。"""

    resources_projection = compile_frontend_resources_projection(project_plan)
    routes_projection = compile_frontend_routes_projection(project_plan)
    if resources_projection is None or routes_projection is None:
        return None
    return {**resources_projection, **routes_projection}


def apply_frontend_resources_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """仅写入 auth-guard Task 唯一所有的 resources.ts。"""

    if projection is None:
        return {"applied": False, "reason": "authorization_disabled"}
    resources = _resources_projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    resources_path = root / RESOURCES_RELATIVE_PATH
    _write_text_atomically(resources_path, _render_resources(resources))
    return {
        "applied": True,
        "resourcesPath": str(RESOURCES_RELATIVE_PATH),
        "resourceCount": len(resources),
    }


def apply_frontend_routes_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """仅更新 routes.tsx 的业务 import 与 PAGE_ROUTES 托管区。"""

    if projection is None:
        return {"applied": False, "reason": "authorization_disabled"}
    pages = _routes_projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    routes_path = root / ROUTES_RELATIVE_PATH
    if not routes_path.is_file():
        raise AuthorizationFrontendProjectionError("auth 模板缺少 frontend/src/constants/routes.tsx。")
    route_source = routes_path.read_text(encoding="utf-8")
    _managed_bounds(route_source, IMPORT_START, IMPORT_END)
    _managed_bounds(route_source, ROUTES_START, ROUTES_END)
    updated = _replace_managed(route_source, IMPORT_START, IMPORT_END, _render_imports(pages))
    updated = _replace_managed(updated, ROUTES_START, ROUTES_END, _render_routes(pages))
    if updated != route_source:
        _write_text_atomically(routes_path, updated)
    return {
        "applied": True,
        "routesPath": str(ROUTES_RELATIVE_PATH),
        "pageCount": len(pages),
    }


def verify_frontend_resources_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """只读验证 resources.ts 与资源投影完全一致。"""

    if projection is None:
        return {"verified": False, "reason": "authorization_disabled"}
    resources = _resources_projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    resources_path = root / RESOURCES_RELATIVE_PATH
    if not resources_path.is_file():
        raise AuthorizationFrontendProjectionError("auth 模板缺少 frontend/src/constants/resources.ts。")
    if resources_path.read_text(encoding="utf-8") != _render_resources(resources):
        raise AuthorizationFrontendProjectionError("前端 RESOURCES 与确认权限目录不一致。")
    return {"verified": True, "resourceCount": len(resources)}


def verify_frontend_routes_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """只读验证 routes.tsx 的业务托管区与路由投影完全一致。"""

    if projection is None:
        return {"verified": False, "reason": "authorization_disabled"}
    pages = _routes_projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    routes_path = root / ROUTES_RELATIVE_PATH
    if not routes_path.is_file():
        raise AuthorizationFrontendProjectionError("auth 模板缺少 frontend/src/constants/routes.tsx。")
    source = routes_path.read_text(encoding="utf-8")
    imports_start, imports_end = _managed_bounds(source, IMPORT_START, IMPORT_END)
    routes_start, routes_end = _managed_bounds(source, ROUTES_START, ROUTES_END)
    if source[imports_start + len(IMPORT_START):imports_end] != "\n" + _render_imports(pages):
        raise AuthorizationFrontendProjectionError("前端业务路由 import 与确认页面不一致。")
    if source[routes_start + len(ROUTES_START):routes_end] != "\n" + _render_routes(pages):
        raise AuthorizationFrontendProjectionError("前端业务 PAGE_ROUTES 与确认页面权限不一致。")
    return {"verified": True, "pageCount": len(pages)}


def verify_authorization_frontend_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """组合独立只读校验，保持完整前端权限 EDD 结果兼容。"""

    if projection is None:
        return {"verified": False, "reason": "authorization_disabled"}
    resources_result = verify_frontend_resources_projection(workspace, projection)
    routes_result = verify_frontend_routes_projection(workspace, projection)
    return {
        "verified": True,
        "resourceCount": resources_result["resourceCount"],
        "pageCount": routes_result["pageCount"],
    }


def _page_projection_items(project_plan: dict[str, Any], page_keys: dict[str, str], resources: list[dict[str, str]]) -> list[dict[str, str]]:
    """从 TechnicalPlan 页面记录生成组件、路径和可选页面资源引用。"""

    resource_by_key = {item["resourceKey"]: item for item in resources}
    pages = project_plan.get("pages") if isinstance(project_plan.get("pages"), list) else []
    used_keys: set[str] = {"DefaultPage"}
    result: list[dict[str, str]] = []
    for page in _dict_items(pages):
        page_id = str(page.get("pageId") or page.get("id") or "").strip()
        route = str(page.get("path") or "").strip()
        if not page_id or not route.startswith("/"):
            raise AuthorizationFrontendProjectionError("TechnicalPlan 页面缺少合法 pageId 或 path。")
        page_key = derive_page_key(page, used_keys)
        used_keys.add(page_key)
        item = {
            "pageId": page_id,
            "path": route,
            "pageKey": page_key,
            "name": str(page.get("name") or page_id),
            "menu": not any(part.startswith(":") for part in route.split("/")),
        }
        resource_key = page_keys.get(page_id)
        if resource_key:
            reference = resource_by_key.get(resource_key)
            if not reference or reference["group"] != "PAGE":
                raise AuthorizationFrontendProjectionError(f"受控页面 {page_id} 缺少 PAGE 资源常量。")
            item.update({"resourceGroup": reference["group"], "resourceName": reference["name"]})
        result.append(item)
    return sorted(result, key=lambda item: (item["path"], item["pageId"]))


def _resources_projection_value(value: Any) -> list[dict[str, str]]:
    """验证资源投影的独立最小结构。"""

    if not isinstance(value, dict):
        raise AuthorizationFrontendProjectionError("Build DAG 的 authorization_frontend_projection 必须是对象。")
    resources = _dict_items(value.get("resources"))
    if not resources:
        raise AuthorizationFrontendProjectionError("前端权限资源投影缺少完整资源目录。")
    return resources


def _routes_projection_value(value: Any) -> list[dict[str, str]]:
    """验证路由投影的独立最小结构。"""

    if not isinstance(value, dict):
        raise AuthorizationFrontendProjectionError("Build DAG 的 authorization_frontend_projection 必须是对象。")
    pages = _dict_items(value.get("pages"))
    if not pages:
        raise AuthorizationFrontendProjectionError("前端权限路由投影缺少业务页面。")
    return pages


def _render_resources(resources: list[dict[str, str]]) -> str:
    """渲染前端唯一的完整 RESOURCES 常量目录。"""

    grouped = {group: [item for item in resources if item["group"] == group] for group in ("SYSTEM", "PAGE", "OPERATION")}
    lines = ["/** 由 AIStudio 根据确认权限目录生成，请勿手工修改。 */", "export const RESOURCES = {"]
    for group in ("SYSTEM", "PAGE", "OPERATION"):
        lines.append(f"  {group}: {{")
        for item in grouped[group]:
            lines.append(f"    {item['name']}: {json.dumps(item['resourceKey'], ensure_ascii=False)},")
        lines.append("  },")
    lines.extend(["} as const;", ""])
    return "\n".join(lines)


def _render_imports(pages: list[dict[str, str]]) -> str:
    """渲染业务页面组件 import 托管区。"""

    lines = [f"import {item['pageKey']} from '@/pages/{item['pageKey']}';" for item in pages]
    return "\n".join(lines) + "\n"


def _render_routes(pages: list[dict[str, str]]) -> str:
    """渲染 PAGE_ROUTES 配置；模板负责 RouteGuard 和菜单派生。"""

    lines: list[str] = []
    for item in pages:
        lines.extend(["{", f"  path: {json.dumps(item['path'], ensure_ascii=False)},"])
        if item.get("resourceGroup") and item.get("resourceName"):
            lines.append(f"  resourceKey: RESOURCES.{item['resourceGroup']}.{item['resourceName']},")
        if item.get("menu"):
            lines.append(
                f"  menu: {{ key: {json.dumps(item['pageId'], ensure_ascii=False)}, "
                f"label: {json.dumps(item['name'], ensure_ascii=False)} }},"
            )
        lines.extend([f"  element: <{item['pageKey']} />", "},"])
    return "\n".join(lines) + ("\n" if lines else "")


def _managed_bounds(content: str, start_marker: str, end_marker: str) -> tuple[int, int]:
    """定位固定模板托管区，拒绝缺失或顺序错误的模板。"""

    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise AuthorizationFrontendProjectionError("auth 模板 constants/routes.tsx 缺少有效业务路由托管标记。")
    return start, end


def _replace_managed(content: str, start_marker: str, end_marker: str, rendered: str) -> str:
    """仅替换模板已声明的业务路由托管区正文。"""

    start, end = _managed_bounds(content, start_marker, end_marker)
    body_start = start + len(start_marker)
    return content[:body_start] + "\n" + rendered + content[end:]


def _write_text_atomically(path: Path, content: str) -> None:
    """原子写入平台拥有的前端资源或路由文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
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


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信数组中仅保留对象，避免投影遍历异常。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
