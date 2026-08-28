"""将已确认 DAG 的页面权限投影写入模板声明的共享路由托管区。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.services.application_template_generation import (
    ApplicationTemplateGenerationError,
    load_template_generation_manifest,
)


ROUTE_GUARD_DESCRIPTOR_RELATIVE_PATH = Path(
    "frontend/.xcodeagent/route-guard-projection.json"
)
ROUTE_GUARD_DESCRIPTOR_SCHEMA = "xcodeagent.route-guard-projection.v1"


class AuthorizationRouteProjectionError(ValueError):
    """表示模板没有声明或无法安全写入共享 RouteGuard 托管区。"""


def apply_authorization_route_projection(
    workspace: str | Path,
    projection: Any,
) -> dict[str, Any]:
    """在 Build 分发前幂等写入模板声明的业务页面权限映射。"""

    items = _projection_items(projection)
    if not items:
        return {"applied": False, "reason": "authorization_disabled_or_no_controlled_pages"}
    workspace_path = Path(workspace).expanduser().resolve()
    try:
        manifest = load_template_generation_manifest(workspace_path)
    except ApplicationTemplateGenerationError as exc:
        raise AuthorizationRouteProjectionError(str(exc)) from exc
    branch = _frontend_branch(manifest)
    if branch != "auth":
        raise AuthorizationRouteProjectionError(
            "权限路由投影存在，但前端模板不是 auth 分支。"
        )
    descriptor = _load_descriptor(workspace_path)
    target = _target_path(workspace_path, descriptor)
    start_marker = _required_text(descriptor, "startMarker")
    end_marker = _required_text(descriptor, "endMarker")
    content = target.read_text(encoding="utf-8")
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise AuthorizationRouteProjectionError("RouteGuard 托管文件缺少有效边界标记。")
    body_start = start + len(start_marker)
    generated = _render_projection(items)
    updated = content[:body_start] + "\n" + generated + content[end:]
    if updated != content:
        _write_text_atomically(target, updated)
    return {"applied": True, "path": str(target.relative_to(workspace_path)), "count": len(items)}


def verify_authorization_route_projection(
    workspace: str | Path,
    projection: Any,
) -> dict[str, Any]:
    """只读验证 RouteGuard 托管区与确认投影完全一致，不写入任何文件。"""

    items = _projection_items(projection)
    if not items:
        return {"verified": False, "reason": "authorization_disabled_or_no_controlled_pages"}
    workspace_path = Path(workspace).expanduser().resolve()
    try:
        manifest = load_template_generation_manifest(workspace_path)
    except ApplicationTemplateGenerationError as exc:
        raise AuthorizationRouteProjectionError(str(exc)) from exc
    if _frontend_branch(manifest) != "auth":
        raise AuthorizationRouteProjectionError("权限路由投影存在，但前端模板不是 auth 分支。")
    descriptor = _load_descriptor(workspace_path)
    target = _target_path(workspace_path, descriptor)
    content = target.read_text(encoding="utf-8")
    start_marker = _required_text(descriptor, "startMarker")
    end_marker = _required_text(descriptor, "endMarker")
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise AuthorizationRouteProjectionError("RouteGuard 托管文件缺少有效边界标记。")
    body_start = start + len(start_marker)
    expected = content[:body_start] + "\n" + _render_projection(items) + content[end:]
    if content != expected:
        raise AuthorizationRouteProjectionError("RouteGuard 托管区与确认投影不一致。")
    return {"verified": True, "path": str(target.relative_to(workspace_path)), "count": len(items)}


def _projection_items(value: Any) -> list[dict[str, str]]:
    """校验 DAG 中持久化的页面权限投影，拒绝模糊或重复映射。"""

    if value is None:
        return []
    if not isinstance(value, list):
        raise AuthorizationRouteProjectionError("Build DAG 的 authorization_route_projection 必须是数组。")
    result: list[dict[str, str]] = []
    page_ids: set[str] = set()
    routes: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise AuthorizationRouteProjectionError("RouteGuard 投影包含非法项。")
        page_id = _required_text(item, "pageId")
        route = _required_text(item, "route")
        resource_key = _required_text(item, "resourceKey")
        if not route.startswith("/") or page_id in page_ids or route in routes:
            raise AuthorizationRouteProjectionError("RouteGuard 投影存在非法或重复页面路由。")
        page_ids.add(page_id)
        routes.add(route)
        result.append({"pageId": page_id, "route": route, "resourceKey": resource_key})
    return sorted(result, key=lambda item: (item["route"], item["pageId"]))


def _load_descriptor(workspace: Path) -> dict[str, Any]:
    """读取 auth 模板提供的 RouteGuard 托管区声明。"""

    path = workspace / ROUTE_GUARD_DESCRIPTOR_RELATIVE_PATH
    if not path.is_file():
        raise AuthorizationRouteProjectionError(
            f"auth 模板缺少 RouteGuard 托管区声明：{path.relative_to(workspace)}。"
        )
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorizationRouteProjectionError("RouteGuard 托管区声明无法读取。") from exc
    if not isinstance(descriptor, dict) or descriptor.get("schemaVersion") != ROUTE_GUARD_DESCRIPTOR_SCHEMA:
        raise AuthorizationRouteProjectionError("RouteGuard 托管区声明 schema 无效。")
    return descriptor


def _target_path(workspace: Path, descriptor: dict[str, Any]) -> Path:
    """解析并约束模板声明的目标文件，禁止越过前端目录。"""

    relative = _required_text(descriptor, "targetPath")
    target = (workspace / "frontend" / relative).resolve()
    frontend_root = (workspace / "frontend").resolve()
    if frontend_root not in target.parents or not target.is_file():
        raise AuthorizationRouteProjectionError("RouteGuard 托管目标必须是前端目录内已有文件。")
    return target


def _frontend_branch(manifest: dict[str, Any]) -> str:
    """从模板 manifest 读取前端实际下载分支。"""

    steps = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
    download = steps.get("download") if isinstance(steps.get("download"), dict) else {}
    targets = download.get("targets") if isinstance(download.get("targets"), dict) else {}
    frontend = targets.get("frontend") if isinstance(targets.get("frontend"), dict) else {}
    return str(frontend.get("branch") or "").strip()


def _required_text(value: dict[str, Any], key: str) -> str:
    """读取必填字符串字段并提供统一错误。"""

    text = str(value.get(key) or "").strip()
    if not text:
        raise AuthorizationRouteProjectionError(f"RouteGuard 投影缺少 {key}。")
    return text


def _render_projection(items: list[dict[str, str]]) -> str:
    """渲染由模板 Router 消费的确定性 TypeScript 映射，不注入 Router 逻辑。"""

    lines = ["export const XCODEAGENT_ROUTE_GUARD_PROJECTION = ["]
    for item in items:
        lines.append("  {")
        lines.append(f"    pageId: {json.dumps(item['pageId'], ensure_ascii=False)},")
        lines.append(f"    route: {json.dumps(item['route'], ensure_ascii=False)},")
        lines.append(f"    resourceKey: {json.dumps(item['resourceKey'], ensure_ascii=False)},")
        lines.append("  },")
    lines.append("] as const;\n")
    return "\n".join(lines)


def _write_text_atomically(path: Path, content: str) -> None:
    """原子替换托管区文件，避免 Build 中断留下半截路由配置。"""

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
