"""将确认权限事实直接编译为前端资源常量和显式业务路由。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.services.ui_design_generator import derive_page_key


RESOURCES_RELATIVE_PATH = Path("frontend/src/authorization/resources.ts")
ROUTES_RELATIVE_PATH = Path("frontend/src/routes/index.tsx")
IMPORT_START = "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START"
IMPORT_END = "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END"
ROUTES_START = "// XCODEAGENT_BUSINESS_ROUTES_START"
ROUTES_END = "// XCODEAGENT_BUSINESS_ROUTES_END"


class AuthorizationFrontendProjectionError(ValueError):
    """表示前端资源常量或业务路由无法按确认权限事实安全生成。"""


def compile_frontend_authorization_projection(project_plan: dict[str, Any]) -> dict[str, Any] | None:
    """从完整 TechnicalPlan 编译前端唯一资源目录和全部业务页面路由。"""

    manifest = project_plan.get("authorization_manifest")
    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return None
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    page_resource_keys = {
        str(item.get("pageId") or "").strip(): str(item.get("resourceKey") or "").strip()
        for item in _dict_items(bindings.get("pages"))
        if str(item.get("pageId") or "").strip() and str(item.get("resourceKey") or "").strip()
    }
    resources = _resource_catalog(manifest.get("resources"))
    pages = _page_projection_items(project_plan, page_resource_keys, resources)
    return {"resources": resources, "pages": pages}


def apply_authorization_frontend_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """原子写入 resources.ts，并在模板托管区生成显式业务 RouteGuard 路由。"""

    if projection is None:
        return {"applied": False, "reason": "authorization_disabled"}
    value = _projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    resources_path = root / RESOURCES_RELATIVE_PATH
    routes_path = root / ROUTES_RELATIVE_PATH
    if not routes_path.is_file():
        raise AuthorizationFrontendProjectionError("auth 模板缺少 frontend/src/routes/index.tsx。")
    route_source = routes_path.read_text(encoding="utf-8")
    _managed_bounds(route_source, IMPORT_START, IMPORT_END)
    _managed_bounds(route_source, ROUTES_START, ROUTES_END)
    _write_text_atomically(resources_path, _render_resources(value["resources"]))
    updated = _replace_managed(route_source, IMPORT_START, IMPORT_END, _render_imports(value["pages"]))
    updated = _replace_managed(updated, ROUTES_START, ROUTES_END, _render_routes(value["pages"]))
    if updated != route_source:
        _write_text_atomically(routes_path, updated)
    return {
        "applied": True,
        "resourcesPath": str(RESOURCES_RELATIVE_PATH),
        "routesPath": str(ROUTES_RELATIVE_PATH),
        "resourceCount": len(value["resources"]),
        "pageCount": len(value["pages"]),
    }


def verify_authorization_frontend_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """只读验证资源常量和显式业务路由与确认投影一致。"""

    if projection is None:
        return {"verified": False, "reason": "authorization_disabled"}
    value = _projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    resources_path = root / RESOURCES_RELATIVE_PATH
    routes_path = root / ROUTES_RELATIVE_PATH
    if not resources_path.is_file() or not routes_path.is_file():
        raise AuthorizationFrontendProjectionError("auth 模板缺少前端资源常量或路由文件。")
    if resources_path.read_text(encoding="utf-8") != _render_resources(value["resources"]):
        raise AuthorizationFrontendProjectionError("前端 RESOURCES 与确认权限目录不一致。")
    source = routes_path.read_text(encoding="utf-8")
    imports_start, imports_end = _managed_bounds(source, IMPORT_START, IMPORT_END)
    routes_start, routes_end = _managed_bounds(source, ROUTES_START, ROUTES_END)
    if source[imports_start + len(IMPORT_START):imports_end] != "\n" + _render_imports(value["pages"]):
        raise AuthorizationFrontendProjectionError("前端业务路由 import 与确认页面不一致。")
    if source[routes_start + len(ROUTES_START):routes_end] != "\n" + _render_routes(value["pages"]):
        raise AuthorizationFrontendProjectionError("前端业务 RouteGuard 与确认页面权限不一致。")
    return {"verified": True, "resourceCount": len(value["resources"]), "pageCount": len(value["pages"])}


def resource_constant_reference(resource_key: str, resource_type: str, *, page_id: str = "", action_id: str = "") -> dict[str, str]:
    """把确认资源键转换为前端 RESOURCES 的稳定分组与属性名。"""

    group = {"system": "SYSTEM", "page": "PAGE", "operation": "OPERATION"}.get(resource_type)
    if not group:
        raise AuthorizationFrontendProjectionError(f"不支持的前端资源类型：{resource_type}。")
    if resource_type == "system":
        source = resource_key.removeprefix("system_")
    elif resource_type == "page":
        source = (page_id or resource_key).removeprefix("page_")
    else:
        source = "_".join(part for part in ((page_id or "").removeprefix("page_"), action_id) if part)
        source = source or resource_key.removeprefix("page_")
    name = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        raise AuthorizationFrontendProjectionError(f"资源 {resource_key} 无法生成合法 RESOURCES 常量名。")
    return {"group": group, "name": name, "resourceKey": resource_key}


def _resource_catalog(value: Any) -> list[dict[str, str]]:
    """收敛完整 manifest 资源目录，拒绝重复前端常量符号。"""

    result: list[dict[str, str]] = []
    symbols: set[tuple[str, str]] = set()
    for item in _dict_items(value):
        resource_key = str(item.get("resourceKey") or "").strip()
        resource_type = str(item.get("type") or "").strip()
        target = str(item.get("targetResourceRef") or "")
        page_id = target.removeprefix("page:") if target.startswith("page:") else ""
        action_parts = target.removeprefix("action:").split(":", 1) if target.startswith("action:") else []
        reference = resource_constant_reference(
            resource_key,
            resource_type,
            page_id=page_id or (action_parts[0] if len(action_parts) == 2 else ""),
            action_id=action_parts[1] if len(action_parts) == 2 else "",
        )
        symbol = (reference["group"], reference["name"])
        if symbol in symbols:
            raise AuthorizationFrontendProjectionError(f"RESOURCES 常量名冲突：{reference['group']}.{reference['name']}。")
        symbols.add(symbol)
        result.append(reference)
    return sorted(result, key=lambda item: (item["group"], item["name"]))


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
        item = {"pageId": page_id, "path": route, "pageKey": page_key}
        resource_key = page_keys.get(page_id)
        if resource_key:
            reference = resource_by_key.get(resource_key)
            if not reference or reference["group"] != "PAGE":
                raise AuthorizationFrontendProjectionError(f"受控页面 {page_id} 缺少 PAGE 资源常量。")
            item.update({"resourceGroup": reference["group"], "resourceName": reference["name"]})
        result.append(item)
    return sorted(result, key=lambda item: (item["path"], item["pageId"]))


def _projection_value(value: Any) -> dict[str, list[dict[str, str]]]:
    """验证持久化前端投影的最小结构。"""

    if not isinstance(value, dict):
        raise AuthorizationFrontendProjectionError("Build DAG 的 authorization_frontend_projection 必须是对象。")
    resources = _dict_items(value.get("resources"))
    pages = _dict_items(value.get("pages"))
    if not resources or not pages:
        raise AuthorizationFrontendProjectionError("前端权限投影缺少完整资源目录或业务页面。")
    return {"resources": resources, "pages": pages}


def _render_resources(resources: list[dict[str, str]]) -> str:
    """渲染前端唯一的完整 RESOURCES 常量目录。"""

    grouped = {group: [item for item in resources if item["group"] == group] for group in ("SYSTEM", "PAGE", "OPERATION")}
    lines = ["/** 由 XCodeAgent 根据确认权限目录生成，请勿手工修改。 */", "export const RESOURCES = {"]
    for group in ("SYSTEM", "PAGE", "OPERATION"):
        lines.append(f"  {group}: {{")
        for item in grouped[group]:
            lines.append(f"    {item['name']}: {json.dumps(item['resourceKey'], ensure_ascii=False)},")
        lines.append("  },")
    lines.extend(["} as const;", ""])
    return "\n".join(lines)


def _render_imports(pages: list[dict[str, str]]) -> str:
    """渲染业务页面和资源常量 import 托管区。"""

    lines = ["import { RESOURCES } from '@/authorization/resources';"]
    lines.extend(f"import {item['pageKey']} from '@/pages/{item['pageKey']}';" for item in pages)
    return "\n".join(lines) + "\n"


def _render_routes(pages: list[dict[str, str]]) -> str:
    """渲染全部业务页面的显式 Layout 子路由。"""

    lines: list[str] = []
    for item in pages:
        element = f"<{item['pageKey']} />"
        if item.get("resourceGroup") and item.get("resourceName"):
            element = (
                f"<RouteGuard resourceKey={{RESOURCES.{item['resourceGroup']}.{item['resourceName']}}}>"
                f"\n          <{item['pageKey']} />\n        </RouteGuard>"
            )
        lines.extend(["{", f"  path: {json.dumps(item['path'], ensure_ascii=False)},", "  element: <Layout />,", "  children: [", "    {", "      index: true,", "      element: (", f"        {element}", "      ),", "    },", "  ],", "},"])
    return "\n".join(lines) + ("\n" if lines else "")


def _managed_bounds(content: str, start_marker: str, end_marker: str) -> tuple[int, int]:
    """定位固定模板托管区，拒绝缺失或顺序错误的模板。"""

    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise AuthorizationFrontendProjectionError("auth 模板 routes/index.tsx 缺少有效业务路由托管标记。")
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
