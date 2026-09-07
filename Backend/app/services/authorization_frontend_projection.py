"""将确认权限事实编译为资源常量与受控页 route decoration。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

RESOURCES_RELATIVE_PATH = Path("frontend/src/constants/resources.ts")


class AuthorizationFrontendProjectionError(ValueError):
    """表示前端资源常量或业务路由无法按确认权限事实安全生成。"""


def compile_frontend_authorization_projection(project_plan: dict[str, Any]) -> dict[str, Any] | None:
    """从完整 TechnicalPlan 编译资源目录和受控页 decoration，不拥有普通路由。"""

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
    decorations = _route_decorations(page_resource_keys, resources)
    return {"resources": resources, "routeDecorations": decorations}


def apply_authorization_frontend_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """为 authorization effective 模板写入唯一 RESOURCES 目录。"""

    if projection is None:
        return {"applied": False, "reason": "authorization_disabled"}
    value = _projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    resources_path = root / RESOURCES_RELATIVE_PATH
    _write_text_atomically(resources_path, _render_resources(value["resources"]))
    return {
        "applied": True,
        "resourcesPath": str(RESOURCES_RELATIVE_PATH),
        "resourceCount": len(value["resources"]),
        "protectedPageCount": len(value["routeDecorations"]),
    }


def verify_authorization_frontend_projection(workspace: str | Path, projection: Any) -> dict[str, Any]:
    """只读验证资源常量与确认权限目录一致。"""

    if projection is None:
        return {"verified": False, "reason": "authorization_disabled"}
    value = _projection_value(projection)
    root = Path(workspace).expanduser().resolve()
    resources_path = root / RESOURCES_RELATIVE_PATH
    if not resources_path.is_file() or resources_path.is_symlink():
        raise AuthorizationFrontendProjectionError("authorization 模板缺少前端资源常量文件。")
    if resources_path.read_text(encoding="utf-8") != _render_resources(value["resources"]):
        raise AuthorizationFrontendProjectionError("前端 RESOURCES 与确认权限目录不一致。")
    return {"verified": True, "resourceCount": len(value["resources"]), "protectedPageCount": len(value["routeDecorations"])}


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


def _route_decorations(page_keys: dict[str, str], resources: list[dict[str, str]]) -> list[dict[str, str]]:
    """仅把已确认 PAGE 资源映射为共享 Route Projection 可消费的 decoration。"""

    resource_by_key = {item["resourceKey"]: item for item in resources}
    result: list[dict[str, str]] = []
    for page_id, resource_key in page_keys.items():
        reference = resource_by_key.get(resource_key)
        if not reference or reference["group"] != "PAGE":
            raise AuthorizationFrontendProjectionError(f"受控页面 {page_id} 缺少 PAGE 资源常量。")
        result.append({"pageId": page_id, "resourceKey": resource_key})
    return sorted(result, key=lambda item: item["pageId"])


def _projection_value(value: Any) -> dict[str, list[dict[str, str]]]:
    """验证持久化前端投影的最小结构。"""

    if not isinstance(value, dict):
        raise AuthorizationFrontendProjectionError("Build DAG 的 authorization_frontend_projection 必须是对象。")
    resources = _dict_items(value.get("resources"))
    decorations = _dict_items(value.get("routeDecorations"))
    if not resources:
        raise AuthorizationFrontendProjectionError("前端权限投影缺少完整资源目录。")
    return {"resources": resources, "routeDecorations": decorations}


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
